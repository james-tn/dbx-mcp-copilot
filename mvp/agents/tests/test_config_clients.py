"""Tests for planner API Azure OpenAI client construction."""

from __future__ import annotations

import os
from pathlib import Path
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


def test_dap_api_scope_defaults_from_client_id(monkeypatch) -> None:
    monkeypatch.delenv("DAP_API_SCOPE", raising=False)
    monkeypatch.delenv("DAP_API_EXPECTED_AUDIENCE", raising=False)
    monkeypatch.setenv("DAP_API_CLIENT_ID", "11111111-2222-3333-4444-555555555555")

    assert config.get_dap_api_scope() == "api://11111111-2222-3333-4444-555555555555/.default"
    assert config.get_dap_api_expected_audience() == "api://11111111-2222-3333-4444-555555555555"


def test_dap_api_explicit_scope_and_audience_override_client_id_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DAP_API_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("DAP_API_SCOPE", "api://custom-dap/access_as_user")
    monkeypatch.setenv("DAP_API_EXPECTED_AUDIENCE", "api://custom-dap")

    assert config.get_dap_api_scope() == "api://custom-dap/access_as_user"
    assert config.get_dap_api_expected_audience() == "api://custom-dap"


def test_customer_top_opportunities_config_helpers(monkeypatch) -> None:
    monkeypatch.setenv("TOP_OPPORTUNITIES_SOURCE", "prod_catalog.data_science_account_iq_gold.account_iq_scores")
    monkeypatch.setenv("TOP_OPPORTUNITIES_CATALOG", "prod_catalog")
    monkeypatch.setenv("TOP_OPPORTUNITIES_SCHEMA", "data_science_account_iq_gold")
    monkeypatch.setenv("TOP_OPPORTUNITIES_TABLE", "account_iq_scores")

    assert config.get_customer_top_opportunities_source() == "prod_catalog.data_science_account_iq_gold.account_iq_scores"
    assert config.get_customer_top_opportunities_catalog() == "prod_catalog"
    assert config.get_customer_top_opportunities_schema() == "data_science_account_iq_gold"
    assert config.get_customer_top_opportunities_table() == "account_iq_scores"


def test_secure_customer_defaults_enable_customer_backend_and_legacy_sources(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")
    monkeypatch.delenv("CUSTOMER_BACKEND_MODE", raising=False)
    monkeypatch.delenv("CUSTOMER_TOP_OPPORTUNITIES_SOURCE", raising=False)
    monkeypatch.delenv("CUSTOMER_CONTACTS_SOURCE", raising=False)
    monkeypatch.delenv("CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH", raising=False)

    assert config.get_customer_backend_enabled() is True
    assert config.get_customer_backend_mode() == "customer_existing_databricks"
    assert config.get_customer_top_opportunities_source() == "prod_catalog.data_science_account_iq_gold.account_iq_scores"
    assert config.get_customer_contacts_source() == "prod_catalog.account_iq_gold.aiq_contact"
    assert config.get_customer_scope_accounts_static_json_path() == ""


def test_customer_databricks_host_enables_customer_backend_without_mode(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "false")
    monkeypatch.delenv("CUSTOMER_BACKEND_MODE", raising=False)
    monkeypatch.delenv("MOCK_DATABRICKS_ENVIRONMENT", raising=False)
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-example.azuredatabricks.net")
    monkeypatch.setenv("TOP_OPPORTUNITIES_SOURCE", "catalog.schema.account_iq_scores")

    assert config.get_customer_backend_mode() == "customer_existing_databricks"
    assert config.get_customer_backend_enabled() is True


def test_customer_databricks_host_adds_https_when_missing(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "adb-example.azuredatabricks.net")

    assert config.get_customer_databricks_host() == "https://adb-example.azuredatabricks.net"


def test_mock_databricks_mode_wins_over_host_detection(monkeypatch) -> None:
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")
    monkeypatch.setenv("MOCK_DATABRICKS_ENVIRONMENT", "true")
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-example.azuredatabricks.net")
    monkeypatch.setenv("TOP_OPPORTUNITIES_SOURCE", "catalog.schema.account_iq_scores")

    assert config.get_customer_backend_mode() == "demo_seeded"


def test_customer_sales_team_static_map_helper_can_load_from_path(monkeypatch, tmp_path: Path) -> None:
    sales_team_path = tmp_path / "sales_team.json"
    sales_team_path.write_text('{"seller@example.com":"GreatLakes-ENT-Named-1"}', encoding="utf-8")

    monkeypatch.delenv("SALES_TEAM_STATIC_MAP_JSON", raising=False)
    monkeypatch.setenv("SALES_TEAM_STATIC_MAP_JSON_PATH", str(sales_team_path))

    assert config.get_customer_sales_team_static_map_json() == '{"seller@example.com":"GreatLakes-ENT-Named-1"}'


def test_customer_config_helpers_still_accept_legacy_customer_aliases(monkeypatch) -> None:
    monkeypatch.setenv("CUSTOMER_DATABRICKS_HOST", "adb-legacy.azuredatabricks.net")
    monkeypatch.setenv("CUSTOMER_TOP_OPPORTUNITIES_SOURCE", "legacy.catalog.top_opps")
    monkeypatch.setenv("CUSTOMER_CONTACTS_SOURCE", "legacy.catalog.contacts")

    assert config.get_customer_databricks_host() == "https://adb-legacy.azuredatabricks.net"
    assert config.get_customer_top_opportunities_source() == "legacy.catalog.top_opps"
    assert config.get_customer_contacts_source() == "legacy.catalog.contacts"
