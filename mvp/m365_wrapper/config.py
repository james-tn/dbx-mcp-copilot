"""
Configuration helpers for the thin M365 wrapper service.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import AgentAuthConfiguration, AuthHandler, AuthTypes

try:
    from ..shared.runtime_env import ensure_runtime_env_loaded
except ImportError:
    _MODULE_PATH = Path(__file__).resolve()
    for _candidate_root in (_MODULE_PATH.parent.parent, _MODULE_PATH.parent):
        if (_candidate_root / "shared").exists():
            if str(_candidate_root) not in sys.path:
                sys.path.insert(0, str(_candidate_root))
            break
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required.")
    return value


def get_planner_service_base_url() -> str:
    return _required("PLANNER_SERVICE_BASE_URL").rstrip("/")


def get_bot_app_id() -> str:
    return _required("BOT_APP_ID")


def get_planner_api_scope() -> str:
    configured = os.environ.get("PLANNER_API_SCOPE", "").strip()
    if configured:
        return configured
    audience = _required("PLANNER_API_EXPECTED_AUDIENCE").rstrip("/")
    return f"{audience}/access_as_user"


def get_wrapper_timeout_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("WRAPPER_FORWARD_TIMEOUT_SECONDS", "300")))
    except ValueError:
        return 300.0


def get_wrapper_ack_threshold_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS", "10")))
    except ValueError:
        return 10.0


def get_wrapper_long_running_messages_enabled() -> bool:
    value = os.environ.get("WRAPPER_ENABLE_LONG_RUNNING_MESSAGES", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_wrapper_debug_chat_enabled() -> bool:
    value = os.environ.get("WRAPPER_ENABLE_DEBUG_CHAT", "false").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_wrapper_debug_allowed_upns() -> set[str]:
    configured = os.environ.get("WRAPPER_DEBUG_ALLOWED_UPNS", "").strip()
    if not configured:
        return set()
    return {
        item.strip().lower()
        for item in configured.split(",")
        if item.strip()
    }


def get_wrapper_debug_expected_audience() -> str:
    configured = os.environ.get("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "").strip()
    if configured:
        return configured
    bot_sso_resource = os.environ.get("BOT_SSO_RESOURCE", "").strip()
    if bot_sso_resource:
        return bot_sso_resource
    return f"api://botid-{get_bot_app_id()}"


def get_port() -> int:
    try:
        return int(os.environ.get("PORT", "3978"))
    except ValueError:
        return 3978


def get_handler_base_id() -> str:
    return os.environ.get("M365_AUTH_HANDLER_ID", "planner_api").strip() or "planner_api"


def get_handler_ids() -> tuple[str, str]:
    base = get_handler_base_id()
    return (f"{base}_agentic", f"{base}_connector")


def get_abs_oauth_connection_name() -> str:
    return os.environ.get("AZUREBOTOAUTHCONNECTIONNAME", "").strip() or "SERVICE_CONNECTION"


def get_obo_connection_name() -> str:
    return os.environ.get("OBOCONNECTIONNAME", "").strip() or "PLANNER_API_CONNECTION"


def build_connection_manager() -> MsalConnectionManager:
    tenant_id = _required("AZURE_TENANT_ID")
    bot_app_id = _required("BOT_APP_ID")
    bot_app_password = _required("BOT_APP_PASSWORD")

    service_connection = AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,
        connection_name="SERVICE_CONNECTION",
        tenant_id=tenant_id,
        client_id=bot_app_id,
        client_secret=bot_app_password,
    )

    obo_connection_name = get_obo_connection_name()
    abs_oauth_connection_name = get_abs_oauth_connection_name()
    connections: dict[str, AgentAuthConfiguration] = {
        "SERVICE_CONNECTION": service_connection,
    }
    if abs_oauth_connection_name not in connections:
        connections[abs_oauth_connection_name] = AgentAuthConfiguration(
            auth_type=AuthTypes.client_secret,
            connection_name=abs_oauth_connection_name,
            tenant_id=tenant_id,
            client_id=bot_app_id,
            client_secret=bot_app_password,
        )
    if obo_connection_name != "SERVICE_CONNECTION":
        connections[obo_connection_name] = AgentAuthConfiguration(
            auth_type=AuthTypes.client_secret,
            connection_name=obo_connection_name,
            tenant_id=tenant_id,
            client_id=bot_app_id,
            client_secret=bot_app_password,
        )

    return MsalConnectionManager(connections_configurations=connections)


def build_auth_handlers() -> dict[str, AuthHandler]:
    scope = get_planner_api_scope()
    abs_oauth_connection_name = get_abs_oauth_connection_name()
    obo_connection_name = get_obo_connection_name()
    agentic_id, connector_id = get_handler_ids()
    return {
        agentic_id: AuthHandler(
            name=agentic_id,
            title="Sign in to Daily Account Planner",
            text="Sign in",
            abs_oauth_connection_name=abs_oauth_connection_name,
            obo_connection_name=obo_connection_name,
            auth_type="AgenticUserAuthorization",
            scopes=[scope],
        ),
        connector_id: AuthHandler(
            name=connector_id,
            title="Sign in to Daily Account Planner",
            text="Sign in",
            abs_oauth_connection_name=abs_oauth_connection_name,
            obo_connection_name="",
            auth_type="UserAuthorization",
            scopes=[scope],
        ),
    }
