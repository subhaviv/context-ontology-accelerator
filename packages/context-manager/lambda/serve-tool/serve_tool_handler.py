# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Gateway Lambda target in front of a Context Manager (serve) runtime.

Deployed by ``ServeToolStack`` as one function PER TOOL (``SERVE_TOOL_NAME``),
each fronting the IAM-authorized serve-agent runtime and SigV4-signing with its
own role. Every call runs as ONE fixed COA identity from this function's env
(``COA_AGENT_USER_ID`` / ``COA_AGENT_GROUPS``), never from tool arguments or the
gateway context. Admission (which agents may call the tool at all) is the calling
platform's job; serve-agent then authorizes that identity against the namespace
with its own grants.

The event holds only the tool's arguments. Payload keys mirror
``coa_mcp.tools.execution`` so serve sees the same contract from every caller.

Stdlib + botocore only (both ship in the Lambda Python runtime), so the asset
needs no pip install.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class ToolError(Exception):
    """A tool failure with an HTTP-like status, surfaced to the gateway caller."""

    def __init__(self, status: int, message: str) -> None:
        """Record ``status`` alongside the caller-visible ``message``."""
        super().__init__(message)
        self.status = status
        self.message = message


# ── Tool payload builders (same shapes as coa_mcp.tools.execution) ────────────


def _positive_int(value: Any) -> int | None:
    """Return ``value`` if it is a genuine positive int, else ``None`` (bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _require(event: dict, key: str) -> Any:
    value = event.get(key)
    if value in (None, ""):
        raise ToolError(400, f"missing required argument '{key}'")
    return value


def _build_query(event: dict, profile: dict) -> dict:
    options: dict[str, Any] = {"maxResults": min(int(event.get("maxResults") or 1000), 10000)}
    if event.get("execute") is False:
        options["execute"] = False
    for key in ("tierOverride", "mode", "strategy"):
        if event.get(key) is not None:
            options[key] = event[key]
    dims = event.get("dimensions")
    if isinstance(dims, list) and dims:
        options["dimensions"] = {
            d["name"]: d["value"] for d in dims if isinstance(d, dict) and "name" in d and "value" in d
        }
    if event.get("includeSupporting") is False:
        options["includeSupporting"] = False
    timeout_ms = _positive_int(event.get("timeoutMs"))
    if timeout_ms:
        options["timeoutMs"] = timeout_ms
    return {
        "query": _require(event, "query"),
        "namespace": profile["namespace"],
        "profile": profile,
        "options": options,
    }


def _build_translate(event: dict, profile: dict) -> dict:
    return {
        "action": "translate",
        "query": _require(event, "query"),
        "namespace": profile["namespace"],
        "profile": profile,
    }


def _build_rag(event: dict, profile: dict) -> dict:
    options: dict[str, Any] = {"topK": min(int(event.get("topK") or 10), 100)}
    if event.get("minScore") is not None:
        options["minScore"] = event["minScore"]
    for key in ("sourceFilter", "entityFilter"):
        if event.get(key):
            options[key] = event[key]
    return {
        "action": "kbSearch",
        "query": _require(event, "query"),
        "namespace": profile["namespace"],
        "profile": profile,
        "options": options,
    }


def _build_graph(event: dict, profile: dict) -> dict:
    direction = event.get("direction") or "both"
    if direction not in ("outgoing", "incoming", "both"):
        raise ToolError(400, f"invalid direction '{direction}'")
    options: dict[str, Any] = {
        "startUri": _require(event, "startUri"),
        "maxDepth": min(int(event.get("maxDepth") or 2), 5),
        "direction": direction,
        "maxResults": min(int(event.get("maxResults") or 100), 1000),
    }
    if event.get("relationshipFilter"):
        options["relationshipFilter"] = event["relationshipFilter"]
    return {
        "action": "graphTraverse",
        "namespace": profile["namespace"],
        "profile": profile,
        "options": options,
    }


_BUILDERS = {
    "query": _build_query,
    "translate_sparql": _build_translate,
    "rag_retrieval": _build_rag,
    "graph_traversal": _build_graph,
}


# ── Tool + identity ────────────────────────────────────────────────────────────


def _tool_name() -> str:
    """One function per tool, pinned by ``SERVE_TOOL_NAME``."""
    name = os.environ.get("SERVE_TOOL_NAME", "")
    if name not in _BUILDERS:
        raise ToolError(500, f"SERVE_TOOL_NAME misconfigured: '{name}'")
    return name


def _agent_profile(namespace: str) -> dict:
    """The one fixed COA identity agent calls run as, from this function's env."""
    user_id = os.environ.get("COA_AGENT_USER_ID", "").strip()
    groups = [g.strip() for g in os.environ.get("COA_AGENT_GROUPS", "").split(",") if g.strip()]
    if not user_id or not groups:
        raise ToolError(500, "agent identity misconfigured (COA_AGENT_USER_ID / COA_AGENT_GROUPS)")
    return {"userId": user_id, "groups": groups, "namespace": namespace}


# ── Invoke ─────────────────────────────────────────────────────────────────────


def _session_id(principal: str, namespace: str) -> str:
    digest = hashlib.sha256(f"{principal}|{namespace}".encode()).hexdigest()
    return f"coa-cm-{digest[:48]}"


def _invocation_url(runtime_arn: str, region: str) -> str:
    encoded = quote(runtime_arn, safe="")
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded}/invocations?qualifier=DEFAULT"


def _sigv4_headers(url: str, body: bytes, region: str, extra: dict) -> dict:
    import botocore.session
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    creds = botocore.session.get_session().get_credentials()
    if creds is None:
        raise RuntimeError("no AWS credentials available to sign the serve call")
    request = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json", **extra})
    SigV4Auth(creds, "bedrock-agentcore", region).add_auth(request)
    return dict(request.headers)


def _first_event(raw: str) -> dict:
    """Return the first JSON event of an SSE body, or the body itself if plain JSON."""
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ToolError(502, "serve returned an empty response")


def _invoke(payload: dict, *, session_principal: str) -> dict:
    region = os.environ.get("AWS_REGION", "us-east-1")
    url = _invocation_url(os.environ["SERVE_RUNTIME_ARN"], region)
    body = json.dumps(payload).encode()
    session = {_SESSION_HEADER: _session_id(session_principal, payload["namespace"])}
    headers = _sigv4_headers(url, body, region, session)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = int(os.environ.get("SERVE_INVOKE_TIMEOUT_S", "170"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https AgentCore URL)
            result = _first_event(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        logger.warning("serve invoke failed status=%s", exc.code)
        raise ToolError(exc.code, f"serve returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ToolError(504, f"serve invoke failed: {exc}") from exc
    status = result.get("statusCode")
    if isinstance(status, int) and status >= 400:
        raise ToolError(status, str(result.get("message") or result.get("error") or "serve error"))
    return result


# ── Entry point ────────────────────────────────────────────────────────────────


def handler(event: dict, context: Any) -> dict:
    """Gateway Lambda target entry point: dispatch one serve tool call."""
    try:
        tool = _tool_name()
        if not isinstance(event, dict):
            raise ToolError(400, "tool arguments must be an object")
        namespace = str(_require(event, "namespaceId"))
        profile = _agent_profile(namespace)
        payload = _BUILDERS[tool](event, profile)
        result = _invoke(payload, session_principal=profile["userId"])
        logger.info("tool=%s namespace=%s ok", tool, namespace)
        return result
    except ToolError as exc:
        logger.info("tool error status=%s", exc.status)
        return {"error": {"status": exc.status, "message": exc.message}}
