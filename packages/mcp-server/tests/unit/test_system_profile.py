# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent-MCP wrapper's scope-based system-caller identity."""

from __future__ import annotations

import time

import jwt
import pytest
from coa_mcp.auth import system_profile
from coa_mcp.auth.claims import ClaimsExtractionError
from coa_mcp.auth.system_profile import (
    SystemProfileError,
    groups_from_scopes,
    resolve_system_profile,
)

from .conftest import _TEST_ISSUER, _TEST_KID, _jwk, _private_key

PREFIX = "coa/group."


def _m2m_token(
    client_id: str,
    scope: str,
    *,
    exp_offset: int = 3600,
    issuer: str = _TEST_ISSUER,
    token_use: str = "access",
) -> str:
    payload = {
        "sub": client_id,
        "client_id": client_id,
        "token_use": token_use,
        "scope": scope,
        "iss": issuer,
        "exp": int(time.time()) + exp_offset,
    }
    return jwt.encode(payload, _private_key, algorithm="RS256", headers={"kid": _TEST_KID})


@pytest.fixture(autouse=True)
def _mock_m2m_jwks(monkeypatch):
    system_profile._get_m2m_authorizer.cache_clear()
    authorizer = system_profile._get_m2m_authorizer()
    monkeypatch.setattr(authorizer, "_get_jwks", lambda: {"keys": [_jwk()]})
    yield
    system_profile._get_m2m_authorizer.cache_clear()


def _resolve(header, prefix=PREFIX):
    return resolve_system_profile(
        header, scope_prefix=prefix, legacy_user_id="beacon-agent", legacy_groups="beacon-agents"
    )


@pytest.mark.unit
class TestScopeBased:
    def test_scope_becomes_group_and_client_is_user(self):
        token = _m2m_token("beacon-client", "loom/mcp:read coa/group.beacon-agents")
        p = _resolve(f"Bearer {token}")
        assert (p.user_id, p.groups) == ("beacon-client", ("beacon-agents",))

    def test_each_agent_gets_only_its_own_groups(self):
        mapper = _resolve(f"Bearer {_m2m_token('mapper-client', 'coa/group.data-mapping-agents')}")
        assert (mapper.user_id, mapper.groups) == ("mapper-client", ("data-mapping-agents",))

    def test_multiple_coa_scopes_become_multiple_groups(self):
        token = _m2m_token("gw", "coa/group.a coa/group.b loom/mcp:read coa/group.a")
        assert _resolve(f"Bearer {token}").groups == ("a", "b")

    def test_token_without_coa_scope_is_refused(self):
        with pytest.raises(SystemProfileError, match="holds no 'coa/group.\\*' scope"):
            _resolve(f"Bearer {_m2m_token('beacon-client', 'loom/mcp:read')}")

    def test_refusal_is_a_claims_error_for_existing_handlers(self):
        with pytest.raises(ClaimsExtractionError):
            _resolve(f"Bearer {_m2m_token('x', 'loom/mcp:read')}")

    def test_missing_header_is_refused(self):
        with pytest.raises(SystemProfileError, match="Missing Authorization header"):
            _resolve(None)

    def test_expired_token_is_refused(self):
        token = _m2m_token("c", "coa/group.a", exp_offset=-100)
        with pytest.raises(SystemProfileError, match="JWT verification failed"):
            _resolve(f"Bearer {token}")

    def test_wrong_issuer_is_refused(self):
        token = _m2m_token("c", "coa/group.a", issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other")
        with pytest.raises(SystemProfileError, match="JWT verification failed"):
            _resolve(f"Bearer {token}")

    def test_unsigned_garbage_is_refused(self):
        with pytest.raises(SystemProfileError, match="JWT verification failed"):
            _resolve("Bearer not.a.jwt")

    def test_id_token_is_refused(self):
        token = _m2m_token("c", "coa/group.a", token_use="id")
        with pytest.raises(SystemProfileError, match="access token"):
            _resolve(f"Bearer {token}")


@pytest.mark.unit
class TestLegacySingleProfile:
    def test_empty_prefix_uses_legacy_profile_without_a_token(self):
        p = _resolve(None, prefix="")
        assert (p.user_id, p.groups) == ("beacon-agent", ("beacon-agents",))


@pytest.mark.unit
class TestGroupsFromScopes:
    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            (None, ()),
            ("", ()),
            ("loom/mcp:read", ()),
            ("coa/group.", ()),
            ("coa/group.x", ("x",)),
            ("coa/groupx.y coa/group.z", ("z",)),
        ],
    )
    def test_parsing(self, claim, expected):
        assert groups_from_scopes(claim, PREFIX) == expected
