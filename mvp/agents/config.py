"""
Configuration helpers for the Azure Container Apps agent-service runtime.

The Daily Account Planner now runs as a stateful agent service surfaced to
Microsoft 365 Copilot through a thin custom-engine forwarder. Azure OpenAI
stays the planner model runtime, while Databricks access is handled separately
through delegated OBO.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urljoin

from azure.identity import AzureCliCredential, DefaultAzureCredential, get_bearer_token_provider

from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from openai import AsyncAzureOpenAI

try:
    from ..shared.identity import is_hosted_environment
    from ..shared.runtime_env import ensure_runtime_env_loaded
except ImportError:
    _MODULE_PATH = Path(__file__).resolve()
    for _candidate_root in (_MODULE_PATH.parent.parent, _MODULE_PATH.parent):
        if (_candidate_root / "shared").exists():
            if str(_candidate_root) not in sys.path:
                sys.path.insert(0, str(_candidate_root))
            break
    from shared.identity import is_hosted_environment
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()

_DEFAULT_ENDPOINT = "https://eastus2openai001.cognitiveservices.azure.com"
_DEFAULT_MODEL = "gpt-5.3-chat"
_DEFAULT_API_VERSION = "2025-04-01-preview"
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_RETRIES = 6
_DEFAULT_AZURE_DATABRICKS_SCOPE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
_DEFAULT_CUSTOMER_TOP_OPPORTUNITIES_SOURCE = "prod_catalog.data_science_account_iq_gold.account_iq_scores"
_DEFAULT_CUSTOMER_CONTACTS_SOURCE = "prod_catalog.account_iq_gold.aiq_contact"
_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _first_env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _load_json_text_from_path(*env_keys: str) -> str:
    configured = _first_env_value(*env_keys)
    if not configured:
        return ""

    path = Path(configured).expanduser()
    candidate_paths = [path] if path.is_absolute() else [
        (Path.cwd() / path).resolve(),
        (Path(__file__).resolve().parent / path).resolve(),
        (Path(__file__).resolve().parent.parent / path).resolve(),
    ]
    for candidate in candidate_paths:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


def _should_use_default_credential() -> bool:
    return is_hosted_environment()


def _get_credential():
    return DefaultAzureCredential() if _should_use_default_credential() else AzureCliCredential()


def _get_api_key() -> str | None:
    if get_secure_deployment_enabled():
        return None
    value = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    return value or None


def _normalize_endpoint(endpoint: str) -> str:
    return (endpoint or _DEFAULT_ENDPOINT).strip()


def _normalize_host_with_https(host: str) -> str:
    normalized = (host or "").strip().rstrip("/")
    if not normalized:
        return ""
    if "://" in normalized:
        return normalized
    return f"https://{normalized}"


def _get_model() -> str:
    return (
        os.environ.get(
            "AZURE_OPENAI_DEPLOYMENT",
            os.environ.get(
                "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME",
                os.environ.get("AZURE_OPENAI_MODEL", _DEFAULT_MODEL),
            ),
        ).strip()
        or _DEFAULT_MODEL
    )


def _get_api_version() -> str:
    return os.environ.get("AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION).strip() or _DEFAULT_API_VERSION


def get_openai_timeout_seconds() -> float:
    try:
        return max(10.0, float(os.environ.get("AZURE_OPENAI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))))
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def get_openai_max_retries() -> int:
    try:
        return max(0, int(os.environ.get("AZURE_OPENAI_MAX_RETRIES", str(_DEFAULT_MAX_RETRIES))))
    except ValueError:
        return _DEFAULT_MAX_RETRIES


def get_client() -> AzureOpenAIResponsesClient:
    """Responses API client for the planner runtime."""
    model = _get_model()
    endpoint = _normalize_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT", _DEFAULT_ENDPOINT))
    api_version = _get_api_version()
    api_key = _get_api_key()
    base_url = urljoin(endpoint.rstrip("/") + "/", "openai/")
    timeout = get_openai_timeout_seconds()
    max_retries = get_openai_max_retries()

    if api_key:
        async_client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout,
            max_retries=max_retries,
        )
        return AzureOpenAIResponsesClient(
            api_key=api_key,
            deployment_name=model,
            endpoint=endpoint,
            base_url=base_url,
            api_version=api_version,
            async_client=async_client,
        )

    credential = _get_credential()
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    async_client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
        timeout=timeout,
        max_retries=max_retries,
    )
    return AzureOpenAIResponsesClient(
        deployment_name=model,
        endpoint=endpoint,
        base_url=base_url,
        api_version=api_version,
        async_client=async_client,
    )


def get_chat_client() -> AzureOpenAIChatClient:
    """Chat Completions client for the optional local tool-router path."""
    model = _get_model()
    endpoint = _normalize_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT", _DEFAULT_ENDPOINT))
    api_key = _get_api_key()

    if api_key:
        return AzureOpenAIChatClient(
            api_key=api_key,
            deployment_name=model,
            endpoint=endpoint,
            api_version=_get_api_version(),
        )

    credential = _get_credential()
    return AzureOpenAIChatClient(
        deployment_name=model,
        endpoint=endpoint,
        credential=credential,
    )


def get_session_store_mode() -> str:
    return os.environ.get("SESSION_STORE_MODE", "memory").strip().lower() or "memory"


def get_session_max_turns() -> int:
    try:
        return max(1, int(os.environ.get("SESSION_MAX_TURNS", "20")))
    except ValueError:
        return 20


def get_session_max_sessions() -> int:
    try:
        return max(1, int(os.environ.get("SESSION_MAX_SESSIONS", "500")))
    except ValueError:
        return 500


def get_session_idle_ttl_seconds() -> float:
    try:
        return max(60.0, float(os.environ.get("SESSION_IDLE_TTL_SECONDS", "28800")))
    except ValueError:
        return 28800.0


def get_secure_deployment_enabled() -> bool:
    value = os.environ.get("SECURE_DEPLOYMENT", "false").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_effective_ri_scope_mode() -> str:
    if get_secure_deployment_enabled():
        return "user"
    return os.environ.get("RI_SCOPE_MODE", "user").strip().lower() or "user"


def get_account_pulse_execution_mode() -> str:
    value = os.environ.get("ACCOUNT_PULSE_EXECUTION_MODE", "dynamic_parallel").strip().lower()
    if value in {"legacy_sequential", "dynamic_parallel"}:
        return value
    return "dynamic_parallel"


def get_account_pulse_max_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("ACCOUNT_PULSE_MAX_CONCURRENCY", "8")))
    except ValueError:
        return 8


def get_account_pulse_model_concurrency() -> int:
    raw_value = os.environ.get("ACCOUNT_PULSE_MAX_MODEL_CONCURRENCY", "").strip()
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            pass
    default_limit = 3 if get_secure_deployment_enabled() else 4
    return min(get_account_pulse_max_concurrency(), default_limit)


def get_account_pulse_source_mode() -> str:
    value = os.environ.get("ACCOUNT_PULSE_SOURCE_MODE", "live").strip().lower()
    if value in {"live", "replay"}:
        return value
    return "live"


def get_account_pulse_replay_fixture_set() -> str:
    return os.environ.get("ACCOUNT_PULSE_REPLAY_FIXTURE_SET", "small_parent_set").strip() or "small_parent_set"


def get_account_pulse_internal_aggregator_enabled() -> bool:
    value = os.environ.get("ACCOUNT_PULSE_ENABLE_INTERNAL_AGGREGATOR", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_customer_backend_mode() -> str:
    value = os.environ.get("CUSTOMER_BACKEND_MODE", "").strip().lower()
    if value in {"customer_existing_databricks", "demo_seeded"}:
        return value
    mock_enabled = os.environ.get("MOCK_DATABRICKS_ENVIRONMENT", "").strip().lower()
    if mock_enabled in {"1", "true", "yes", "on"}:
        return "demo_seeded"
    customer_host = _first_env_value("DATABRICKS_HOST", "CUSTOMER_DATABRICKS_HOST")
    has_customer_runtime_sources = any(
        (
            _first_env_value("TOP_OPPORTUNITIES_SOURCE", "CUSTOMER_TOP_OPPORTUNITIES_SOURCE"),
            _first_env_value("CONTACTS_SOURCE", "CUSTOMER_CONTACTS_SOURCE"),
            _first_env_value("SCOPE_ACCOUNTS_CATALOG", "CUSTOMER_SCOPE_ACCOUNTS_CATALOG"),
            _first_env_value("SALES_TEAM_MAPPING_CATALOG", "CUSTOMER_SALES_TEAM_MAPPING_CATALOG"),
        )
    )
    if customer_host and (get_secure_deployment_enabled() or has_customer_runtime_sources):
        return "customer_existing_databricks"
    if get_secure_deployment_enabled():
        return "customer_existing_databricks"
    return "demo_seeded"


def get_customer_backend_enabled() -> bool:
    if get_secure_deployment_enabled():
        return True
    return get_customer_backend_mode() == "customer_existing_databricks"


def get_dap_api_base_url() -> str:
    return os.environ.get("DAP_API_BASE_URL", "").strip().rstrip("/")


def get_dap_api_client_id() -> str:
    return os.environ.get("DAP_API_CLIENT_ID", "").strip()


def get_dap_api_scope() -> str:
    configured = os.environ.get("DAP_API_SCOPE", "").strip()
    if configured:
        return configured
    client_id = get_dap_api_client_id()
    if client_id:
        return f"api://{client_id}/.default"
    return ""


def get_dap_api_expected_audience() -> str:
    configured = os.environ.get("DAP_API_EXPECTED_AUDIENCE", "").strip()
    if configured:
        return configured
    client_id = get_dap_api_client_id()
    if client_id:
        return f"api://{client_id}"
    return ""


def get_dap_api_auth_mode() -> str:
    value = os.environ.get("DAP_API_AUTH_MODE", "obo").strip().lower()
    if value in {"obo", "forward_user_token"}:
        return value
    return "obo"


def get_dap_api_token_header_mode() -> str:
    value = os.environ.get("DAP_API_TOKEN_HEADER_MODE", "authorization").strip().lower()
    if value in {"authorization", "x_forwarded_access_token", "both"}:
        return value
    return "authorization"


def get_dap_healthcheck_path() -> str:
    return os.environ.get("DAP_HEALTHCHECK_PATH", "/api/v1/healthcheck").strip() or "/api/v1/healthcheck"


def get_dap_accounts_query_path() -> str:
    return os.environ.get("DAP_ACCOUNTS_QUERY_PATH", "/api/v1/accounts/query").strip() or "/api/v1/accounts/query"


def get_dap_debug_headers_path() -> str:
    return os.environ.get("DAP_DEBUG_HEADERS_PATH", "/api/v1/debug/headers").strip() or "/api/v1/debug/headers"


def get_customer_databricks_host() -> str:
    return _normalize_host_with_https(
        _first_env_value("DATABRICKS_HOST", "CUSTOMER_DATABRICKS_HOST").rstrip("/")
    )


def get_customer_databricks_scope() -> str:
    return (
        _first_env_value("DATABRICKS_OBO_SCOPE", "CUSTOMER_DATABRICKS_OBO_SCOPE")
        or _DEFAULT_AZURE_DATABRICKS_SCOPE
    )


def get_customer_databricks_warehouse_id() -> str:
    return _first_env_value("DATABRICKS_WAREHOUSE_ID", "CUSTOMER_DATABRICKS_WAREHOUSE_ID")


def get_customer_databricks_resource_id() -> str:
    return _first_env_value("DATABRICKS_AZURE_RESOURCE_ID", "CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID")


def get_customer_databricks_pat() -> str:
    return _first_env_value("DATABRICKS_PAT", "CUSTOMER_DATABRICKS_PAT")


def get_customer_sales_team_static_map_json() -> str:
    configured = _first_env_value("SALES_TEAM_STATIC_MAP_JSON", "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON")
    if configured:
        return configured
    return _load_json_text_from_path(
        "SALES_TEAM_STATIC_MAP_JSON_PATH",
        "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH",
    )


def get_customer_sales_team_mapping_source() -> str:
    return _first_env_value("SALES_TEAM_MAPPING_SOURCE", "CUSTOMER_SALES_TEAM_MAPPING_SOURCE")


def get_customer_sales_team_mapping_query() -> str:
    return _first_env_value("SALES_TEAM_MAPPING_QUERY", "CUSTOMER_SALES_TEAM_MAPPING_QUERY")


def get_customer_sales_team_user_column() -> str:
    return (
        _first_env_value("SALES_TEAM_MAPPING_USER_COLUMN", "CUSTOMER_SALES_TEAM_MAPPING_USER_COLUMN")
        or "user_upn"
    )


def get_customer_sales_team_column() -> str:
    return (
        _first_env_value("SALES_TEAM_MAPPING_TEAM_COLUMN", "CUSTOMER_SALES_TEAM_MAPPING_TEAM_COLUMN")
        or "sales_team"
    )


def get_customer_scope_accounts_source() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_SOURCE", "CUSTOMER_SCOPE_ACCOUNTS_SOURCE")


def get_customer_scope_accounts_static_json_path() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_STATIC_JSON_PATH", "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH")


def get_customer_scope_accounts_query() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_QUERY", "CUSTOMER_SCOPE_ACCOUNTS_QUERY")


def get_customer_scope_accounts_catalog() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_CATALOG", "CUSTOMER_SCOPE_ACCOUNTS_CATALOG")


def get_customer_scope_accounts_schema() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_SCHEMA", "CUSTOMER_SCOPE_ACCOUNTS_SCHEMA")


def get_customer_scope_accounts_table() -> str:
    return _first_env_value("SCOPE_ACCOUNTS_TABLE", "CUSTOMER_SCOPE_ACCOUNTS_TABLE")


def get_customer_contacts_source() -> str:
    configured = _first_env_value("CONTACTS_SOURCE", "CUSTOMER_CONTACTS_SOURCE")
    if configured:
        return configured
    if get_secure_deployment_enabled():
        return _DEFAULT_CUSTOMER_CONTACTS_SOURCE
    return ""


def get_customer_contacts_query() -> str:
    return _first_env_value("CONTACTS_QUERY", "CUSTOMER_CONTACTS_QUERY")


def get_customer_contacts_catalog() -> str:
    return _first_env_value("CONTACTS_CATALOG", "CUSTOMER_CONTACTS_CATALOG")


def get_customer_contacts_schema() -> str:
    return _first_env_value("CONTACTS_SCHEMA", "CUSTOMER_CONTACTS_SCHEMA")


def get_customer_contacts_table() -> str:
    return _first_env_value("CONTACTS_TABLE", "CUSTOMER_CONTACTS_TABLE")


def get_customer_top_opportunities_source() -> str:
    configured = _first_env_value("TOP_OPPORTUNITIES_SOURCE", "CUSTOMER_TOP_OPPORTUNITIES_SOURCE")
    if configured:
        return configured
    if get_secure_deployment_enabled():
        return _DEFAULT_CUSTOMER_TOP_OPPORTUNITIES_SOURCE
    return ""


def get_customer_top_opportunities_query() -> str:
    return _first_env_value("TOP_OPPORTUNITIES_QUERY", "CUSTOMER_TOP_OPPORTUNITIES_QUERY")


def get_customer_top_opportunities_catalog() -> str:
    return _first_env_value("TOP_OPPORTUNITIES_CATALOG", "CUSTOMER_TOP_OPPORTUNITIES_CATALOG")


def get_customer_top_opportunities_schema() -> str:
    return _first_env_value("TOP_OPPORTUNITIES_SCHEMA", "CUSTOMER_TOP_OPPORTUNITIES_SCHEMA")


def get_customer_top_opportunities_table() -> str:
    return _first_env_value("TOP_OPPORTUNITIES_TABLE", "CUSTOMER_TOP_OPPORTUNITIES_TABLE")


def get_customer_sales_team_mapping_catalog() -> str:
    return _first_env_value("SALES_TEAM_MAPPING_CATALOG", "CUSTOMER_SALES_TEAM_MAPPING_CATALOG")


def get_customer_sales_team_mapping_schema() -> str:
    return _first_env_value("SALES_TEAM_MAPPING_SCHEMA", "CUSTOMER_SALES_TEAM_MAPPING_SCHEMA")


def get_customer_sales_team_mapping_table() -> str:
    return _first_env_value("SALES_TEAM_MAPPING_TABLE", "CUSTOMER_SALES_TEAM_MAPPING_TABLE")


def get_customer_legacy_static_fallback_enabled() -> bool:
    configured = _first_env_value(
        "LEGACY_STATIC_CUSTOMER_FALLBACK_ENABLED",
        "CUSTOMER_LEGACY_STATIC_FALLBACK_ENABLED",
    )
    if configured:
        return configured.lower() in _TRUTHY_VALUES
    return not is_hosted_environment()
