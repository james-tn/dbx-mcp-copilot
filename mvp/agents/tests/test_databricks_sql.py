"""Tests for direct MCP tool wiring and Databricks SQL behavior."""

from __future__ import annotations

import asyncio
import os
import sys

from agent_framework import MCPStreamableHTTPTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import next_move
from mcp_server.databricks_sql import DatabricksSqlClient, DatabricksSqlSettings, _extract_rows


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
        return type(
            "Response",
            (),
            {
                "status_code": body.get("status_code", 200),
                "json": lambda self=None: payload,
                "text": "",
            },
        )()

    async def aclose(self):
        return None


def test_extract_rows_supports_manifest_result_data_array_shape() -> None:
    payload = {
        "manifest": {"schema": {"columns": [{"name": "sales_team"}, {"name": "account_count"}]}},
        "result": {"data_array": [{"values": [{"string_value": "GreatLakes-ENT-Named-1"}, {"string_value": "6"}]}]},
    }
    assert _extract_rows(payload) == [{"sales_team": "GreatLakes-ENT-Named-1", "account_count": "6"}]


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


def test_next_move_agent_uses_direct_mcp_tool(monkeypatch) -> None:
    monkeypatch.setenv("MCP_BASE_URL", "https://mcp.example.com/mcp")

    class _FakeClient:
        def as_agent(self, **kwargs):
            return kwargs

    agent = next_move.create_next_move_agent(_FakeClient())
    tool = agent["tools"][0]

    assert isinstance(tool, MCPStreamableHTTPTool)
    assert tool.url == "https://mcp.example.com/mcp"
    assert tool.allowed_tools == [
        "get_scoped_accounts",
        "lookup_rep",
        "get_top_opportunities",
        "get_account_contacts",
    ]
    assert "get_top_opportunities" in tool.allowed_tools
