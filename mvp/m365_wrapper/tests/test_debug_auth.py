"""Tests for wrapper debug-chat auth helpers."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from m365_wrapper import debug_auth


def test_extract_bearer_token_requires_header() -> None:
    with pytest.raises(debug_auth.DebugAuthValidationError):
        debug_auth.extract_bearer_token(None)


def test_acquire_planner_token_on_behalf_of_uses_wrapper_obo(monkeypatch) -> None:
    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            assert user_assertion == "wrapper-user-token"
            assert scopes == ["api://planner/access_as_user"]
            return {"access_token": "planner-obo-token"}

    monkeypatch.setattr(
        debug_auth,
        "load_debug_auth_settings",
        lambda expected_audience=None: debug_auth.DebugAuthSettings(
            tenant_id="tenant",
            wrapper_client_id="wrapper-app",
            wrapper_client_secret="secret",
            expected_audience="api://botid-wrapper-app",
            planner_scope="api://planner/access_as_user",
        ),
    )
    monkeypatch.setattr(debug_auth, "get_debug_confidential_app", lambda expected_audience=None: _FakeApp())

    token = debug_auth.acquire_planner_token_on_behalf_of(
        user_assertion="wrapper-user-token",
        expected_audience="api://botid-wrapper-app",
    )

    assert token == "planner-obo-token"


def test_acquire_planner_token_on_behalf_of_raises_on_failure(monkeypatch) -> None:
    class _FakeApp:
        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            return {"error_description": "consent required"}

    monkeypatch.setattr(
        debug_auth,
        "load_debug_auth_settings",
        lambda expected_audience=None: debug_auth.DebugAuthSettings(
            tenant_id="tenant",
            wrapper_client_id="wrapper-app",
            wrapper_client_secret="secret",
            expected_audience="api://botid-wrapper-app",
            planner_scope="api://planner/access_as_user",
        ),
    )
    monkeypatch.setattr(debug_auth, "get_debug_confidential_app", lambda expected_audience=None: _FakeApp())

    with pytest.raises(debug_auth.DebugAuthOboError, match="consent required"):
        debug_auth.acquire_planner_token_on_behalf_of(
            user_assertion="wrapper-user-token",
            expected_audience="api://botid-wrapper-app",
        )
