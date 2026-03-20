"""Configuration tests for the thin M365 wrapper."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from m365_wrapper.config import build_auth_handlers


def test_build_auth_handlers_uses_user_auth_for_connector(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_API_SCOPE", "api://planner/access_as_user")
    monkeypatch.setenv("AZUREBOTOAUTHCONNECTIONNAME", "SERVICE_CONNECTION")
    monkeypatch.setenv("OBOCONNECTIONNAME", "PLANNER_API_CONNECTION")
    monkeypatch.setenv("M365_AUTH_HANDLER_ID", "planner_api")

    handlers = build_auth_handlers()

    connector = handlers["planner_api_connector"]
    assert connector.auth_type == "userauthorization"
    assert connector.abs_oauth_connection_name == "SERVICE_CONNECTION"
    assert connector.obo_connection_name == ""
    assert connector.scopes == ["api://planner/access_as_user"]
