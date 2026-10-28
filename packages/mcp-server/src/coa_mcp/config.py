# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP Server configuration — extends shared SCLConfig."""

from coa_common.config import resolve_region
from pydantic import Field
from pydantic_settings import BaseSettings


class MCPConfig(BaseSettings):
    """Configuration for the MCP Server.

    Values loaded from environment variables with ``SCL_`` prefix.
    Pydantic BaseSettings reads env vars automatically — for example,
    ``aws_region`` is resolved from the runtime environment only when the
    ``SCL_AWS_REGION`` environment variable is not set. When the env var is
    present, its value takes precedence over the default.
    """

    model_config = {"env_prefix": "SCL_"}

    # General
    environment: str = "dev"
    log_level: str = "INFO"

    # AWS — SCL_AWS_REGION wins when set; otherwise resolve from the runtime
    # environment (AWS_REGION, then AWS_DEFAULT_REGION, then us-east-1).
    # Nothing in infra/ sets SCL_AWS_REGION — the MCP stack sets AWS_REGION —
    # so a hardcoded default here pinned every deployment's downstream calls
    # (Lambda invokes, Context Manager endpoint) to us-east-1 and broke every
    # MCP tool in any other region (#92). default_factory keeps the pydantic
    # env lookup precedence intact while making the fallback region-aware.
    aws_region: str = Field(default_factory=resolve_region)

    # Neptune (for discovery tools)
    neptune_endpoint: str = ""
    neptune_port: int = 8182

    # Fuseki local dev mode (bypasses SigV4, uses plain HTTP)
    use_fuseki: bool = False
    fuseki_url: str = "http://localhost:3030/coa/query"

    # MCP Server
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # Auth
    auth_enabled: bool = True

    # ── System-caller mode (agent-MCP wrapper) ────────────────────────
    # When cm_sigv4 is true, execution tools SIGN the Context Manager invoke
    # with AWS SigV4 (the wrapper's own runtime role) INSTEAD of forwarding the
    # caller's bearer token. This is the agent-MCP wrapper path: the caller
    # authenticates to THIS server with a Loom M2M token (validated by the
    # AgentCore authorizer's allowedClients), and the server re-authenticates to
    # the IAM-authorized serve-agent runtime as a system via SigV4. The caller's
    # token is NOT forwarded (the serve-agent runtime has no JWT authorizer).
    #
    # Nothing sets SCL_CM_SIGV4 on the human MCP stack, so it defaults false and
    # the human path keeps forwarding the user JWT unchanged.
    cm_sigv4: bool = False

    # The system identity this wrapper asserts to serve-agent on the SigV4 path.
    # Derived from the VERIFIED M2M token's client (set by the agent-mcp stack
    # as SCL_SYSTEM_PROFILE_*), NOT from the request body — so groups come from
    # a cryptographically-validated identity, not a caller-supplied value.
    # Only consulted when cm_sigv4 is true.
    system_profile_user_id: str = "beacon-agent"
    system_profile_groups: str = ""  # comma-separated; e.g. "beacon-agents"

    # Scope-based system identity, set by the agent-mcp stack as
    # SCL_SYSTEM_SCOPE_PREFIX (e.g. "coa/group."). When set, the wrapper
    # VERIFIES the caller's M2M access token and derives its COA groups from
    # the token's "<prefix><group>" scopes, granted to the client in the IdP;
    # a token with no such scope is refused (fail closed). When empty, the
    # single legacy profile above is used for every caller.
    system_scope_prefix: str = ""
