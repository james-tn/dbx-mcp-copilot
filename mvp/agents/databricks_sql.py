"""
Direct Databricks SQL helper for the Daily Account Planner.

The planner executes a small set of app-owned canned queries through the
Databricks SQL Statements API. Agents never receive a dynamic SQL capability.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import AzureCliCredential, DefaultAzureCredential

_DATABRICKS_AUDIENCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
_DEFAULT_HOST = "https://adb-7405610222366876.16.azuredatabricks.net"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_RETRY_COUNT = 1
_DEFAULT_POLL_ATTEMPTS = 6
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0


class DatabricksSqlError(RuntimeError):
    """Base error for Databricks SQL execution."""


class DatabricksSqlAuthError(DatabricksSqlError):
    """Raised when Databricks authentication fails."""


@dataclass(frozen=True)
class DatabricksSqlSettings:
    host: str
    token_scope: str
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
        token = await asyncio.to_thread(self.credential.get_token, self.settings.token_scope)
        return {"Authorization": f"Bearer {token.token}"}

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
                    raise DatabricksSqlAuthError("Databricks SQL authentication failed.")
                response.raise_for_status()
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

    async def _resolve_warehouse_id(self) -> str:
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

    async def query_sql(self, statement: str) -> list[dict[str, Any]]:
        warehouse_id = await self._resolve_warehouse_id()
        payload = await self._request(
            "POST",
            "/api/2.0/sql/statements",
            json_payload={
                "statement": statement,
                "warehouse_id": warehouse_id,
                "wait_timeout": "0s",
                "disposition": "INLINE",
            },
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
