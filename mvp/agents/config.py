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

from azure.identity import AzureCliCredential, DefaultAzureCredential, get_bearer_token_provider

from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from openai import AsyncAzureOpenAI

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)

_DEFAULT_ENDPOINT = "https://eastus2openai001.cognitiveservices.azure.com"
_DEFAULT_MODEL = "gpt-5.3-chat"
_DEFAULT_API_VERSION = "2025-04-01-preview"


def _should_use_default_credential() -> bool:
    identity_markers = (
        "IDENTITY_ENDPOINT",
        "MSI_ENDPOINT",
        "AZURE_CLIENT_ID",
        "CONTAINER_APP_NAME",
    )
    return any(os.environ.get(marker) for marker in identity_markers)


def _get_credential():
    return DefaultAzureCredential() if _should_use_default_credential() else AzureCliCredential()


def _normalize_endpoint(endpoint: str) -> str:
    value = (endpoint or _DEFAULT_ENDPOINT).strip()
    if ".openai.azure.com" in value:
        host = value.split("//")[1].split(".")[0]
        return f"https://{host}.cognitiveservices.azure.com"
    return value


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


def get_client() -> AzureOpenAIResponsesClient:
    """Responses API client for the planner runtime."""
    model = _get_model()
    endpoint = _normalize_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT", _DEFAULT_ENDPOINT))
    credential = _get_credential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    async_client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=_get_api_version(),
    )
    return AzureOpenAIResponsesClient(
        deployment_name=model,
        endpoint=endpoint,
        api_version=_get_api_version(),
        async_client=async_client,
    )


def get_chat_client() -> AzureOpenAIChatClient:
    """Chat Completions client for the optional local tool-router path."""
    model = _get_model()
    endpoint = _normalize_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT", _DEFAULT_ENDPOINT))
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


def get_account_pulse_execution_mode() -> str:
    value = os.environ.get("ACCOUNT_PULSE_EXECUTION_MODE", "legacy_sequential").strip().lower()
    if value in {"legacy_sequential", "dynamic_parallel"}:
        return value
    return "legacy_sequential"


def get_account_pulse_max_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("ACCOUNT_PULSE_MAX_CONCURRENCY", "8")))
    except ValueError:
        return 8


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
