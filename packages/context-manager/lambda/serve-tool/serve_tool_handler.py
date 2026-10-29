# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Gateway Lambda target in front of a Context Manager (serve) runtime.

One handler, deployed twice by ``ServeToolStack``:

* ``SERVE_TOOL_MODE=agent`` fronts the IAM-authorized serve-agent runtime. The
  invoke is SigV4-signed with this function's own role. The COA identity sent in
  ``profile`` comes from a server-side map keyed on the calling gateway's id
  (``bedrockAgentCoreGatewayId``, set by the gateway service, never by the
  model). An unmapped gateway is refused: no default identity.
* ``SERVE_TOOL_MODE=user`` fronts the JWT-authorized human serve runtime. The
  caller's ID token arrives as a gateway-propagated header and is forwarded as
  ``Authorization: Bearer``. Serve's own authorizer and ``identity.py`` validate
  it and take groups from the token, so this function makes no identity decision.

Each gateway Lambda target carries one tool, so the tool is read from
``bedrockAgentCoreToolName`` (``<target>___<tool>``) and the event holds only
that tool's arguments. Payload keys mirror ``coa_mcp.tools.execution`` so serve
sees the same contract from every caller.

Stdlib + botocore only (both ship in the Lambda Python runtime), so the asset
needs no pip install.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
_TOOL_DELIMITER = "___"
_MAP_TTL_S = 300

_gateway_map_cache: dict[str, Any] = {"value": None, "loaded_at": 0.0}


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


# ── Gateway context ────────────────────────────────────────────────────────────


def _custom_context(context: Any) -> dict:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) if client_context else None
    return custom if isinstance(custom, dict) else {}


def _tool_name(custom: dict) -> str:
    raw = str(custom.get("bedrockAgentCoreToolName") or "")
    name = raw.split(_TOOL_DELIMITER)[-1] if raw else ""
    if name not in _BUILDERS:
        raise ToolError(400, f"unknown tool '{raw}'")
    return name


# ── Identity ───────────────────────────────────────────────────────────────────


def _load_gateway_map() -> dict:
    """Read the gateway-id → COA identity map from SSM, cached for ``_MAP_TTL_S``."""
    now = time.time()
    cached = _gateway_map_cache["value"]
    if cached is not None and now - _gateway_map_cache["loaded_at"] < _MAP_TTL_S:
        return cached
    import boto3

    param = os.environ["GATEWAY_IDENTITY_PARAM"]
    raw = boto3.client("ssm").get_parameter(Name=param)["Parameter"]["Value"]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("gateway identity map is not valid JSON (param=%s)", param)
        value = {}
    if not isinstance(value, dict):
        value = {}
    _gateway_map_cache.update(value=value, loaded_at=now)
    return value


def _agent_profile(custom: dict, namespace: str) -> dict:
    """Resolve the system identity for the calling gateway; refuse if unmapped."""
    gateway_id = str(custom.get("bedrockAgentCoreGatewayId") or "")
    if not gateway_id:
        raise ToolError(403, "no gateway id in invocation context")
    entry = _load_gateway_map().get(gateway_id)
    groups = entry.get("groups") if isinstance(entry, dict) else None
    user_id = entry.get("userId") if isinstance(entry, dict) else None
    if not user_id or not isinstance(groups, list) or not all(isinstance(g, str) and g for g in groups) or not groups:
        logger.warning("refused unmapped gateway %s", gateway_id)
        raise ToolError(403, "calling gateway is not mapped to a COA identity")
    return {"userId": user_id, "groups": groups, "namespace": namespace}


def _propagated_token(custom: dict) -> str:
    header = os.environ.get("ID_TOKEN_HEADER", "X-Amzn-Bedrock-AgentCore-Runtime-Custom-IdToken").lower()
    headers = custom.get("bedrockAgentCorePropagatedHeaders") or {}
    token = ""
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == header:
                token = str(value or "")
                break
    if token.lower().startswith("bearer "):
        token = token[7:]
    token = token.strip()
    if not token:
        raise ToolError(401, "missing caller ID token header")
    return token


def _token_subject(token: str) -> str:
    """Read ``sub`` WITHOUT verifying, only to scope the runtime session id.

    Serve's JWT authorizer verifies the token; an invalid token never reaches a
    session, so this value is never trusted for authorization.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("sub") or "")
    except (IndexError, ValueError):
        return ""


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


def _invoke(payload: dict, *, bearer: str | None, session_principal: str) -> dict:
    region = os.environ.get("AWS_REGION", "us-east-1")
    url = _invocation_url(os.environ["SERVE_RUNTIME_ARN"], region)
    body = json.dumps(payload).encode()
    session = {_SESSION_HEADER: _session_id(session_principal, payload["namespace"])}
    if bearer:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}", **session}
    else:
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
    mode = os.environ.get("SERVE_TOOL_MODE", "")
    try:
        if mode not in ("agent", "user"):
            raise ToolError(500, f"SERVE_TOOL_MODE misconfigured: '{mode}'")
        if not isinstance(event, dict):
            raise ToolError(400, "tool arguments must be an object")
        custom = _custom_context(context)
        tool = _tool_name(custom)
        namespace = str(_require(event, "namespaceId"))
        if mode == "agent":
            profile = _agent_profile(custom, namespace)
            bearer, principal = None, profile["userId"]
        else:
            bearer = _propagated_token(custom)
            # userId/groups left empty: serve takes both from the verified JWT.
            profile = {"userId": "", "groups": [], "namespace": namespace}
            principal = _token_subject(bearer)
        payload = _BUILDERS[tool](event, profile)
        result = _invoke(payload, bearer=bearer, session_principal=principal)
        logger.info("tool=%s mode=%s namespace=%s ok", tool, mode, namespace)
        return result
    except ToolError as exc:
        logger.info("tool error mode=%s status=%s", mode, exc.status)
        return {"error": {"status": exc.status, "message": exc.message}}
