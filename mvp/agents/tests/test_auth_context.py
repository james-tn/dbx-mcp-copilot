"""Tests for planner API auth helpers."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import auth_context
from auth_context import AuthSettings, DatabricksOboError, TokenClaims


def test_expected_audiences_expand_api_uri_variants() -> None:
    settings = AuthSettings(
        azure_tenant_id="tenant",
        planner_api_client_id="client",
        planner_api_client_secret="secret",
        planner_api_expected_audience="api://planner-api,planner-api",
        databricks_obo_scope="scope",
    )

    assert settings.expected_audiences == ["api://planner-api", "planner-api"]


def test_extract_bearer_token_requires_header() -> None:
    with pytest.raises(auth_context.AuthenticationRequiredError):
        auth_context.extract_bearer_token(None)


def test_acquire_databricks_access_token_uses_obo(monkeypatch) -> None:
    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            assert user_assertion == "user-token"
            assert scopes == ["scope"]
            return {"access_token": "dbx-token"}

    monkeypatch.setattr(
        auth_context,
        "load_auth_settings",
        lambda: AuthSettings(
            azure_tenant_id="tenant",
            planner_api_client_id="client",
            planner_api_client_secret="secret",
            planner_api_expected_audience="api://planner-api",
            databricks_obo_scope="scope",
        ),
    )
    monkeypatch.setattr(auth_context, "get_confidential_app", lambda: _FakeApp())

    assert auth_context.acquire_databricks_access_token("user-token") == "dbx-token"


def test_acquire_databricks_access_token_raises_on_obo_failure(monkeypatch) -> None:
    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            return {"error_description": "consent required"}

    monkeypatch.setattr(
        auth_context,
        "load_auth_settings",
        lambda: AuthSettings(
            azure_tenant_id="tenant",
            planner_api_client_id="client",
            planner_api_client_secret="secret",
            planner_api_expected_audience="api://planner-api",
            databricks_obo_scope="scope",
        ),
    )
    monkeypatch.setattr(auth_context, "get_confidential_app", lambda: _FakeApp())

    with pytest.raises(DatabricksOboError, match="consent required"):
        auth_context.acquire_databricks_access_token("user-token")
