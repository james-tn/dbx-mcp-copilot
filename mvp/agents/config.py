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
    _MVP_ROOT = Path(__file__).resolve().parent.parent
    if str(_MVP_ROOT) not in sys.path:
        sys.path.insert(0, str(_MVP_ROOT))
    from shared.identity import is_hosted_environment
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()

_DEFAULT_ENDPOINT = "https://eastus2openai001.cognitiveservices.azure.com"
_DEFAULT_MODEL = "gpt-5.3-chat"
_DEFAULT_API_VERSION = "2025-04-01-preview"
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_RETRIES = 6


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
