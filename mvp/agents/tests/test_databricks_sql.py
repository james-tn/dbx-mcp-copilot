"""Tests for the direct Databricks SQL client and semantic tools."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import sentinel

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import databricks_tools
from databricks_sql import DatabricksSqlClient, DatabricksSqlSettings, _extract_rows

_JSON_MODULE = json


class _FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, scope: str):
        self.scopes.append(scope)
        suffix = "mgmt" if "management.core.windows.net" in scope else "dbx"
        return type("Token", (), {"token": f"azure-token-{suffix}"})()


class _FakeAsyncHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, headers=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        body = self.responses.pop(0)
        payload = body["payload"]
        text = body.get("text")
        if text is None:
            text = _JSON_MODULE.dumps(payload) if not isinstance(payload, str) else payload
        return type(
            "Response",
            (),
            {
                "status_code": body.get("status_code", 200),
                "json": lambda self=None: payload,
                "text": text,
                "raise_for_status": (
                    lambda self=None: None
                    if body.get("status_code", 200) < 400
                    else (_ for _ in ()).throw(RuntimeError("http error"))
                ),
            },
        )()

    async def aclose(self):
        return None


def test_extract_rows_supports_manifest_result_data_array_shape() -> None:
    payload = {
        "manifest": {
            "schema": {
                "columns": [
                    {"name": "sales_team"},
                    {"name": "account_count"},
                ]
            }
        },
        "result": {
            "data_array": [
                {
                    "values": [
                        {"string_value": "GreatLakes-ENT-Named-1"},
                        {"string_value": "6"},
                    ]
                }
            ]
        },
    }
    assert _extract_rows(payload) == [
        {"sales_team": "GreatLakes-ENT-Named-1", "account_count": "6"}
    ]


def test_extract_rows_coerces_manifest_typed_values() -> None:
    payload = {
        "manifest": {
            "schema": {
                "columns": [
                    {"name": "account_id", "type_name": "STRING"},
                    {"name": "is_subsidiary", "type_name": "BOOLEAN"},
                    {"name": "xf_score_previous_day", "type_name": "DOUBLE"},
                    {"name": "renewal_date", "type_name": "DATE"},
                ]
            }
        },
        "result": {
            "data_array": [
                {
                    "values": [
                        {"string_value": "001GL0001"},
                        {"string_value": "false"},
                        {"string_value": "74.0"},
                        {"null_value": "NULL_VALUE"},
                    ]
                }
            ]
        },
    }

    assert _extract_rows(payload) == [
        {
            "account_id": "001GL0001",
            "is_subsidiary": False,
            "xf_score_previous_day": 74.0,
            "renewal_date": None,
        }
    ]


def test_query_sql_discovers_warehouse_and_polls_results() -> None:
    credential = _FakeCredential()
    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id=None,
            warehouse_id=None,
            timeout_seconds=5.0,
            retry_count=1,
            poll_attempts=2,
            poll_interval_seconds=0.0,
            pat=None,
        ),
        credential=credential,
        http_client=_FakeAsyncHttpClient(
            [
                {
                    "payload": {
                        "warehouses": [{"id": "warehouse-1", "state": "RUNNING"}],
                    }
                },
                {
                    "payload": {
                        "statement_id": "stmt-1",
                        "status": {"state": "PENDING"},
                    }
                },
                {
                    "payload": {
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "current_user"}]}},
                        "result": {"data_array": [{"values": [{"string_value": "seller@example.com"}]}]},
                    }
                },
            ]
        ),
    )

    rows = asyncio.run(client.query_sql("SELECT current_user() AS current_user"))

    assert rows == [{"current_user": "seller@example.com"}]
    assert credential.scopes == ["scope", "scope", "scope"]
    asyncio.run(client.close())


def test_authorization_header_prefers_explicit_access_token() -> None:
    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id=None,
            warehouse_id="warehouse-1",
            timeout_seconds=5.0,
            retry_count=1,
            poll_attempts=2,
            poll_interval_seconds=0.0,
            pat=None,
        ),
        access_token="delegated-token",
        credential=_FakeCredential(),
        http_client=_FakeAsyncHttpClient([]),
    )

    assert asyncio.run(client._authorization_header()) == {"Authorization": "Bearer delegated-token"}


def test_authorization_header_adds_azure_workspace_headers_for_service_principal() -> None:
    credential = _FakeCredential()
    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            warehouse_id="warehouse-1",
            timeout_seconds=5.0,
            retry_count=1,
            poll_attempts=2,
            poll_interval_seconds=0.0,
            pat=None,
        ),
        credential=credential,
        http_client=_FakeAsyncHttpClient([]),
    )

    assert asyncio.run(client._authorization_header()) == {
        "Authorization": "Bearer azure-token-dbx",
        "X-Databricks-Azure-SP-Management-Token": "azure-token-mgmt",
        "X-Databricks-Azure-Workspace-Resource-Id": "/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
    }
    assert credential.scopes == ["scope", "https://management.core.windows.net//.default"]


def test_execute_returns_typed_rows() -> None:
    credential = _FakeCredential()
    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id=None,
            warehouse_id=None,
            timeout_seconds=5.0,
            retry_count=1,
            poll_attempts=2,
            poll_interval_seconds=0.0,
            pat=None,
        ),
        credential=credential,
        http_client=_FakeAsyncHttpClient(
            [
                {
                    "payload": {
                        "warehouses": [{"id": "warehouse-1", "state": "RUNNING"}],
                    }
                },
                {
                    "payload": {
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "count", "type_name": "INT"}]}},
                        "result": {"data_array": [{"values": [{"string_value": "5"}]}]},
                    }
                },
            ]
        ),
    )

    rows = asyncio.run(client.execute("SELECT COUNT(*) AS count FROM foo"))

    assert rows == [{"count": 5}]
    asyncio.run(client.close())


def test_execute_retries_with_discovered_warehouse_when_configured_one_is_missing() -> None:
    credential = _FakeCredential()
    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id=None,
            warehouse_id="missing-warehouse",
            timeout_seconds=5.0,
            retry_count=0,
            poll_attempts=2,
            poll_interval_seconds=0.0,
            pat=None,
        ),
        credential=credential,
        http_client=_FakeAsyncHttpClient(
            [
                {
                    "status_code": 404,
                    "payload": {"message": "The warehouse missing-warehouse was not found."},
                },
                {
                    "payload": {
                        "warehouses": [{"id": "warehouse-1", "state": "RUNNING"}],
                    }
                },
                {
                    "payload": {
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "count", "type_name": "INT"}]}},
                        "result": {"data_array": [{"values": [{"string_value": "5"}]}]},
                    }
                },
            ]
        ),
    )

    rows = asyncio.run(client.execute("SELECT COUNT(*) AS count FROM foo"))

    assert rows == [{"count": 5}]
    assert client._resolved_warehouse_id == "warehouse-1"
    asyncio.run(client.close())


def test_get_top_opportunities_blocks_override_in_authenticated_session(monkeypatch) -> None:
    monkeypatch.setattr(databricks_tools, "get_request_user_assertion", lambda: "user-token")

    with pytest.raises(ValueError, match="authenticated planner sessions"):
        asyncio.run(
            databricks_tools.get_top_opportunities.func(territory_override="Germany-ENT-Named-5")
        )


def test_lookup_rep_returns_authenticated_session_error(monkeypatch) -> None:
    monkeypatch.setattr(databricks_tools, "get_request_user_assertion", lambda: "user-token")

    payload = json.loads(asyncio.run(databricks_tools.lookup_rep.func(rep_name="Scott")))

    assert "disabled in authenticated planner sessions" in payload["error"]


def test_get_top_opportunities_blocks_override_in_secure_deployment(monkeypatch) -> None:
    monkeypatch.setattr(databricks_tools, "get_request_user_assertion", lambda: None)
    monkeypatch.setenv("SECURE_DEPLOYMENT", "true")

    with pytest.raises(ValueError, match="secure deployment mode"):
        asyncio.run(
            databricks_tools.get_top_opportunities.func(territory_override="Germany-ENT-Named-5")
        )


def test_get_scoped_accounts_uses_demo_territory(monkeypatch) -> None:
    monkeypatch.setenv("RI_SCOPE_MODE", "demo")
    monkeypatch.setenv("RI_DEMO_TERRITORY", "UK-COM-Named-3")

    async def _fake_run_query(statement):
        return [{"account_id": "001", "global_ultimate": "Tesco PLC", "sales_team": "UK-COM-Named-3"}]

    monkeypatch.setattr(databricks_tools, "_run_query", _fake_run_query)

    payload = json.loads(asyncio.run(databricks_tools.get_scoped_accounts.func()))

    assert payload["territory"] == "UK-COM-Named-3"
    assert payload["territories"] == ["UK-COM-Named-3"]
    assert payload["segment"] == "COM"
    assert payload["unique_global_ultimates"] == 1


def test_get_scoped_accounts_summarizes_user_scope(monkeypatch) -> None:
    monkeypatch.setenv("RI_SCOPE_MODE", "user")

    async def _fake_run_query(statement):
        return [
            {"account_id": "001", "global_ultimate": "Ford", "sales_team": "GreatLakes-ENT-Named-1"},
            {"account_id": "002", "global_ultimate": "adidas", "sales_team": "Germany-ENT-Named-5"},
        ]

    monkeypatch.setattr(databricks_tools, "_run_query", _fake_run_query)

    payload = json.loads(asyncio.run(databricks_tools.get_scoped_accounts.func()))

    assert payload["territory"] is None
    assert payload["territories"] == ["Germany-ENT-Named-5", "GreatLakes-ENT-Named-1"]
    assert payload["segment"] == "MIXED"
