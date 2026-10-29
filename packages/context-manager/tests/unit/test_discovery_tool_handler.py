# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the discovery-tool (list_metrics / describe_schema) Lambda handler."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "lambda" / "discovery-tool"))

import discovery_tool_handler as h  # noqa: E402

NS = "ns-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("DISCOVERY_TOOL_NAME", "list_metrics")
    monkeypatch.setenv("BACKEND_LAMBDA_ARN", "arn:aws:lambda:us-east-1:123456789012:function:metric-api")
    monkeypatch.setenv("COA_AGENT_USER_ID", "factory-agent")
    monkeypatch.setenv("COA_AGENT_GROUPS", "factory-agents")
    monkeypatch.setenv("ROLES_TABLE_NAME", "roles")
    h._lambda_client = None


def _resolved(resource_roles=(), global_roles=()):
    return SimpleNamespace(resource_roles=list(resource_roles), global_roles=list(global_roles))


GRANTED = _resolved([{"role": "data-analyst", "resourceUID": NS}])


@pytest.fixture
def authz():
    """Patch the shared resolver + Cedar; yields the mocks for per-test tuning."""
    with (
        patch("coa_serve.role_resolver.resolve_profile", return_value=GRANTED) as resolve,
        patch("coa_authorization.policy_loader.load_policies_for_roles", return_value="permit(...);") as load,
        patch("coa_authorization.policy_evaluator.evaluate", return_value=SimpleNamespace(allowed=True)) as cedar,
    ):
        yield SimpleNamespace(resolve=resolve, load=load, cedar=cedar)


@pytest.fixture
def backend():
    client = MagicMock()

    def _reply(status=200, body=None, function_error=False):
        resp = {"Payload": io.BytesIO(json.dumps({"statusCode": status, "body": json.dumps(body or {})}).encode())}
        if function_error:
            resp["FunctionError"] = "Unhandled"
        client.invoke.return_value = resp

    _reply(body={"metrics": [{"name": "m1"}]})
    client.reply = _reply
    h._lambda_client = client
    return client


def _sent(client) -> dict:
    return json.loads(client.invoke.call_args.kwargs["Payload"])


@pytest.mark.unit
class TestAuthorization:
    def test_runs_as_fixed_identity_and_checks_view_namespace(self, authz, backend):
        out = h.handler({"namespaceId": NS, "profile": {"userId": "root"}}, None)
        assert out == {"metrics": [{"name": "m1"}]}
        authz.resolve.assert_called_once_with("factory-agent", ["factory-agents"], namespace=NS)
        assert authz.cedar.call_args.kwargs["action"] == "viewNamespace"
        assert authz.cedar.call_args.kwargs["resource_id"] == NS
        assert authz.load.call_args.kwargs["role_ids"] == {"default", "data-analyst"}

    def test_no_grant_on_this_namespace_is_refused_before_backend(self, authz, backend):
        authz.resolve.return_value = _resolved([{"role": "data-analyst", "resourceUID": "other-ns"}])
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 403
        backend.invoke.assert_not_called()
        authz.cedar.assert_not_called()

    def test_global_role_passes_the_namespace_check(self, authz, backend):
        authz.resolve.return_value = _resolved(global_roles=["platform-viewer"])
        assert "error" not in h.handler({"namespaceId": NS}, None)

    def test_cedar_deny_is_refused(self, authz, backend):
        authz.cedar.return_value = SimpleNamespace(allowed=False)
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 403
        backend.invoke.assert_not_called()

    def test_no_policies_is_refused(self, authz, backend):
        authz.load.return_value = ""
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 403
        backend.invoke.assert_not_called()

    def test_grant_lookup_failure_is_502_not_allowed(self, authz, backend):
        authz.resolve.side_effect = RuntimeError("throttled")
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 502
        backend.invoke.assert_not_called()

    @pytest.mark.parametrize("var", ["COA_AGENT_USER_ID", "COA_AGENT_GROUPS"])
    def test_missing_identity_env(self, authz, backend, monkeypatch, var):
        monkeypatch.setenv(var, "")
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 500


@pytest.mark.unit
class TestRequests:
    def test_list_metrics_event(self, authz, backend):
        h.handler({"namespaceId": NS, "maxResults": 5000, "nextToken": "t1"}, None)
        sent = _sent(backend)
        assert backend.invoke.call_args.kwargs["FunctionName"].endswith(":metric-api")
        assert sent["resource"] == "/namespaces/{namespaceId}/metrics"
        assert sent["path"] == f"/namespaces/{NS}/metrics"
        assert sent["queryStringParameters"] == {"maxResults": "1000", "nextToken": "t1"}
        assert sent["requestContext"]["authorizer"] == {
            "principalId": "factory-agent",
            "userId": "factory-agent",
            "sub": "factory-agent",
            "email": "factory-agent",
            "groups": "factory-agents",
            "globalRoles": "",
        }

    def test_describe_schema_event_and_projection(self, authz, backend, monkeypatch):
        monkeypatch.setenv("DISCOVERY_TOOL_NAME", "describe_schema")
        backend.reply(body={"classes": [{"uri": "c"}], "ontologyVersion": "v3", "namespace": NS})
        out = h.handler({"namespaceId": NS, "includeProperties": False, "classFilter": "urn:x"}, None)
        assert out == {"classes": [{"uri": "c"}], "ontologyVersion": "v3"}
        sent = _sent(backend)
        assert sent["path"] == f"/namespaces/{NS}/schema"
        assert sent["queryStringParameters"] == {
            "maxResults": "100",
            "includeProperties": "false",
            "classFilter": "urn:x",
        }

    def test_backend_error_status_is_surfaced(self, authz, backend):
        backend.reply(status=404, body={"message": "namespace not found"})
        assert h.handler({"namespaceId": NS}, None)["error"] == {"status": 404, "message": "namespace not found"}

    def test_backend_function_error_is_502(self, authz, backend):
        backend.reply(function_error=True)
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 502

    @pytest.mark.parametrize("name", ["", "query", "list_metrics "])
    def test_unknown_tool_name(self, authz, backend, monkeypatch, name):
        monkeypatch.setenv("DISCOVERY_TOOL_NAME", name)
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 500
        backend.invoke.assert_not_called()

    def test_missing_namespace(self, authz, backend):
        assert h.handler({}, None)["error"]["status"] == 400
        authz.resolve.assert_not_called()


@pytest.mark.unit
class TestGetNamespace:
    ITEM = {
        "PK": f"NS#{NS}",
        "SK": "METADATA",
        "namespaceId": NS,
        "name": "pharma",
        "displayName": "Pharma",
        "description": "Supply chain",
        "status": "ACTIVE",
        "language": "en",
        "owner": "someone@example.com",
        "athenaWorkgroupName": "wg",
        "dataZoneProjectId": "dz",
    }

    @pytest.fixture
    def table(self, monkeypatch):
        monkeypatch.setenv("DISCOVERY_TOOL_NAME", "get_namespace")
        monkeypatch.setenv("NAMESPACES_TABLE", "coa-dev-namespaces")
        t = MagicMock()
        t.get_item.return_value = {"Item": dict(self.ITEM)}
        h._ns_table = t
        yield t
        h._ns_table = None

    def test_returns_profile_fields_only(self, authz, table):
        out = h.handler({"namespaceId": NS}, None)
        assert out == {
            "namespace": {
                "namespaceId": NS,
                "name": "pharma",
                "displayName": "Pharma",
                "description": "Supply chain",
                "status": "ACTIVE",
                "language": "en",
            }
        }
        table.get_item.assert_called_once_with(Key={"PK": f"NS#{NS}", "SK": "METADATA"})
        assert authz.cedar.call_args.kwargs["action"] == "viewNamespace"

    def test_legacy_item_without_language_reads_as_default(self, authz, table):
        item = dict(self.ITEM)
        del item["language"]
        table.get_item.return_value = {"Item": item}
        assert h.handler({"namespaceId": NS}, None)["namespace"]["language"] == "en"

    def test_no_grant_is_refused_before_reading(self, authz, table):
        authz.resolve.return_value = _resolved([])
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 403
        table.get_item.assert_not_called()

    def test_missing_namespace_item_is_404(self, authz, table):
        table.get_item.return_value = {}
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 404

    def test_read_failure_is_502(self, authz, table):
        table.get_item.side_effect = RuntimeError("throttled")
        assert h.handler({"namespaceId": NS}, None)["error"]["status"] == 502
