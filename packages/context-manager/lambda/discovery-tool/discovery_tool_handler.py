# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agent Lambda tools for COA discovery: list_metrics, describe_schema, get_namespace.

Deployed by ``ServeToolStack`` as one function PER TOOL (``DISCOVERY_TOOL_NAME``),
each with its own ``PROD`` alias, alongside the serve tools.

Unlike the serve tools, these do not go through serve-agent. ``list_metrics`` and
``describe_schema`` invoke the metric-service / ontology-proxy Lambda directly with
a synthetic API Gateway event, like the MCP server. ``get_namespace`` reads the
namespace's metadata item from the namespaces table (the same item
``coa_control_plane.namespace.get_handler`` serves) and returns only its profile
fields; it does not invoke the control-plane namespace Lambda, which also serves
PUT/DELETE behind API Gateway's authorizer. None of those backends authorize the
caller themselves, so this function runs the same namespace gate the MCP server
runs before every discovery call (``coa_mcp.auth.grant_resolver``):

1. resolve the fixed agent identity's roles from the ResourceRoleMappings table
   (``coa_serve.role_resolver.resolve_profile``, the shared resolver);
2. fail closed unless it holds a role on THIS namespace (or a GLOBAL role);
3. evaluate COA's Cedar ``viewNamespace`` policy for those roles.

Only then is the backend called. Every call runs as ONE fixed COA identity from
this function's env (``COA_AGENT_USER_ID`` / ``COA_AGENT_GROUPS``), never from
tool arguments or the gateway context.

Request shapes mirror ``coa_mcp.tools.discovery`` so the backends see the same
contract from every caller.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import uuid4

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Cedar action per tool -- same mapping as coa_mcp.auth.grant_resolver.TOOL_CEDAR_ACTION.
_CEDAR_ACTION = {"list_metrics": "viewNamespace", "describe_schema": "viewNamespace", "get_namespace": "viewNamespace"}
_RESOURCE = {
    "list_metrics": "/namespaces/{namespaceId}/metrics",
    "describe_schema": "/namespaces/{namespaceId}/schema",
}
# get_namespace returns the namespace's curated profile only -- not owner,
# DataZone/Athena wiring, source counts or the datasource ExternalId.
_NAMESPACE_FIELDS = ("namespaceId", "name", "displayName", "description", "status")

_lambda_client: Any = None
_ns_table: Any = None


class ToolError(Exception):
    """A tool failure with an HTTP-like status, surfaced to the gateway caller."""

    def __init__(self, status: int, message: str) -> None:
        """Record ``status`` alongside the caller-visible ``message``."""
        super().__init__(message)
        self.status = status
        self.message = message


# ── Config ─────────────────────────────────────────────────────────────────────


def _tool_name() -> str:
    name = os.environ.get("DISCOVERY_TOOL_NAME", "")
    if name not in _CEDAR_ACTION:
        raise ToolError(500, f"DISCOVERY_TOOL_NAME misconfigured: '{name}'")
    return name


def _identity() -> tuple[str, list[str]]:
    user_id = os.environ.get("COA_AGENT_USER_ID", "").strip()
    groups = [g.strip() for g in os.environ.get("COA_AGENT_GROUPS", "").split(",") if g.strip()]
    if not user_id or not groups:
        raise ToolError(500, "agent identity misconfigured (COA_AGENT_USER_ID / COA_AGENT_GROUPS)")
    return user_id, groups


# ── Authorization (same gate as the MCP server's grant resolver) ───────────────


def _authorize(user_id: str, groups: list[str], namespace: str, action: str) -> tuple[list[str], list[dict]]:
    """Return ``(global_roles, resource_roles)`` if allowed; raise ``ToolError`` otherwise."""
    from coa_serve.role_resolver import resolve_profile

    try:
        resolved = resolve_profile(user_id, groups, namespace=namespace)
    except Exception:
        # A failed grant read means access is unknown, not denied: retryable 502,
        # never a fall-through to the backend.
        logger.exception("grant lookup failed namespace=%s", namespace)
        raise ToolError(502, "unable to verify access") from None

    on_namespace = [r for r in resolved.resource_roles if r.get("resourceUID") == namespace]
    if not on_namespace and not resolved.global_roles:
        logger.warning("no grant user=%s namespace=%s", user_id, namespace)
        raise ToolError(403, "Access denied")

    from coa_authorization.policy_evaluator import evaluate
    from coa_authorization.policy_loader import load_policies_for_roles

    role_ids = {"default", *resolved.global_roles, *(r["role"] for r in resolved.resource_roles if r.get("role"))}
    policies = load_policies_for_roles(
        role_ids=role_ids,
        table_name=os.environ["ROLES_TABLE_NAME"],
        region=os.environ.get("AWS_REGION", "us-east-1"),
    )
    if not policies:
        logger.warning("no cedar policies roles=%s", sorted(role_ids))
        raise ToolError(403, "Access denied")
    decision = evaluate(
        policies=policies,
        user_id=user_id,
        action=action,
        resource_type="Namespace",
        resource_id=namespace,
        global_roles=resolved.global_roles,
        resource_roles=resolved.resource_roles,
        resource_attrs={"namespace": namespace},
    )
    if not decision.allowed:
        logger.warning("cedar denied user=%s action=%s namespace=%s", user_id, action, namespace)
        raise ToolError(403, "Access denied")
    return resolved.global_roles, resolved.resource_roles


# ── Backend request (same shapes as coa_mcp.tools.discovery) ───────────────────


def _bounded_int(value: Any, default: int, cap: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return min(value, cap)


def _query_params(tool: str, event: dict) -> dict[str, str]:
    if tool == "list_metrics":
        params = {"maxResults": str(_bounded_int(event.get("maxResults"), 100, 1000))}
        if event.get("nextToken"):
            params["nextToken"] = str(event["nextToken"])
        return params
    include = event.get("includeProperties")
    params = {
        "maxResults": str(_bounded_int(event.get("maxResults"), 100, 500)),
        "includeProperties": "false" if include is False else "true",
    }
    if event.get("classFilter"):
        params["classFilter"] = str(event["classFilter"])
    return params


def _api_event(tool: str, namespace: str, query: dict, user_id: str, groups: list[str], global_roles: list[str]):
    resource = _RESOURCE[tool]
    return {
        "httpMethod": "GET",
        "resource": resource,
        "path": resource.replace("{namespaceId}", namespace),
        "pathParameters": {"namespaceId": namespace},
        "queryStringParameters": query,
        # No user token on the system path (same as the MCP server's agent wrapper).
        "headers": {"Authorization": "Bearer ", "Content-Type": "application/json"},
        "body": None,
        "isBase64Encoded": False,
        "requestContext": {
            "stage": "tool",
            "requestId": str(uuid4()),
            # Mirrors coa_mcp.server._build_authorizer_context.
            "authorizer": {
                "principalId": user_id,
                "userId": user_id,
                "sub": user_id,
                "email": user_id,
                "groups": ",".join(groups),
                "globalRoles": ",".join(global_roles),
            },
        },
    }


def _invoke_backend(event: dict) -> dict:
    global _lambda_client
    if _lambda_client is None:
        import boto3
        from botocore.config import Config

        _lambda_client = boto3.client(
            "lambda", config=Config(read_timeout=30, connect_timeout=5, retries={"max_attempts": 1})
        )
    response = _lambda_client.invoke(
        FunctionName=os.environ["BACKEND_LAMBDA_ARN"],
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode(),
    )
    raw = response["Payload"].read().decode()
    if "FunctionError" in response:
        logger.error("backend function error: %s", raw[:500])
        raise ToolError(502, "backend failed")
    proxy = json.loads(raw)
    status = proxy.get("statusCode", 200)
    body = proxy.get("body") or "{}"
    parsed = json.loads(body) if isinstance(body, str) else body
    if isinstance(status, int) and status >= 400:
        message = parsed.get("message") if isinstance(parsed, dict) else None
        raise ToolError(status, str(message or f"backend returned {status}"))
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _read_namespace(namespace: str) -> dict:
    """The namespace's profile, from the same item the control-plane GET serves."""
    global _ns_table
    if _ns_table is None:
        import boto3

        _ns_table = boto3.resource("dynamodb").Table(os.environ["NAMESPACES_TABLE"])
    try:
        item = _ns_table.get_item(Key={"PK": f"NS#{namespace}", "SK": "METADATA"}).get("Item")
    except Exception:
        logger.exception("namespace read failed namespace=%s", namespace)
        raise ToolError(502, "unable to read namespace") from None
    if not item:
        raise ToolError(404, f"Namespace not found: {namespace}")
    from coa_common.language_tags import effective_namespace_language

    profile = {k: item[k] for k in _NAMESPACE_FIELDS if item.get(k) is not None}
    profile["language"] = effective_namespace_language(item)
    return {"namespace": profile}


# ── Entry point ────────────────────────────────────────────────────────────────


def handler(event: dict, context: Any) -> dict:
    """Gateway Lambda target entry point: run one discovery tool call."""
    try:
        tool = _tool_name()
        if not isinstance(event, dict):
            raise ToolError(400, "tool arguments must be an object")
        namespace = event.get("namespaceId")
        if not isinstance(namespace, str) or not namespace:
            raise ToolError(400, "missing required argument 'namespaceId'")
        user_id, groups = _identity()
        global_roles, _ = _authorize(user_id, groups, namespace, _CEDAR_ACTION[tool])
        if tool == "get_namespace":
            logger.info("tool=%s namespace=%s ok", tool, namespace)
            return _read_namespace(namespace)
        result = _invoke_backend(_api_event(tool, namespace, _query_params(tool, event), user_id, groups, global_roles))
        if tool == "describe_schema":
            # Project to the Smithy DescribeSchema shape, as the MCP server does.
            result = {"classes": result.get("classes", []), "ontologyVersion": result.get("ontologyVersion")}
        logger.info("tool=%s namespace=%s ok", tool, namespace)
        return result
    except ToolError as exc:
        logger.info("tool error status=%s", exc.status)
        return {"error": {"status": exc.status, "message": exc.message}}
