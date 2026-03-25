"""Tests for planner API auth helpers."""

from __future__ import annotations

import os
import sys

import pytest
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import auth_context
from auth_context import AuthSettings, TokenClaims


def test_expected_audiences_expand_api_uri_variants() -> None:
    settings = AuthSettings(
        azure_tenant_id="tenant",
        planner_api_client_id="client",
        planner_api_expected_audience="api://planner-api,planner-api",
    )

    assert settings.expected_audiences == ["api://planner-api", "planner-api", "client"]


def test_extract_bearer_token_requires_header() -> None:
    with pytest.raises(auth_context.AuthenticationRequiredError):
        auth_context.extract_bearer_token(None)


def test_bind_request_identity_exposes_request_claims() -> None:
    reset_tokens = auth_context.bind_request_identity(
        "user-token",
        TokenClaims(
            oid="user-123",
            tid="tenant",
            upn="seller@example.com",
            aud="api://planner-api",
            scp="access_as_user",
        ),
    )
    try:
        assert auth_context.get_request_user_assertion() == "user-token"
        assert auth_context.get_request_user_id() == "user-123"
    finally:
        auth_context.reset_request_identity(*reset_tokens)


def test_planner_mcp_bearer_auth_uses_request_identity() -> None:
    reset_tokens = auth_context.bind_request_identity(
        "planner-token",
        TokenClaims(
            oid="user-123",
            tid="tenant",
            upn="seller@example.com",
            aud="api://planner-api",
            scp="access_as_user",
        ),
    )
    try:
        auth = auth_context.PlannerMcpBearerAuth()
        request = httpx.Request("GET", "https://example.test/mcp")
        request = next(auth.auth_flow(request))
        assert request.headers["Authorization"] == "Bearer planner-token"
    finally:
        auth_context.reset_request_identity(*reset_tokens)
