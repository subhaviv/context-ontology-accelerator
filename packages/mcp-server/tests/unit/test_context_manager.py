# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Context Manager AgentCore HTTP client.

The client POSTs a payload to the AgentCore invocations endpoint via httpx-sse
and forwards the caller's JWT. These tests mock httpx_sse.aconnect_sse so no
real network calls happen, verifying URL/header construction and error mapping.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from coa_mcp.clients.context_manager import (
    _SESSION_HEADER,
    ContextManagerClient,
    ContextManagerError,
    runtime_session_id,
)


def _make_client() -> ContextManagerClient:
    """Build a client with a real httpx.AsyncClient (mocked at the SSE layer)."""
    client = ContextManagerClient(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/cm-abc",
        region="us-east-1",
    )
    return client


def _mock_sse_response(status_code: int = 200, events: list[dict] | None = None, body: bytes = b""):
    """Create a mock for aconnect_sse that yields SSE events.

    Args:
        status_code: HTTP status code on the response.
        events: List of dicts to return as SSE event JSON payloads.
        body: Raw body bytes for error responses.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.aread = AsyncMock(return_value=body)

    mock_event_source = MagicMock()
    mock_event_source.response = mock_response

    async def _aiter_sse():
        for event_data in events or []:
            sse_event = MagicMock()
            sse_event.json.return_value = event_data
            yield sse_event

    mock_event_source.aiter_sse = _aiter_sse

    @asynccontextmanager
    async def _aconnect_sse(*args, **kwargs):
        # Store call args for assertion
        _aconnect_sse.last_call_args = args
        _aconnect_sse.last_call_kwargs = kwargs
        yield mock_event_source

    return _aconnect_sse


@pytest.mark.unit
class TestContextManagerClientInit:
    def test_url_includes_encoded_arn_and_region(self):
        client = ContextManagerClient(
            runtime_arn="arn:aws:bedrock-agentcore:eu-west-1:1:runtime/cm",
            region="eu-west-1",
        )
        assert "bedrock-agentcore.eu-west-1.amazonaws.com" in client._url
        # The ARN colons/slashes must be percent-encoded into the path segment.
        assert "arn%3Aaws%3Abedrock-agentcore" in client._url
        assert client._url.endswith("/invocations?qualifier=DEFAULT")


@pytest.mark.unit
class TestContextManagerInvoke:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_success(self):
        client = _make_client()
        mock_sse = _mock_sse_response(200, events=[{"result": {"tier": 1}}])

        with patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse):
            result = await client.invoke({"query": "hi"}, "my-jwt")

        assert result == {"result": {"tier": 1}}

    @pytest.mark.asyncio
    async def test_forwards_bearer_token_and_payload(self):
        client = _make_client()
        mock_sse = _mock_sse_response(200, events=[{}])

        with patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse):
            await client.invoke({"query": "revenue"}, "jwt-123")

        kwargs = mock_sse.last_call_kwargs
        assert kwargs["json"] == {"query": "revenue"}
        assert kwargs["headers"]["Authorization"] == "Bearer jwt-123"
        assert kwargs["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_http_400_raises_context_manager_error(self):
        client = _make_client()
        mock_sse = _mock_sse_response(403, body=b"Forbidden")

        with (
            patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse),
            pytest.raises(ContextManagerError, match="returned 403"),
        ):
            await client.invoke({}, "tok")

    @pytest.mark.asyncio
    async def test_timeout_raises_context_manager_error(self):
        client = _make_client()

        @asynccontextmanager
        async def _raise_timeout(*args, **kwargs):
            raise httpx.TimeoutException("slow")
            yield  # noqa: RUF027 — unreachable but needed for generator syntax

        with (
            patch("coa_mcp.clients.context_manager.aconnect_sse", _raise_timeout),
            pytest.raises(ContextManagerError, match="timed out"),
        ):
            await client.invoke({}, "tok")

    @pytest.mark.asyncio
    async def test_http_error_raises_context_manager_error(self):
        client = _make_client()

        @asynccontextmanager
        async def _raise_connect(*args, **kwargs):
            raise httpx.ConnectError("no route")
            yield  # noqa: RUF027 — unreachable but needed for generator syntax

        with (
            patch("coa_mcp.clients.context_manager.aconnect_sse", _raise_connect),
            pytest.raises(ContextManagerError, match="HTTP error"),
        ):
            await client.invoke({}, "tok")

    @pytest.mark.asyncio
    async def test_empty_stream_raises_context_manager_error(self):
        client = _make_client()
        mock_sse = _mock_sse_response(200, events=[])

        with (
            patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse),
            pytest.raises(ContextManagerError, match="empty SSE stream"),
        ):
            await client.invoke({}, "tok")

    @pytest.mark.asyncio
    async def test_close_delegates_to_httpx_aclose(self):
        client = _make_client()
        client._client = MagicMock()
        client._client.aclose = AsyncMock()
        await client.close()
        client._client.aclose.assert_awaited_once()


def _payload(user: str, ns: str) -> dict:
    return {"query": "q", "namespace": ns, "profile": {"userId": user, "groups": [], "namespace": ns}}


@pytest.mark.unit
class TestRuntimeSessionAffinity:
    """Each invoke carries a deterministic AgentCore session id so repeat calls
    reuse a warm Context Manager microVM instead of cold-starting one per call."""

    def test_same_caller_and_namespace_get_same_session(self):
        assert runtime_session_id(_payload("u1", "ns1")) == runtime_session_id(_payload("u1", "ns1"))

    def test_different_callers_or_namespaces_never_share(self):
        base = runtime_session_id(_payload("u1", "ns1"))
        assert runtime_session_id(_payload("u2", "ns1")) != base
        assert runtime_session_id(_payload("u1", "ns2")) != base

    def test_meets_agentcore_length_and_hides_identity(self):
        sid = runtime_session_id(_payload("alice@example.com", "ns1"))
        assert len(sid) >= 33
        assert "alice" not in sid

    @pytest.mark.asyncio
    async def test_bearer_path_sends_session_header(self):
        client = _make_client()
        mock_sse = _mock_sse_response(200, events=[{}])
        with patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse):
            await client.invoke(_payload("u1", "ns1"), "jwt")
        headers = mock_sse.last_call_kwargs["headers"]
        assert headers[_SESSION_HEADER] == runtime_session_id(_payload("u1", "ns1"))

    @pytest.mark.asyncio
    async def test_sigv4_path_signs_the_session_header(self):
        client = ContextManagerClient(
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/cm-abc",
            region="us-east-1",
            sigv4=True,
        )
        creds = MagicMock()
        creds.access_key, creds.secret_key, creds.token = "AKIDEXAMPLE", "secret", None
        client._boto_session = MagicMock()
        client._boto_session.get_credentials.return_value = creds
        mock_sse = _mock_sse_response(200, events=[{}])
        with patch("coa_mcp.clients.context_manager.aconnect_sse", mock_sse):
            await client.invoke(_payload("beacon-agent", "ns1"), "ignored")
        headers = mock_sse.last_call_kwargs["headers"]
        assert headers[_SESSION_HEADER] == runtime_session_id(_payload("beacon-agent", "ns1"))
        # the header must be inside the SigV4 signature, not appended after it
        assert _SESSION_HEADER.lower() in headers["Authorization"].lower()
