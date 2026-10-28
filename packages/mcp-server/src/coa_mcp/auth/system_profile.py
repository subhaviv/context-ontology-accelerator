# SPDX-License-Identifier: Apache-2.0

"""System-caller identity for the agent-MCP wrapper (M2M, SigV4 path).

Machine callers (Beacon, the data-mapping agent, the enterprise governance
gateway, ...) each authenticate with their OWN Loom M2M client. Following the
Cognito M2M model, what a machine identity may do is carried in the access
token's custom SCOPES, granted to its client in the IdP. This module turns the
verified token's scopes into the system principal asserted to serve-agent, so
the IdP is the single source of truth: onboarding a new agent is granting its
client a scope, not editing or redeploying COA.

Two modes, chosen by config:

* **Scope-based** (``SCL_SYSTEM_SCOPE_PREFIX`` set, e.g. ``coa/group.``): the
  bearer token is verified here as well (signature, issuer, expiry; defense in
  depth behind the AgentCore authorizer's ``allowedScopes``), must be an access
  token, and every scope ``<prefix><group>`` becomes IdP group ``<group>``
  (mapped to ``Group::<group>`` in the RRM lookup). The userId is the token's
  ``client_id``, so each agent is distinguishable in audit. A token with no
  matching scope is refused: fail closed.
* **Legacy single profile** (prefix empty): every caller gets the one
  ``SCL_SYSTEM_PROFILE_USER_ID`` / ``SCL_SYSTEM_PROFILE_GROUPS`` profile,
  exactly as before. Kept so the wrapper can be deployed ahead of the IdP
  scope grants and switched over without breaking existing callers.

Groups always come from a verified token or server-side config, never from the
request body.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import structlog
from coa_common.auth import TokenAuthorizer, build_token_authorizer

from .claims import ClaimsExtractionError

logger = structlog.get_logger(__name__)


class SystemProfileError(ClaimsExtractionError):
    """Raised when a system caller's token does not grant a COA system identity.

    Subclasses ClaimsExtractionError so every tool's existing auth-error
    handling covers it unchanged.
    """


@dataclass(frozen=True)
class SystemProfile:
    """The system identity (userId + IdP groups) asserted for one M2M caller."""

    user_id: str
    groups: tuple[str, ...]


def groups_from_scopes(scope_claim: str | None, prefix: str) -> tuple[str, ...]:
    """Return the groups granted by ``<prefix><group>`` scopes, in token order."""
    groups: list[str] = []
    for scope in (scope_claim or "").split():
        if scope.startswith(prefix):
            group = scope[len(prefix) :].strip()
            if group and group not in groups:
                groups.append(group)
    return tuple(groups)


@lru_cache(maxsize=1)
def _get_m2m_authorizer() -> TokenAuthorizer:
    """Verifier for M2M access tokens: signature, issuer and expiry.

    No audience is pinned (an M2M access token has no user ``aud``); what the
    caller may do is decided by its scopes. Cleared in tests via
    ``_get_m2m_authorizer.cache_clear()``.
    """
    return build_token_authorizer(
        issuer=os.environ["JWT_ISSUER_URL"],
        region=os.environ["AWS_REGION"],
        client_id=None,
        jwks_uri=os.environ.get("JWT_JWKS_URI") or None,
    )


def resolve_system_profile(
    authorization_header: str | None,
    *,
    scope_prefix: str,
    legacy_user_id: str,
    legacy_groups: str,
) -> SystemProfile:
    """Return the system identity for a validated M2M caller."""
    if not scope_prefix.strip():
        groups = tuple(g.strip() for g in legacy_groups.split(",") if g.strip())
        return SystemProfile(user_id=legacy_user_id, groups=groups)

    if not authorization_header:
        raise SystemProfileError("Missing Authorization header")
    try:
        claims = _get_m2m_authorizer().verify_claims(authorization_header)
    except ValueError as e:
        logger.warning("system_caller_jwt_verification_failed", error=str(e))
        raise SystemProfileError(f"JWT verification failed: {e}") from e

    if claims.get("token_use") not in (None, "access"):
        raise SystemProfileError("System callers must present an access token")
    client_id = claims.get("client_id") or claims.get("sub")
    if not client_id:
        raise SystemProfileError("Access token lacks a client_id")

    groups = groups_from_scopes(claims.get("scope"), scope_prefix.strip())
    if not groups:
        logger.warning("system_caller_no_coa_scope", client_id=client_id)
        raise SystemProfileError(f"Client {client_id!r} holds no '{scope_prefix.strip()}*' scope for COA")
    logger.info("system_caller_resolved", client_id=client_id, groups=list(groups))
    return SystemProfile(user_id=client_id, groups=groups)
