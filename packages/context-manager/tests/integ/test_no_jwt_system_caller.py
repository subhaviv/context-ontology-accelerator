# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Live integ proof for serve's NO-JWT system-caller path (Option 3 door).

Context
-------
The serve runtime's AgentCore authorizer is a ``CustomJWTAuthorizer`` (see
``infra/lib/stacks/services/serve-stack.ts``), so every invoke through the
deployed front door MUST carry a valid user JWT. But ``coa_serve.identity`` and
``coa_serve.main`` deliberately implement a *no-JWT* fallback for "a future
internal caller that invokes the runtime without forwarding a Bearer token":
``resolve_principal`` reads ``profile.groups`` only when no JWT is present, and
``resolve_profile`` resolves that principal's grant from the RRM
``PrincipalIndex`` GSI.

That fallback is the mechanism a system caller (e.g. Beacon reading the governed
graph as a workload, not per-user) would use — but it has never been exercised
against LIVE infrastructure, because the JWT authorizer forecloses reaching it
through the deployed runtime. This module closes that gap in two layers:

* ``TestResolveProfileLive`` — calls the REAL ``resolve_profile`` against the
  REAL RRM table (no mocks), proving the grant-resolution half of the branch
  works on live DynamoDB. This is the half most likely to break on real infra
  (wrong table name, missing/throttled GSI, IAM gap) and it needs NO agent
  runtime, so it runs today. **Verified live 2026-10-21**: a group-only principal
  ``groups=["Admin"]`` resolved ``global_roles=["platform-admin"]`` against the
  live ``Group::Admin`` grant, and a user principal resolved its
  ``namespace-owner`` resource role — both with ``email=""`` (no JWT).

* ``TestNoJwtInvokeEndToEnd`` — the ACCEPTANCE GATE for the Option-3 agent
  runtime. It performs a real IAM-signed ``InvokeAgentRuntime`` with NO Bearer
  token against an IAM-authorized serve runtime, presenting
  ``profile={userId, groups}``, and asserts a governed answer comes back (not a
  401/403 from the authorizer, not an AccessDenied from a failed grant resolve).
  It SKIPS until ``COA_AGENT_RUNTIME_ARN`` is set — i.e. until the agent door
  exists — so it is written now and becomes the pass/fail test for that build.

Run
---
    export AWS_PROFILE=agentops-publisher
    export AWS_REGION=us-east-1
    export RRM_TABLE_NAME=coa-dev-resource-role-mappings
    # layer 1 (runs today, read-only):
    export COA_TEST_PRINCIPAL_GROUP=Admin                  # a group with a live grant
    # layer 2 (runs once the agent runtime exists):
    export COA_AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:...:runtime/...
    export COA_TEST_NAMESPACE=36521996-76e9-433f-96cb-d2ec2db4e568

    uv run pytest packages/context-manager/tests/integ/test_no_jwt_system_caller.py -m integ -v
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.integ

_RRM_TABLE = os.environ.get("RRM_TABLE_NAME", "")
_PRINCIPAL_GROUP = os.environ.get("COA_TEST_PRINCIPAL_GROUP", "")
_AGENT_RUNTIME_ARN = os.environ.get("COA_AGENT_RUNTIME_ARN", "")
_TEST_NAMESPACE = os.environ.get("COA_TEST_NAMESPACE", "")


@pytest.mark.skipif(
    not (_RRM_TABLE and _PRINCIPAL_GROUP),
    reason="requires RRM_TABLE_NAME + COA_TEST_PRINCIPAL_GROUP (a group with a live grant)",
)
class TestResolveProfileLive:
    """The grant-resolution half of the no-JWT branch, against live DynamoDB.

    Proves the exact code path ``main.invoke`` takes when NO JWT is present:
    ``resolve_profile(user_id, groups, namespace, email="")`` — with ``email=""``
    because ``resolve_principal`` never promotes an attacker-controlled
    ``profile.email`` to a grant-lookup key on the no-JWT path.
    """

    def test_group_principal_resolves_a_role_with_no_jwt(self):
        """A group-only principal (no user, no email, no JWT) resolves a role.

        This is the Beacon shape: a workload presenting ``groups=[<group>]`` and
        nothing else. A resolved role (global or resource) proves the RRM
        ``PrincipalIndex`` lookup works live under the no-JWT contract.
        """
        from coa_serve.role_resolver import resolve_profile

        resolved = resolve_profile(
            user_id="system-caller-probe",  # a principal with no User:: grant
            groups=[_PRINCIPAL_GROUP],
            namespace="",
            email="",  # the no-JWT invariant: never a lookup key here
        )

        resolved_a_role = bool(resolved.global_roles) or any(r.get("role") for r in resolved.resource_roles)
        assert resolved_a_role, (
            f"no-JWT group principal groups=[{_PRINCIPAL_GROUP!r}] resolved NO role "
            f"against {_RRM_TABLE} — the Group:: grant is missing or the "
            f"PrincipalIndex lookup failed. global_roles={resolved.global_roles} "
            f"resource_roles={resolved.resource_roles}"
        )

    def test_email_is_not_a_lookup_key_on_the_no_jwt_path(self):
        """Regression guard: a group grant must resolve WITHOUT any email.

        ``main.invoke`` calls ``resolve_profile(..., email="")`` on the no-JWT
        path precisely because ``profile.email`` is attacker-controlled there.
        This asserts the group grant still resolves with ``email=""`` — i.e. the
        resolution does not secretly depend on an email key.
        """
        from coa_serve.role_resolver import resolve_profile

        resolved = resolve_profile(
            user_id="system-caller-probe",
            groups=[_PRINCIPAL_GROUP],
            namespace="",
            email="",
        )
        assert bool(resolved.global_roles) or resolved.resource_roles, (
            "group grant must resolve with email='' — the no-JWT path never "
            "supplies an email, so resolution must not depend on one"
        )


@pytest.mark.skipif(
    not (_AGENT_RUNTIME_ARN and _TEST_NAMESPACE),
    reason="requires COA_AGENT_RUNTIME_ARN (the IAM-authorized agent runtime) + COA_TEST_NAMESPACE",
)
class TestNoJwtInvokeEndToEnd:
    """ACCEPTANCE GATE for the Option-3 agent runtime (3b).

    A real IAM-signed InvokeAgentRuntime with NO Bearer token against the
    IAM-authorized serve runtime, presenting a system ``profile``. Proves the
    WHOLE no-JWT path end-to-end: authorizer admits the IAM invoke, serve reads
    ``profile.groups``, resolves the grant, and returns a governed answer rather
    than a 401/403 or an AccessDenied-rendered empty.

    This cannot run until that runtime exists, so it SKIPS by default and is the
    pass/fail test to run immediately after the ``coa-dev-serve-agent`` deploy.
    """

    def test_system_caller_gets_a_governed_answer_without_a_jwt(self):
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            config=Config(read_timeout=200, retries={"max_attempts": 0}),
        )

        payload = {
            "query": "list the available metrics",
            "namespace": _TEST_NAMESPACE,
            # The system-caller profile: identity asserted by an already-trusted
            # workload, authenticated by the IAM invoke permission itself. NO JWT.
            "profile": {
                "userId": os.environ.get("COA_TEST_SYSTEM_USER", "beacon-agent"),
                "groups": [os.environ.get("COA_TEST_SYSTEM_GROUP", "beacon-agents")],
            },
        }

        resp = client.invoke_agent_runtime(
            agentRuntimeArn=_AGENT_RUNTIME_ARN,
            qualifier="DEFAULT",
            payload=json.dumps(payload).encode("utf-8"),
        )

        body = resp["response"].read()
        text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)

        # The authorizer must have ADMITTED the no-JWT IAM invoke (no 401/403),
        # and serve must NOT have denied on an unresolved grant.
        assert "401" not in text and "Unauthorized" not in text, (
            f"IAM invoke was rejected by the authorizer — the agent runtime is "
            f"not actually IAM-authorized (no-JWT door not open). body={text[:400]}"
        )
        assert '"statusCode": 403' not in text and "AccessDenied" not in text, (
            f"serve resolved NO role for the system profile — the "
            f"Group::<group> grant is missing or profile.groups was not read on "
            f"the no-JWT branch. body={text[:400]}"
        )
        # A governed response envelope (result/requestId), not an auth error.
        assert ("result" in text) or ("requestId" in text), f"no governed answer envelope returned. body={text[:400]}"
