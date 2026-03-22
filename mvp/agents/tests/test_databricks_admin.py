from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
