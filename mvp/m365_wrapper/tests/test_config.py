"""Configuration tests for the thin M365 wrapper."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from m365_wrapper.config import (
    build_auth_handlers,
    build_connection_manager,
    get_wrapper_incremental_delivery_enabled,
    get_wrapper_debug_allowed_upns,
    get_wrapper_debug_chat_enabled,
    get_wrapper_debug_expected_audience,
    get_wrapper_ack_threshold_seconds,
    get_wrapper_long_running_messages_enabled,
    get_wrapper_timeout_seconds,
)


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


def test_wrapper_long_running_defaults(monkeypatch) -> None:
    monkeypatch.delenv("WRAPPER_FORWARD_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS", raising=False)
    monkeypatch.delenv("WRAPPER_ENABLE_LONG_RUNNING_MESSAGES", raising=False)
    monkeypatch.delenv("WRAPPER_ENABLE_DEBUG_CHAT", raising=False)

    assert get_wrapper_timeout_seconds() == 300.0
    assert get_wrapper_ack_threshold_seconds() == 10.0
    assert get_wrapper_long_running_messages_enabled() is True
    assert get_wrapper_debug_chat_enabled() is False


def test_wrapper_long_running_config_parses_explicit_values(monkeypatch) -> None:
    monkeypatch.setenv("WRAPPER_FORWARD_TIMEOUT_SECONDS", "123")
    monkeypatch.setenv("WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS", "7")
    monkeypatch.setenv("WRAPPER_ENABLE_LONG_RUNNING_MESSAGES", "false")

    assert get_wrapper_timeout_seconds() == 123.0
    assert get_wrapper_ack_threshold_seconds() == 7.0
    assert get_wrapper_long_running_messages_enabled() is False


def test_wrapper_debug_chat_settings(monkeypatch) -> None:
    monkeypatch.setenv("WRAPPER_ENABLE_DEBUG_CHAT", "true")
    monkeypatch.setenv(
        "WRAPPER_DEBUG_ALLOWED_UPNS",
        "ri-test-na@m365cpi89838450.onmicrosoft.com, DaichiM@M365CPI89838450.OnMicrosoft.com ",
    )
    monkeypatch.setenv("BOT_APP_ID", "bot-app-id")
    monkeypatch.delenv("WRAPPER_DEBUG_EXPECTED_AUDIENCE", raising=False)
    monkeypatch.delenv("BOT_SSO_RESOURCE", raising=False)

    assert get_wrapper_debug_chat_enabled() is True
    assert get_wrapper_debug_allowed_upns() == {
        "ri-test-na@m365cpi89838450.onmicrosoft.com",
        "daichim@m365cpi89838450.onmicrosoft.com",
    }
    assert get_wrapper_debug_expected_audience() == "api://botid-bot-app-id"


def test_build_connection_manager_supports_user_managed_identity(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv("BOT_APP_ID", "bot-app-id")
    monkeypatch.setenv("BOT_AUTH_TYPE", "user_managed_identity")
    monkeypatch.setenv("BOT_MANAGED_IDENTITY_CLIENT_ID", "managed-identity-client-id")
    monkeypatch.delenv("BOT_APP_PASSWORD", raising=False)

    manager = build_connection_manager()
    config = manager.get_default_connection_configuration()

    assert str(config.AUTH_TYPE).lower().endswith("user_managed_identity")
    assert config.CLIENT_ID == "managed-identity-client-id"
    assert config.CLIENT_SECRET is None


def test_build_connection_manager_requires_explicit_user_managed_identity_client_id(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv("BOT_APP_ID", "bot-app-id")
    monkeypatch.setenv("BOT_AUTH_TYPE", "user_managed_identity")
    monkeypatch.delenv("BOT_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.delenv("BOT_APP_PASSWORD", raising=False)

    try:
        build_connection_manager()
    except ValueError as exc:
        assert "BOT_MANAGED_IDENTITY_CLIENT_ID" in str(exc)
    else:
        raise AssertionError("Expected explicit managed identity client id requirement")


def test_wrapper_incremental_delivery_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("WRAPPER_ENABLE_INCREMENTAL_DELIVERY", raising=False)
    assert get_wrapper_incremental_delivery_enabled() is False


def test_wrapper_incremental_delivery_flag_parses_true(monkeypatch) -> None:
    monkeypatch.setenv("WRAPPER_ENABLE_INCREMENTAL_DELIVERY", "true")
    assert get_wrapper_incremental_delivery_enabled() is True
