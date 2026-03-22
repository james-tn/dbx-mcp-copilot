"""
Databricks admin helpers for secure bootstrap operations.

This module is only used by the secure seed/bootstrap path. It handles
workspace principal verification and provisioning through Databricks SCIM APIs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from databricks_network import enable_private_databricks_resolution
from databricks_sql import DatabricksSqlSettings


class DatabricksAdminError(RuntimeError):
    """Base error for Databricks admin operations."""


class DatabricksAdminAuthError(DatabricksAdminError):
    """Raised when Databricks admin authentication fails."""


class DatabricksAdminPermissionError(DatabricksAdminError):
    """Raised when Databricks admin authorization fails."""


@dataclass(frozen=True)
class DatabricksAdminSettings:
    host: str
    token_scope: str
    azure_management_scope: str
    azure_workspace_resource_id: str | None
    timeout_seconds: float
    pat: str | None

    @classmethod
    def from_sql_settings(cls, settings: DatabricksSqlSettings) -> "DatabricksAdminSettings":
        return cls(
            host=settings.host,
            token_scope=settings.token_scope,
            azure_management_scope=settings.azure_management_scope,
            azure_workspace_resource_id=settings.azure_workspace_resource_id,
            timeout_seconds=settings.timeout_seconds,
            pat=settings.pat,
        )


class DatabricksAdminClient:
    """Databricks SCIM client for workspace principal bootstrap."""

    def __init__(
        self,
        settings: DatabricksAdminSettings,
        *,
        access_token: str | None = None,
        credential: Any | None = None,
        http_client: httpx.AsyncClient | Any | None = None,
    ) -> None:
        self.settings = settings
        enable_private_databricks_resolution(self.settings.host)
        self.access_token = access_token.strip() if access_token else None
        self.credential = credential
        self.http_client = http_client or httpx.AsyncClient(timeout=self.settings.timeout_seconds)
        self._owns_client = http_client is None

    async def close(self) -> None:
        if self._owns_client and hasattr(self.http_client, "aclose"):
            await self.http_client.aclose()

    async def _authorization_headers(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        if self.settings.pat:
            return {"Authorization": f"Bearer {self.settings.pat}"}
        if self.credential is None:
            raise DatabricksAdminAuthError("Databricks admin credential is not configured.")

        databricks_token = await asyncio.to_thread(
            self.credential.get_token,
            self.settings.token_scope,
        )
        headers = {"Authorization": f"Bearer {databricks_token.token}"}
        if self.settings.azure_workspace_resource_id:
            management_token = await asyncio.to_thread(
                self.credential.get_token,
                self.settings.azure_management_scope,
            )
            headers["X-Databricks-Azure-SP-Management-Token"] = management_token.token
            headers["X-Databricks-Azure-Workspace-Resource-Id"] = (
                self.settings.azure_workspace_resource_id
            )
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        content_type: str = "application/scim+json",
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": content_type,
            **(await self._authorization_headers()),
        }
        response = await self.http_client.request(
            method,
            f"{self.settings.host}{path}",
            headers=headers,
            json=json_payload,
        )
        if response.status_code in {401, 403}:
            message = _format_databricks_error(response)
            if response.status_code == 401:
                raise DatabricksAdminAuthError(message)
            raise DatabricksAdminPermissionError(message)
        if response.status_code >= 400:
            raise DatabricksAdminError(_format_databricks_error(response))

        if not response.content:
            return {}
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise DatabricksAdminError(
                f"Databricks admin API returned non-JSON response ({response.status_code})."
            ) from exc
        if not isinstance(body, dict):
            raise DatabricksAdminError("Databricks admin API returned an unexpected response shape.")
        return body

    async def get_workspace_user(self, user_upn: str) -> dict[str, Any] | None:
        encoded_filter = quote(f'userName eq "{user_upn}"', safe="")
        payload = await self._request(
            "GET",
            f"/api/2.0/preview/scim/v2/Users?filter={encoded_filter}",
            content_type="application/json",
        )
        resources = payload.get("Resources", [])
        if isinstance(resources, list) and resources:
            first = resources[0]
            if isinstance(first, dict):
                return first
        return None

    async def create_workspace_user(self, user_upn: str) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/api/2.0/preview/scim/v2/Users",
            json_payload={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": user_upn,
                "displayName": user_upn.split("@", 1)[0],
            },
        )
        return payload

    async def ensure_workspace_user(self, user_upn: str) -> str:
        existing = await self.get_workspace_user(user_upn)
        if existing is not None:
            return "existing"
        try:
            await self.create_workspace_user(user_upn)
        except DatabricksAdminError:
            retry_existing = await self.get_workspace_user(user_upn)
            if retry_existing is not None:
                return "existing"
            raise
        return "created"


def _format_databricks_error(response: httpx.Response) -> str:
    status = response.status_code
    body_text = response.text.strip()
    if not body_text:
        return f"Databricks admin API request failed with HTTP {status}."

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return f"Databricks admin API request failed with HTTP {status}: {body_text[:500]}"

    if isinstance(payload, dict):
        if "detail" in payload and isinstance(payload["detail"], str):
            return f"Databricks admin API request failed with HTTP {status}: {payload['detail']}"
        if "message" in payload and isinstance(payload["message"], str):
            return f"Databricks admin API request failed with HTTP {status}: {payload['message']}"
        if "error_code" in payload:
            return (
                "Databricks admin API request failed with HTTP "
                f"{status}: {json.dumps(payload, sort_keys=True)[:500]}"
            )

    return f"Databricks admin API request failed with HTTP {status}: {body_text[:500]}"
