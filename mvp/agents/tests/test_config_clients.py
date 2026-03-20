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
        }
    ]
    assert _FakeResponsesClient.calls == [
        {
            "deployment_name": "gpt-5.3-chat",
            "endpoint": "https://example.cognitiveservices.azure.com",
            "api_version": "2025-04-01-preview",
            "async_client": _FakeAsyncAzureOpenAI.instances[0],
        }
    ]


def test_get_client_uses_cli_when_no_managed_identity_is_present(monkeypatch) -> None:
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
        }
    ]
    assert _FakeResponsesClient.calls == [
        {
            "deployment_name": "gpt-5.3-chat",
            "endpoint": "https://example.cognitiveservices.azure.com",
            "api_version": "2025-04-01-preview",
            "async_client": _FakeAsyncAzureOpenAI.instances[0],
        }
    ]
