"""
Direct Databricks SQL helper for the Daily Account Planner.

The planner executes a small set of app-owned canned queries through the
Databricks SQL Statements API. Agents never receive a dynamic SQL capability.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import AzureCliCredential, DefaultAzureCredential

from databricks_network import enable_private_databricks_resolution

_DATABRICKS_AUDIENCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
_DEFAULT_HOST = "https://adb-7405610222366876.16.azuredatabricks.net"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_RETRY_COUNT = 1
_DEFAULT_POLL_ATTEMPTS = 6
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_AZURE_MANAGEMENT_SCOPE = "https://management.core.windows.net//.default"


class DatabricksSqlError(RuntimeError):
    """Base error for Databricks SQL execution."""


class DatabricksSqlAuthError(DatabricksSqlError):
    """Raised when Databricks authentication fails."""


@dataclass(frozen=True)
class DatabricksSqlSettings:
    host: str
    token_scope: str
    azure_management_scope: str
    azure_workspace_resource_id: str | None
    warehouse_id: str | None
    timeout_seconds: float
    retry_count: int
    poll_attempts: int
    poll_interval_seconds: float
    pat: str | None


def _is_hosted() -> bool:
    identity_markers = ("IDENTITY_ENDPOINT", "MSI_ENDPOINT", "AZURE_CLIENT_ID", "CONTAINER_APP_NAME")
    return any(os.environ.get(marker) for marker in identity_markers)


def load_settings() -> DatabricksSqlSettings:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/") or _DEFAULT_HOST
    return DatabricksSqlSettings(
        host=host,
        token_scope=(
            os.environ.get(
                "DATABRICKS_OBO_SCOPE",
                os.environ.get("DATABRICKS_TOKEN_SCOPE", _DATABRICKS_AUDIENCE),
            ).strip()
            or _DATABRICKS_AUDIENCE
        ),
        azure_management_scope=(
            os.environ.get(
                "DATABRICKS_AZURE_MANAGEMENT_SCOPE",
                _DEFAULT_AZURE_MANAGEMENT_SCOPE,
            ).strip()
            or _DEFAULT_AZURE_MANAGEMENT_SCOPE
        ),
        azure_workspace_resource_id=(
            os.environ.get("DATABRICKS_AZURE_RESOURCE_ID", "").strip() or None
        ),
        warehouse_id=os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip() or None,
        timeout_seconds=float(
            os.environ.get("DATABRICKS_SQL_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
        ),
        retry_count=int(os.environ.get("DATABRICKS_SQL_RETRY_COUNT", str(_DEFAULT_RETRY_COUNT))),
        poll_attempts=int(
            os.environ.get("DATABRICKS_SQL_POLL_ATTEMPTS", str(_DEFAULT_POLL_ATTEMPTS))
        ),
        poll_interval_seconds=float(
            os.environ.get(
                "DATABRICKS_SQL_POLL_INTERVAL_SECONDS",
                str(_DEFAULT_POLL_INTERVAL_SECONDS),
            )
        ),
        pat=os.environ.get("DATABRICKS_PAT", "").strip() or None,
    )


def _build_credential():
    return DefaultAzureCredential() if _is_hosted() else AzureCliCredential()


def _coerce_typed_scalar(value: Any, type_name: str) -> Any:
    if value == "NULL_VALUE":
        return None

    if not isinstance(value, str):
        return value

    normalized_type = (type_name or "").upper()
    lower_value = value.lower()

    if normalized_type == "BOOLEAN" or lower_value in {"true", "false"}:
        if lower_value == "true":
            return True
        if lower_value == "false":
            return False

    if normalized_type in {"TINYINT", "SMALLINT", "INT", "INTEGER", "BIGINT", "LONG"}:
        try:
            return int(value)
        except ValueError:
            return value

    if normalized_type in {"FLOAT", "DOUBLE", "DECIMAL"}:
        try:
            return float(value)
        except ValueError:
            return value

    return value


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    manifest = payload.get("manifest", {})
    result = payload.get("result", {})
    schema = manifest.get("schema", {}) if isinstance(manifest, dict) else {}
    columns = schema.get("columns", []) if isinstance(schema, dict) else []
    data_array = result.get("data_array", []) if isinstance(result, dict) else []

    if not isinstance(columns, list) or not isinstance(data_array, list):
        return []

    column_specs = [
        {
            "name": str(col.get("name")),
            "type_name": str(col.get("type_name", "")).upper(),
        }
        for col in columns
        if isinstance(col, dict) and col.get("name") is not None
    ]
    column_names = [spec["name"] for spec in column_specs]
    normalized: list[dict[str, Any]] = []

    for row in data_array:
        if isinstance(row, dict) and isinstance(row.get("values"), list):
            values: list[Any] = []
            for index, value in enumerate(row["values"]):
                type_name = column_specs[index]["type_name"] if index < len(column_specs) else ""
                if not isinstance(value, dict):
                    values.append(_coerce_typed_scalar(value, type_name))
                    continue

                raw_value: Any
                if "null_value" in value:
                    raw_value = None
                elif "boolean_value" in value:
                    raw_value = value["boolean_value"]
                elif "long_value" in value:
                    raw_value = value["long_value"]
                elif "double_value" in value:
                    raw_value = value["double_value"]
                elif "string_value" in value:
                    raw_value = value["string_value"]
                else:
                    raw_value = next(iter(value.values()), None)
                values.append(_coerce_typed_scalar(raw_value, type_name))

            normalized.append(
                {
                    column_names[index]: values[index]
                    for index in range(min(len(column_names), len(values)))
                }
            )
        elif isinstance(row, list):
            normalized.append(
                {
                    column_names[index]: row[index]
                    for index in range(min(len(column_names), len(row)))
                }
            )

    return normalized


def _is_pending(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status", {})
    if isinstance(status, dict):
        state = str(status.get("state", "")).upper()
        return state in {"PENDING", "RUNNING", "QUEUED"}
    return False


class DatabricksSqlClient:
    """Direct Databricks SQL Statements API client."""

    def __init__(
        self,
        settings: DatabricksSqlSettings | None = None,
        *,
        access_token: str | None = None,
        credential: Any | None = None,
        http_client: httpx.AsyncClient | Any | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        enable_private_databricks_resolution(self.settings.host)
        self.access_token = access_token.strip() if access_token else None
        self.credential = credential or _build_credential()
        self.http_client = http_client or httpx.AsyncClient(timeout=self.settings.timeout_seconds)
        self._owns_client = http_client is None
        self._resolved_warehouse_id = self.settings.warehouse_id

    async def close(self) -> None:
        if self._owns_client and hasattr(self.http_client, "aclose"):
            await self.http_client.aclose()

    async def _authorization_header(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        if self.settings.pat:
            return {"Authorization": f"Bearer {self.settings.pat}"}
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
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        headers = {
            "Content-Type": "application/json",
            **(await self._authorization_header()),
        }

        for attempt in range(self.settings.retry_count + 1):
            try:
                response = await self.http_client.request(
                    method,
                    f"{self.settings.host}{path}",
                    headers=headers,
                    json=json_payload,
                )
                if response.status_code in {401, 403}:
                    raise DatabricksSqlAuthError(_format_databricks_http_error(response))
                if response.status_code >= 400:
                    raise DatabricksSqlError(_format_databricks_http_error(response))
                body = response.json()
                if not isinstance(body, dict):
                    raise DatabricksSqlError("Databricks SQL returned an unexpected response shape.")
                error = body.get("status", {}).get("error")
                if isinstance(error, dict):
                    raise DatabricksSqlError(str(error.get("message", "Databricks SQL returned an error.")))
                return body
            except DatabricksSqlAuthError:
                raise
            except (httpx.HTTPError, DatabricksSqlError) as exc:
                last_error = exc
                if attempt >= self.settings.retry_count:
                    break
                await asyncio.sleep(0.25)

        raise DatabricksSqlError("Databricks SQL request failed.") from last_error

    async def resolve_warehouse_id(self) -> str:
        if self._resolved_warehouse_id:
            return self._resolved_warehouse_id

        payload = await self._request("GET", "/api/2.0/sql/warehouses")
        warehouses = payload.get("warehouses", [])
        if not isinstance(warehouses, list) or not warehouses:
            raise DatabricksSqlError("No Databricks SQL warehouse was found.")

        preferred = None
        for warehouse in warehouses:
            if not isinstance(warehouse, dict):
                continue
            state = str(warehouse.get("state", "")).upper()
            if state in {"RUNNING", "STARTING", "STARTED"}:
                preferred = warehouse
                break
        if preferred is None:
            preferred = next((w for w in warehouses if isinstance(w, dict)), None)
        warehouse_id = str((preferred or {}).get("id", "")).strip()
        if not warehouse_id:
            raise DatabricksSqlError("Could not resolve a Databricks SQL warehouse id.")
        self._resolved_warehouse_id = warehouse_id
        return warehouse_id

    async def _resolve_warehouse_id(self) -> str:
        return await self.resolve_warehouse_id()

    async def execute(self, statement: str) -> list[dict[str, Any]]:
        payload = await self._execute_statement(
            statement,
            wait_timeout=f"{max(5, int(self.settings.timeout_seconds))}s",
        )

        if _is_pending(payload):
            statement_id = str(payload.get("statement_id", "")).strip()
            if not statement_id:
                raise DatabricksSqlError(
                    "Databricks SQL returned a pending result without a statement id."
                )
            for _ in range(self.settings.poll_attempts):
                await asyncio.sleep(self.settings.poll_interval_seconds)
                payload = await self._request(
                    "GET",
                    f"/api/2.0/sql/statements/{statement_id}",
                )
                if not _is_pending(payload):
                    break

        rows = _extract_rows(payload)
        if rows:
            return rows
        if payload.get("status", {}).get("state") == "SUCCEEDED":
            return []
        raise DatabricksSqlError("Databricks SQL statement did not return rows.")

    async def query_sql(self, statement: str) -> list[dict[str, Any]]:
        payload = await self._execute_statement(
            statement,
            wait_timeout="0s",
        )

        if _is_pending(payload):
            statement_id = str(payload.get("statement_id", "")).strip()
            if not statement_id:
                raise DatabricksSqlError(
                    "Databricks SQL returned a pending result without a statement id."
                )
            for _ in range(self.settings.poll_attempts):
                await asyncio.sleep(self.settings.poll_interval_seconds)
                payload = await self._request(
                    "GET",
                    f"/api/2.0/sql/statements/{statement_id}",
                )
                if not _is_pending(payload):
                    break

        rows = _extract_rows(payload)
        if rows:
            return rows
        if payload.get("status", {}).get("state") == "SUCCEEDED":
            return []
        raise DatabricksSqlError("Databricks SQL statement did not return rows.")

    async def _execute_statement(self, statement: str, *, wait_timeout: str) -> dict[str, Any]:
        warehouse_id = await self.resolve_warehouse_id()
        try:
            return await self._request(
                "POST",
                "/api/2.0/sql/statements",
                json_payload={
                    "statement": statement,
                    "warehouse_id": warehouse_id,
                    "wait_timeout": wait_timeout,
                    "disposition": "INLINE",
                },
            )
        except DatabricksSqlError as exc:
            if not self._should_retry_with_fresh_warehouse(exc):
                raise
            self._resolved_warehouse_id = None
            warehouse_id = await self.resolve_warehouse_id()
            return await self._request(
                "POST",
                "/api/2.0/sql/statements",
                json_payload={
                    "statement": statement,
                    "warehouse_id": warehouse_id,
                    "wait_timeout": wait_timeout,
                    "disposition": "INLINE",
                },
            )

    def _should_retry_with_fresh_warehouse(self, exc: DatabricksSqlError) -> bool:
        if self._resolved_warehouse_id is None:
            return False

        cursor: BaseException | None = exc
        while cursor is not None:
            message = str(cursor).lower()
            if "warehouse" in message and "not found" in message:
                return True
            cursor = cursor.__cause__
        return False


def _format_databricks_http_error(response: httpx.Response) -> str:
    status = response.status_code
    body_text = response.text.strip()
    if not body_text:
        return f"Databricks request failed with HTTP {status}."

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return f"Databricks request failed with HTTP {status}: {body_text[:500]}"

    if isinstance(payload, dict):
        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            error_payload = status_payload.get("error")
            if isinstance(error_payload, dict):
                message = str(error_payload.get("message", "")).strip()
                if message:
                    return f"Databricks request failed with HTTP {status}: {message}"
        message = str(payload.get("message", "")).strip()
        if message:
            return f"Databricks request failed with HTTP {status}: {message}"
        detail = str(payload.get("detail", "")).strip()
        if detail:
            return f"Databricks request failed with HTTP {status}: {detail}"
        if payload.get("error_code") is not None:
            return f"Databricks request failed with HTTP {status}: {json.dumps(payload, sort_keys=True)[:500]}"

    return f"Databricks request failed with HTTP {status}: {body_text[:500]}"
