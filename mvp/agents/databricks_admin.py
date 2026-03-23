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

    async def ensure_workspace_user_entitlements(
        self,
        user_upn: str,
        *,
        required_entitlements: tuple[str, ...],
    ) -> dict[str, Any]:
        normalized_user_upn = user_upn.strip()
        if not normalized_user_upn:
            raise DatabricksAdminError("Databricks workspace user upn is required.")

        normalized_required_entitlements = sorted(
            {
                entitlement.strip()
                for entitlement in required_entitlements
                if entitlement and entitlement.strip()
            }
        )
        if not normalized_required_entitlements:
            raise DatabricksAdminError("At least one required Databricks entitlement is required.")

        user = await self.get_workspace_user(normalized_user_upn)
        if user is None:
            raise DatabricksAdminError(
                "Databricks workspace user does not exist for entitlement bootstrap."
            )

        user_id = str(user.get("id", "")).strip()
        if not user_id:
            raise DatabricksAdminError("Databricks workspace user id was missing from SCIM response.")

        current_entitlements = _extract_entitlements(user)
        missing_entitlements = [
            entitlement
            for entitlement in normalized_required_entitlements
            if entitlement not in current_entitlements
        ]
        if not missing_entitlements:
            return {
                "status": "already_set",
                "applied": [],
                "required": normalized_required_entitlements,
            }

        await self._request(
            "PATCH",
            f"/api/2.0/preview/scim/v2/Users/{quote(user_id, safe='')}",
            json_payload={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {
                        "op": "add",
                        "path": "entitlements",
                        "value": [{"value": entitlement} for entitlement in missing_entitlements],
                    }
                ],
            },
            content_type="application/scim+json",
        )
        return {
            "status": "patched",
            "applied": missing_entitlements,
            "required": normalized_required_entitlements,
        }

    async def get_workspace_service_principal(self, application_id: str) -> dict[str, Any] | None:
        encoded_filter = quote(f'applicationId eq "{application_id}"', safe="")
        payload = await self._request(
            "GET",
            f"/api/2.0/preview/scim/v2/ServicePrincipals?filter={encoded_filter}",
            content_type="application/json",
        )
        resources = payload.get("Resources", [])
        if isinstance(resources, list) and resources:
            first = resources[0]
            if isinstance(first, dict):
                return first
        return None

    async def create_workspace_service_principal(
        self,
        application_id: str,
        *,
        display_name: str | None = None,
        entitlements: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        normalized_application_id = application_id.strip()
        normalized_entitlements = sorted(
            {
                entitlement.strip()
                for entitlement in entitlements
                if entitlement and entitlement.strip()
            }
        )
        payload = await self._request(
            "POST",
            "/api/2.0/preview/scim/v2/ServicePrincipals",
            json_payload={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
                "applicationId": normalized_application_id,
                "displayName": display_name or normalized_application_id,
                "entitlements": [
                    {"value": entitlement}
                    for entitlement in normalized_entitlements
                ],
            },
        )
        return payload

    async def ensure_workspace_service_principal(
        self,
        application_id: str,
        *,
        display_name: str | None = None,
        entitlements: tuple[str, ...] = (),
    ) -> str:
        normalized_application_id = application_id.strip()
        if not normalized_application_id:
            raise DatabricksAdminError("Databricks service principal application id is required.")

        existing = await self.get_workspace_service_principal(normalized_application_id)
        if existing is not None:
            return "existing"
        try:
            await self.create_workspace_service_principal(
                normalized_application_id,
                display_name=display_name,
                entitlements=entitlements,
            )
        except DatabricksAdminError:
            retry_existing = await self.get_workspace_service_principal(normalized_application_id)
            if retry_existing is not None:
                return "existing"
            raise
        return "created"

    async def ensure_workspace_service_principal_entitlements(
        self,
        application_id: str,
        *,
        required_entitlements: tuple[str, ...],
    ) -> dict[str, Any]:
        normalized_application_id = application_id.strip()
        if not normalized_application_id:
            raise DatabricksAdminError("Databricks service principal application id is required.")

        normalized_required_entitlements = sorted(
            {
                entitlement.strip()
                for entitlement in required_entitlements
                if entitlement and entitlement.strip()
            }
        )
        if not normalized_required_entitlements:
            raise DatabricksAdminError("At least one required Databricks entitlement is required.")

        service_principal = await self.get_workspace_service_principal(normalized_application_id)
        if service_principal is None:
            raise DatabricksAdminError(
                "Databricks service principal does not exist in workspace for entitlement bootstrap."
            )

        service_principal_id = str(service_principal.get("id", "")).strip()
        if not service_principal_id:
            raise DatabricksAdminError("Databricks service principal id was missing from SCIM response.")

        current_entitlements = _extract_entitlements(service_principal)
        missing_entitlements = [
            entitlement
            for entitlement in normalized_required_entitlements
            if entitlement not in current_entitlements
        ]
        if not missing_entitlements:
            return {
                "status": "already_set",
                "applied": [],
                "required": normalized_required_entitlements,
            }

        await self._request(
            "PATCH",
            f"/api/2.0/preview/scim/v2/ServicePrincipals/{quote(service_principal_id, safe='')}",
            json_payload={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {
                        "op": "add",
                        "path": "entitlements",
                        "value": [{"value": entitlement} for entitlement in missing_entitlements],
                    }
                ],
            },
            content_type="application/scim+json",
        )
        return {
            "status": "patched",
            "applied": missing_entitlements,
            "required": normalized_required_entitlements,
        }

    async def ensure_sql_warehouse_permission(
        self,
        warehouse_id: str,
        principal_name: str,
        *,
        permission_level: str = "CAN_USE",
        principal_type: str | None = None,
    ) -> None:
        normalized_warehouse_id = warehouse_id.strip()
        normalized_principal_name = principal_name.strip()
        if not normalized_warehouse_id:
            raise DatabricksAdminError("Databricks SQL warehouse id is required.")
        if not normalized_principal_name:
            raise DatabricksAdminError("Databricks bootstrap principal name is required.")

        normalized_principal_type = (principal_type or "").strip().lower()
        if not normalized_principal_type:
            normalized_principal_type = "user" if "@" in normalized_principal_name else "service_principal"
        if normalized_principal_type not in {"service_principal", "user"}:
            raise DatabricksAdminError(
                "Databricks SQL warehouse principal type must be service_principal or user."
            )

        payload = {
            "access_control_list": [
                {
                    (
                        "service_principal_name"
                        if normalized_principal_type == "service_principal"
                        else "user_name"
                    ): normalized_principal_name,
                    "permission_level": permission_level,
                }
            ]
        }
        candidate_paths = (
            f"/api/2.0/permissions/warehouses/{normalized_warehouse_id}",
            f"/api/2.0/preview/permissions/warehouses/{normalized_warehouse_id}",
            f"/api/2.0/permissions/sql/warehouses/{normalized_warehouse_id}",
            f"/api/2.0/preview/permissions/sql/warehouses/{normalized_warehouse_id}",
        )

        last_error: DatabricksAdminError | None = None
        for path in candidate_paths:
            for method in ("PATCH", "PUT"):
                try:
                    await self._request(
                        method,
                        path,
                        json_payload=payload,
                        content_type="application/json",
                    )
                    return
                except DatabricksAdminError as exc:
                    last_error = exc
                    error_message = str(exc)
                    if "HTTP 404" in error_message or "HTTP 405" in error_message:
                        continue
                    raise

        if last_error is not None:
            raise last_error


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


def _extract_entitlements(resource: dict[str, Any]) -> set[str]:
    entitlements = resource.get("entitlements", [])
    normalized: set[str] = set()
    if isinstance(entitlements, list):
        for raw_item in entitlements:
            value: str | None = None
            if isinstance(raw_item, dict):
                raw_value = raw_item.get("value")
                if isinstance(raw_value, str):
                    value = raw_value
            elif isinstance(raw_item, str):
                value = raw_item
            if value:
                normalized.add(value.strip())
    return normalized
