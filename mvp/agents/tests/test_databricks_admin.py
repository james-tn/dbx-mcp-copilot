from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import databricks_admin
from databricks_admin import (
    DatabricksAdminClient,
    DatabricksAdminPermissionError,
    DatabricksAdminSettings,
)


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
        payload = body.get("payload", {})
        text = body.get("text")
        if text is None:
            if isinstance(payload, str):
                text = payload
            else:
                import json as _json

                text = _json.dumps(payload)
        return type(
            "Response",
            (),
            {
                "status_code": body.get("status_code", 200),
                "json": lambda self=None: payload,
                "text": text,
                "content": text.encode("utf-8"),
            },
        )()

    async def aclose(self):
        return None


def test_ensure_workspace_user_creates_when_missing() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {"payload": {"Resources": []}},
            {"payload": {"id": "user-1", "userName": "seller@example.com"}},
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    result = asyncio.run(client.ensure_workspace_user("seller@example.com"))

    assert result == "created"
    assert http_client.calls[0]["headers"]["X-Databricks-Azure-SP-Management-Token"] == "azure-token-mgmt"
    assert http_client.calls[1]["json"]["userName"] == "seller@example.com"


def test_ensure_workspace_user_raises_permission_error() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {
                "status_code": 403,
                "payload": {"message": "not allowed"},
            }
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    with pytest.raises(DatabricksAdminPermissionError, match="not allowed"):
        asyncio.run(client.ensure_workspace_user("seller@example.com"))


def test_ensure_workspace_service_principal_entitlements_patches_missing_values() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {
                "payload": {
                    "Resources": [
                        {
                            "id": "sp-1",
                            "applicationId": "app-123",
                            "entitlements": [{"value": "workspace-access"}],
                        }
                    ]
                }
            },
            {"payload": {}},
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    result = asyncio.run(
        client.ensure_workspace_service_principal_entitlements(
            "app-123",
            required_entitlements=("workspace-access", "databricks-sql-access"),
        )
    )

    assert result == {
        "status": "patched",
        "applied": ["databricks-sql-access"],
        "required": ["databricks-sql-access", "workspace-access"],
    }
    assert http_client.calls[1]["method"] == "PATCH"
    assert http_client.calls[1]["url"].endswith("/api/2.0/preview/scim/v2/ServicePrincipals/sp-1")
    assert http_client.calls[1]["json"] == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {
                "op": "add",
                "path": "entitlements",
                "value": [{"value": "databricks-sql-access"}],
            }
        ],
    }


def test_ensure_workspace_user_entitlements_patches_missing_values() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {
                "payload": {
                    "Resources": [
                        {
                            "id": "user-1",
                            "userName": "seller@example.com",
                            "entitlements": [{"value": "workspace-access"}],
                        }
                    ]
                }
            },
            {"payload": {}},
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    result = asyncio.run(
        client.ensure_workspace_user_entitlements(
            "seller@example.com",
            required_entitlements=("workspace-access", "databricks-sql-access"),
        )
    )

    assert result == {
        "status": "patched",
        "applied": ["databricks-sql-access"],
        "required": ["databricks-sql-access", "workspace-access"],
    }
    assert http_client.calls[1]["method"] == "PATCH"
    assert http_client.calls[1]["url"].endswith("/api/2.0/preview/scim/v2/Users/user-1")
    assert http_client.calls[1]["json"] == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {
                "op": "add",
                "path": "entitlements",
                "value": [{"value": "databricks-sql-access"}],
            }
        ],
    }


def test_ensure_workspace_user_entitlements_retries_when_scim_id_is_stale(monkeypatch) -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {
                "payload": {
                    "Resources": [
                        {
                            "id": "user-1",
                            "userName": "seller@example.com",
                            "entitlements": [{"value": "workspace-access"}],
                        }
                    ]
                }
            },
            {
                "status_code": 404,
                "payload": {"detail": "User with id user-1 not found."},
            },
            {
                "payload": {
                    "Resources": [
                        {
                            "id": "user-2",
                            "userName": "seller@example.com",
                            "entitlements": [{"value": "workspace-access"}],
                        }
                    ]
                }
            },
            {"payload": {}},
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(databricks_admin.asyncio, "sleep", _fake_sleep)

    result = asyncio.run(
        client.ensure_workspace_user_entitlements(
            "seller@example.com",
            required_entitlements=("workspace-access", "databricks-sql-access"),
        )
    )

    assert result == {
        "status": "patched",
        "applied": ["databricks-sql-access"],
        "required": ["databricks-sql-access", "workspace-access"],
    }
    assert [call["method"] for call in http_client.calls] == ["GET", "PATCH", "GET", "PATCH"]
    assert http_client.calls[1]["url"].endswith("/api/2.0/preview/scim/v2/Users/user-1")
    assert http_client.calls[3]["url"].endswith("/api/2.0/preview/scim/v2/Users/user-2")


def test_ensure_sql_warehouse_permission_falls_back_to_put_on_method_error() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient(
        [
            {"status_code": 405, "payload": {"message": "method not allowed"}},
            {"payload": {}},
        ]
    )
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    asyncio.run(client.ensure_sql_warehouse_permission("wh-123", "mi-client"))

    assert [call["method"] for call in http_client.calls] == ["PATCH", "PUT"]
    assert http_client.calls[0]["url"].endswith("/api/2.0/permissions/warehouses/wh-123")
    assert http_client.calls[1]["url"].endswith("/api/2.0/permissions/warehouses/wh-123")
    assert http_client.calls[1]["json"] == {
        "access_control_list": [
            {
                "service_principal_name": "mi-client",
                "permission_level": "CAN_USE",
            }
        ]
    }


def test_ensure_sql_warehouse_permission_uses_user_name_for_workspace_users() -> None:
    credential = _FakeCredential()
    http_client = _FakeAsyncHttpClient([{"payload": {}}])
    client = DatabricksAdminClient(
        settings=DatabricksAdminSettings(
            host="https://example.databricks.net",
            token_scope="scope",
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id="/subscriptions/123/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
            timeout_seconds=5.0,
            pat=None,
        ),
        credential=credential,
        http_client=http_client,
    )

    asyncio.run(client.ensure_sql_warehouse_permission("wh-123", "seller@example.com"))

    assert http_client.calls[0]["json"] == {
        "access_control_list": [
            {
                "user_name": "seller@example.com",
                "permission_level": "CAN_USE",
            }
        ]
    }
