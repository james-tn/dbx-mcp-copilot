"""Tests for planner API Azure OpenAI client construction."""

from __future__ import annotations

import os
import sys
from unittest.mock import sentinel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


class _FakeResponsesClient:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)


class _FakeAsyncAzureOpenAI:
    calls: list[dict] = []
    instances: list["_FakeAsyncAzureOpenAI"] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)
        self.kwargs = kwargs
        self.instances.append(self)


def test_get_client_uses_default_credential_when_identity_is_present(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://identity")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.3-chat")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setattr(config, "DefaultAzureCredential", lambda: sentinel.hosted_credential)
    monkeypatch.setattr(config, "get_bearer_token_provider", lambda credential, scope: sentinel.token_provider)
    monkeypatch.setattr(config, "AzureOpenAIResponsesClient", _FakeResponsesClient)
    monkeypatch.setattr(config, "AsyncAzureOpenAI", _FakeAsyncAzureOpenAI)

    _FakeResponsesClient.calls.clear()
    _FakeAsyncAzureOpenAI.calls.clear()
    _FakeAsyncAzureOpenAI.instances.clear()
    config.get_client()

    assert _FakeAsyncAzureOpenAI.calls == [
        {
            "azure_endpoint": "https://example.cognitiveservices.azure.com",
            "azure_ad_token_provider": sentinel.token_provider,
            "api_version": "2025-04-01-preview",
            "timeout": 120.0,
            "max_retries": 6,
        }
    ]
    assert _FakeResponsesClient.calls == [
        {
            "deployment_name": "gpt-5.3-chat",
            "endpoint": "https://example.cognitiveservices.azure.com",
            "base_url": "https://example.cognitiveservices.azure.com/openai/",
            "api_version": "2025-04-01-preview",
            "async_client": _FakeAsyncAzureOpenAI.instances[0],
        }
    ]


def test_get_client_uses_cli_when_no_managed_identity_is_present(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.delenv("MSI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CONTAINER_APP_NAME", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.3-chat")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setattr(config, "AzureCliCredential", lambda: sentinel.local_credential)
    monkeypatch.setattr(config, "get_bearer_token_provider", lambda credential, scope: sentinel.token_provider)
    monkeypatch.setattr(config, "AzureOpenAIResponsesClient", _FakeResponsesClient)
    monkeypatch.setattr(config, "AsyncAzureOpenAI", _FakeAsyncAzureOpenAI)

    _FakeResponsesClient.calls.clear()
    _FakeAsyncAzureOpenAI.calls.clear()
    _FakeAsyncAzureOpenAI.instances.clear()
    config.get_client()

    assert _FakeAsyncAzureOpenAI.calls == [
        {
            "azure_endpoint": "https://example.cognitiveservices.azure.com",
            "azure_ad_token_provider": sentinel.token_provider,
            "api_version": "2025-04-01-preview",
            "timeout": 120.0,
            "max_retries": 6,
        }
    ]
    assert _FakeResponsesClient.calls == [
        {
            "deployment_name": "gpt-5.3-chat",
            "endpoint": "https://example.cognitiveservices.azure.com",
            "base_url": "https://example.cognitiveservices.azure.com/openai/",
            "api_version": "2025-04-01-preview",
            "async_client": _FakeAsyncAzureOpenAI.instances[0],
        }
    ]


def test_get_client_prefers_api_key_when_present(monkeypatch) -> None:
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.delenv("MSI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CONTAINER_APP_NAME", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.3-chat")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "get_bearer_token_provider", lambda *_args, **_kwargs: sentinel.token_provider)
    monkeypatch.setattr(config, "AzureOpenAIResponsesClient", _FakeResponsesClient)
    monkeypatch.setattr(config, "AsyncAzureOpenAI", _FakeAsyncAzureOpenAI)

    _FakeResponsesClient.calls.clear()
    _FakeAsyncAzureOpenAI.calls.clear()
    _FakeAsyncAzureOpenAI.instances.clear()
    config.get_client()

    assert _FakeAsyncAzureOpenAI.calls == [
        {
            "azure_endpoint": "https://example.cognitiveservices.azure.com",
            "api_key": "test-key",
            "api_version": "2025-04-01-preview",
            "timeout": 120.0,
            "max_retries": 6,
        }
    ]
    assert _FakeResponsesClient.calls == [
        {
            "api_key": "test-key",
            "deployment_name": "gpt-5.3-chat",
            "endpoint": "https://example.cognitiveservices.azure.com",
            "base_url": "https://example.cognitiveservices.azure.com/openai/",
            "api_version": "2025-04-01-preview",
            "async_client": _FakeAsyncAzureOpenAI.instances[0],
        }
    ]


def test_account_pulse_execution_mode_defaults_to_dynamic_parallel(monkeypatch) -> None:
    monkeypatch.delenv("ACCOUNT_PULSE_EXECUTION_MODE", raising=False)

    assert config.get_account_pulse_execution_mode() == "dynamic_parallel"


def test_secure_deployment_forces_user_scope(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")
    monkeypatch.setenv("RI_SCOPE_MODE", "demo")

    assert config.get_secure_deployment_enabled() is True
    assert config.get_effective_ri_scope_mode() == "user"


def test_account_pulse_model_concurrency_defaults_lower_in_secure_mode(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")
    monkeypatch.setenv("ACCOUNT_PULSE_MAX_CONCURRENCY", "8")
    monkeypatch.delenv("ACCOUNT_PULSE_MAX_MODEL_CONCURRENCY", raising=False)

    assert config.get_account_pulse_model_concurrency() == 3


def test_account_pulse_model_concurrency_respects_explicit_cap(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")
    monkeypatch.setenv("ACCOUNT_PULSE_MAX_CONCURRENCY", "8")
    monkeypatch.setenv("ACCOUNT_PULSE_MAX_MODEL_CONCURRENCY", "2")

    assert config.get_account_pulse_model_concurrency() == 2


def test_session_store_defaults_include_ttl_and_capacity(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_MAX_SESSIONS", raising=False)
    monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)

    assert config.get_session_max_sessions() == 500
    assert config.get_session_idle_ttl_seconds() == 28800.0
