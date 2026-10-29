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
    monkeypatch.setenv("ID_TOKEN_HEADER", ID_HEADER)


@pytest.fixture
def invoke():
    with patch.object(h, "_invoke", return_value={"result": {"rows": []}, "requestId": "r1"}) as m:
        yield m


@pytest.fixture
def agent_env(monkeypatch):
    """Agent-mode function env: fixed identity; the test sets SERVE_TOOL_NAME."""
    monkeypatch.setenv("SERVE_TOOL_MODE", "agent")
    monkeypatch.setenv("COA_AGENT_USER_ID", "factory-agent")
    monkeypatch.setenv("COA_AGENT_GROUPS", "factory-agents")

    def _tool(name: str) -> None:
        monkeypatch.setenv("SERVE_TOOL_NAME", name)

    _tool("query")
    return _tool


FIXED_PROFILE = {"userId": "factory-agent", "groups": ["factory-agents"], "namespace": "ns1"}


@pytest.mark.unit
class TestAgentMode:
    def test_runs_as_fixed_identity_ignoring_arguments_and_gateway(self, agent_env, invoke):
        event = {"query": "q", "namespaceId": "ns1", "profile": {"userId": "root", "groups": ["Admin"]}}
        out = h.handler(event, _ctx(gateway="any-gateway"))
        assert out == {"result": {"rows": []}, "requestId": "r1"}
        payload = invoke.call_args.args[0]
        assert payload["profile"] == FIXED_PROFILE
        assert invoke.call_args.kwargs["bearer"] is None
        assert invoke.call_args.kwargs["session_principal"] == "factory-agent"

    def test_works_without_any_gateway_context(self, agent_env, invoke):
        assert "error" not in h.handler({"query": "q", "namespaceId": "ns1"}, None)

    def test_tool_comes_from_env_not_gateway_tool_name(self, agent_env, invoke):
        agent_env("translate_sparql")
        h.handler({"query": "q", "namespaceId": "ns1"}, _ctx(tool="t___graph_traversal"))
        assert invoke.call_args.args[0]["action"] == "translate"

    @pytest.mark.parametrize("name", ["", "describe_schema", "query "])
    def test_unknown_serve_tool_name_is_refused(self, agent_env, invoke, name):
        agent_env(name)
        out = h.handler({"query": "q", "namespaceId": "ns1"}, _ctx())
        assert out["error"]["status"] == 500
        invoke.assert_not_called()

    @pytest.mark.parametrize("var", ["COA_AGENT_USER_ID", "COA_AGENT_GROUPS"])
    def test_missing_identity_env_is_refused(self, agent_env, invoke, monkeypatch, var):
        monkeypatch.setenv(var, " ")
        assert h.handler({"query": "q", "namespaceId": "ns1"}, _ctx())["error"]["status"] == 500
        invoke.assert_not_called()

    def test_groups_env_is_comma_split(self, agent_env, invoke, monkeypatch):
        monkeypatch.setenv("COA_AGENT_GROUPS", "a, b,,")
        h.handler({"query": "q", "namespaceId": "ns1"}, None)
        assert invoke.call_args.args[0]["profile"]["groups"] == ["a", "b"]

    def test_no_gateway_map_code_remains(self):
        src = Path(h.__file__).read_text()
        assert "gateway_map" not in src and "GATEWAY_IDENTITY" not in src


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

    def test_tool_comes_from_gateway_tool_name(self, invoke):
        token = _jwt("u")
        h.handler(
            {"query": "q", "namespaceId": "ns1"},
            _ctx(tool="coa-rag___rag_retrieval", headers={ID_HEADER: token}),
        )
        assert invoke.call_args.args[0]["action"] == "kbSearch"

    def test_unknown_tool(self, invoke):
        out = h.handler({"namespaceId": "ns1"}, _ctx(tool="t___describe_schema", headers={ID_HEADER: _jwt("u")}))
        assert out["error"]["status"] == 400


@pytest.mark.unit
class TestToolDispatch:
    """Each of the 4 agent functions builds its own serve request."""

    def test_query_options(self, agent_env, invoke):
        event = {
            "query": "q",
            "namespaceId": "ns1",
            "execute": False,
            "maxResults": 50000,
            "dimensions": [{"name": "region", "value": "EU"}],
            "timeoutMs": True,
        }
        h.handler(event, None)
        payload = invoke.call_args.args[0]
        assert payload["query"] == "q" and payload["namespace"] == "ns1" and "action" not in payload
        assert payload["options"] == {"maxResults": 10000, "execute": False, "dimensions": {"region": "EU"}}

    def test_translate_sparql(self, agent_env, invoke):
        agent_env("translate_sparql")
        h.handler({"query": "q", "namespaceId": "ns1"}, None)
        assert invoke.call_args.args[0] == {
            "action": "translate",
            "query": "q",
            "namespace": "ns1",
            "profile": FIXED_PROFILE,
        }

    def test_rag_retrieval(self, agent_env, invoke):
        agent_env("rag_retrieval")
        h.handler({"query": "q", "namespaceId": "ns1", "topK": 500, "sourceFilter": ["d1"]}, None)
        payload = invoke.call_args.args[0]
        assert payload["action"] == "kbSearch"
        assert payload["options"] == {"topK": 100, "sourceFilter": ["d1"]}

    def test_graph_traversal(self, agent_env, invoke):
        agent_env("graph_traversal")
        h.handler({"startUri": "urn:x", "namespaceId": "ns1", "maxDepth": 9}, None)
        payload = invoke.call_args.args[0]
        assert payload["action"] == "graphTraverse"
        assert payload["options"] == {"startUri": "urn:x", "maxDepth": 5, "direction": "both", "maxResults": 100}

    def test_bad_direction(self, agent_env, invoke):
        agent_env("graph_traversal")
        out = h.handler({"startUri": "urn:x", "namespaceId": "ns1", "direction": "up"}, None)
        assert out["error"]["status"] == 400

    def test_missing_required_argument(self, agent_env, invoke):
        assert h.handler({"namespaceId": "ns1"}, None)["error"]["status"] == 400

    def test_missing_namespace(self, agent_env, invoke):
        assert h.handler({"query": "q"}, None)["error"]["status"] == 400

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
