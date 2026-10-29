# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the serve-tool gateway Lambda handler."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "lambda" / "serve-tool"))

import serve_tool_handler as h  # noqa: E402

ID_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-IdToken"


def _ctx(tool: str = "coa-query___query", gateway: str = "gw-beacon", headers: dict | None = None):
    custom: dict[str, object] = {"bedrockAgentCoreToolName": tool, "bedrockAgentCoreGatewayId": gateway}
    if headers is not None:
        custom["bedrockAgentCorePropagatedHeaders"] = headers
    return SimpleNamespace(client_context=SimpleNamespace(custom=custom))


def _jwt(sub: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SERVE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/serve-x")
    monkeypatch.setenv("GATEWAY_IDENTITY_PARAM", "/coa/serve-tool/gateway-identities")
    monkeypatch.setenv("ID_TOKEN_HEADER", ID_HEADER)
    h._gateway_map_cache.update(value=None, loaded_at=0.0)


@pytest.fixture
def gateway_map():
    with patch.object(
        h,
        "_load_gateway_map",
        return_value={"gw-beacon": {"userId": "beacon-agent", "groups": ["beacon-agents"]}},
    ):
        yield


@pytest.fixture
def invoke():
    with patch.object(h, "_invoke", return_value={"result": {"rows": []}, "requestId": "r1"}) as m:
        yield m


@pytest.mark.unit
class TestAgentMode:
    @pytest.fixture(autouse=True)
    def _mode(self, monkeypatch):
        monkeypatch.setenv("SERVE_TOOL_MODE", "agent")

    def test_identity_comes_from_gateway_map_not_arguments(self, gateway_map, invoke):
        event = {"query": "q", "namespaceId": "ns1", "profile": {"groups": ["Admin"]}}
        out = h.handler(event, _ctx())
        assert out == {"result": {"rows": []}, "requestId": "r1"}
        payload = invoke.call_args.args[0]
        assert payload["profile"] == {"userId": "beacon-agent", "groups": ["beacon-agents"], "namespace": "ns1"}
        assert invoke.call_args.kwargs["bearer"] is None

    def test_unmapped_gateway_is_refused(self, gateway_map, invoke):
        out = h.handler({"query": "q", "namespaceId": "ns1"}, _ctx(gateway="gw-other"))
        assert out["error"]["status"] == 403
        invoke.assert_not_called()

    def test_missing_gateway_context_is_refused(self, gateway_map, invoke):
        ctx = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "t___query"}))
        assert h.handler({"query": "q", "namespaceId": "ns1"}, ctx)["error"]["status"] == 403

    def test_entry_without_groups_is_refused(self, invoke):
        with patch.object(h, "_load_gateway_map", return_value={"gw-beacon": {"userId": "x", "groups": []}}):
            assert h.handler({"query": "q", "namespaceId": "ns1"}, _ctx())["error"]["status"] == 403


@pytest.mark.unit
class TestUserMode:
    @pytest.fixture(autouse=True)
    def _mode(self, monkeypatch):
        monkeypatch.setenv("SERVE_TOOL_MODE", "user")

    def test_forwards_propagated_token_and_asserts_no_groups(self, invoke):
        token = _jwt("user-1")
        out = h.handler({"query": "q", "namespaceId": "ns1"}, _ctx(headers={ID_HEADER.lower(): f"Bearer {token}"}))
        assert "error" not in out
        assert invoke.call_args.kwargs["bearer"] == token
        assert invoke.call_args.kwargs["session_principal"] == "user-1"
        assert invoke.call_args.args[0]["profile"] == {"userId": "", "groups": [], "namespace": "ns1"}

    def test_missing_token_is_401(self, invoke):
        assert h.handler({"query": "q", "namespaceId": "ns1"}, _ctx(headers={}))["error"]["status"] == 401
        invoke.assert_not_called()


@pytest.mark.unit
class TestToolDispatch:
    @pytest.fixture(autouse=True)
    def _mode(self, monkeypatch):
        monkeypatch.setenv("SERVE_TOOL_MODE", "agent")

    def test_query_options(self, gateway_map, invoke):
        event = {
            "query": "q",
            "namespaceId": "ns1",
            "execute": False,
            "maxResults": 50000,
            "dimensions": [{"name": "region", "value": "EU"}],
            "timeoutMs": True,
        }
        h.handler(event, _ctx())
        opts = invoke.call_args.args[0]["options"]
        assert opts == {"maxResults": 10000, "execute": False, "dimensions": {"region": "EU"}}

    def test_graph_traversal(self, gateway_map, invoke):
        h.handler({"startUri": "urn:x", "namespaceId": "ns1", "maxDepth": 9}, _ctx(tool="coa-graph___graph_traversal"))
        payload = invoke.call_args.args[0]
        assert payload["action"] == "graphTraverse"
        assert payload["options"]["maxDepth"] == 5

    def test_bad_direction(self, gateway_map, invoke):
        out = h.handler(
            {"startUri": "urn:x", "namespaceId": "ns1", "direction": "up"}, _ctx(tool="t___graph_traversal")
        )
        assert out["error"]["status"] == 400

    def test_unknown_tool(self, gateway_map, invoke):
        assert h.handler({"namespaceId": "ns1"}, _ctx(tool="t___describe_schema"))["error"]["status"] == 400

    def test_missing_namespace(self, gateway_map, invoke):
        assert h.handler({"query": "q"}, _ctx())["error"]["status"] == 400

    def test_bad_mode(self, monkeypatch, invoke):
        monkeypatch.setenv("SERVE_TOOL_MODE", "")
        assert h.handler({"query": "q", "namespaceId": "ns1"}, _ctx())["error"]["status"] == 500


@pytest.mark.unit
class TestInvoke:
    def test_sse_first_event(self):
        assert h._first_event('event: m\ndata: {"a": 1}\n\ndata: {"b": 2}\n') == {"a": 1}

    def test_plain_json(self):
        assert h._first_event('{"a": 1}') == {"a": 1}

    def test_serve_status_error_raises(self, monkeypatch):
        monkeypatch.setenv("SERVE_TOOL_MODE", "user")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'data: {"statusCode": 403, "message": "denied"}\n'

        with (
            patch.object(h.urllib.request, "urlopen", return_value=_Resp()) as urlopen,
            pytest.raises(h.ToolError) as exc,
        ):
            h._invoke({"namespace": "ns1"}, bearer="tok", session_principal="u")
        assert exc.value.status == 403
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tok"
        assert req.full_url.endswith("/invocations?qualifier=DEFAULT")

    def test_session_id_is_long_enough_and_scoped(self):
        a, b = h._session_id("u1", "ns"), h._session_id("u2", "ns")
        assert a != b and len(a) >= 33
