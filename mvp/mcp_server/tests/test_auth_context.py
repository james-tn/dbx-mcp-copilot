"""Tests for MCP middle-tier auth helpers."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import shared.enterprise_auth as auth_context
from shared.enterprise_auth import DatabricksOboError, McpAuthSettings, TokenClaims


def test_acquire_databricks_access_token_uses_secret_obo(monkeypatch) -> None:
    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            assert user_assertion == "user-token"
            assert scopes == ["scope"]
            return {"access_token": "dbx-token"}

    monkeypatch.setattr(
        auth_context,
        "load_auth_settings",
        lambda: McpAuthSettings(
            azure_tenant_id="tenant",
            mcp_client_id="client",
            mcp_client_secret="secret",
            mcp_expected_audience="api://mcp",
            databricks_obo_scope="scope",
            managed_identity_client_id="",
            client_assertion_scope="api://AzureADTokenExchange/.default",
        ),
    )
    monkeypatch.setattr(auth_context, "get_confidential_app", lambda: _FakeApp())

    assert auth_context.acquire_databricks_access_token("user-token") == "dbx-token"


def test_acquire_databricks_access_token_uses_managed_identity_assertion(monkeypatch) -> None:
    class _FakeOboCredential:
        def __init__(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant"
            assert kwargs["client_id"] == "client"
            assert kwargs["user_assertion"] == "user-token"
            self.client_assertion = kwargs["client_assertion_func"]()

        def get_token(self, scope):
            assert scope == "scope"
            assert self.client_assertion == "managed-assertion"
            return type("Token", (), {"token": "dbx-token"})()

    monkeypatch.setattr(
        auth_context,
        "load_auth_settings",
        lambda: McpAuthSettings(
            azure_tenant_id="tenant",
            mcp_client_id="client",
            mcp_client_secret="",
            mcp_expected_audience="api://mcp",
            databricks_obo_scope="scope",
            managed_identity_client_id="mi-client",
            client_assertion_scope="api://AzureADTokenExchange/.default",
        ),
    )
    monkeypatch.setattr(auth_context, "build_client_assertion", lambda: "managed-assertion")
    monkeypatch.setattr(auth_context, "OnBehalfOfCredential", _FakeOboCredential)

    assert auth_context.acquire_databricks_access_token("user-token") == "dbx-token"


def test_acquire_databricks_access_token_caches_request_scoped_token(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            calls.append(user_assertion)
            return {"access_token": "dbx-token"}

    monkeypatch.setattr(
        auth_context,
        "load_auth_settings",
        lambda: McpAuthSettings(
            azure_tenant_id="tenant",
            mcp_client_id="client",
            mcp_client_secret="secret",
            mcp_expected_audience="api://mcp",
            databricks_obo_scope="scope",
            managed_identity_client_id="",
            client_assertion_scope="api://AzureADTokenExchange/.default",
        ),
    )
    monkeypatch.setattr(auth_context, "get_confidential_app", lambda: _FakeApp())

    reset_tokens = auth_context.bind_request_identity(
        "user-token",
        TokenClaims(
            oid="user-123",
            tid="tenant",
            upn="seller@example.com",
            aud="api://mcp",
            scp="access_as_user",
        ),
    )
    try:
        assert auth_context.acquire_databricks_access_token() == "dbx-token"
        assert auth_context.acquire_databricks_access_token() == "dbx-token"
    finally:
        auth_context.reset_request_identity(*reset_tokens)

    assert calls == ["user-token"]
