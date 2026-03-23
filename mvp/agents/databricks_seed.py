"""
Idempotent Databricks bootstrap seeding for the secure Daily Account Planner.

This module runs from inside the private ACA environment and uses a non-human
Azure identity rather than seller OBO.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from databricks_admin import (
    DatabricksAdminClient,
    DatabricksAdminError,
    DatabricksAdminSettings,
)
from databricks_sql import DatabricksSqlClient, DatabricksSqlError, DatabricksSqlSettings, load_settings

_MODULE_DIR = Path(__file__).resolve().parent


def _resolve_root_dir() -> Path:
    for candidate in (_MODULE_DIR.parent, _MODULE_DIR):
        if (candidate / "infra").exists():
            return candidate
    return _MODULE_DIR.parent


_ROOT_DIR = _resolve_root_dir()
_DEFAULT_SQL_FILE = _ROOT_DIR / "infra" / "databricks" / "seed-databricks-ri.sql"
_DEFAULT_SEED_VERSION = "2026-03-secure-bootstrap-v2"
_DEFAULT_STATE_SCHEMA = "ri_ops"
_DEFAULT_STATE_TABLE = "bootstrap_state"
_DEFAULT_AUTH_MODE = "managed_identity"
_DEFAULT_BOOTSTRAP_REQUIRED_ENTITLEMENTS = ("workspace-access", "databricks-sql-access")
_DEFAULT_WORKSPACE_USERS = (
    "ri-test-na@m365cpi89838450.onmicrosoft.com,"
    "DaichiM@M365CPI89838450.OnMicrosoft.com"
)


class DatabricksSeedError(RuntimeError):
    """Raised when the secure Databricks seed flow fails."""


@dataclass(frozen=True)
class DatabricksSeedConfig:
    sql_file: Path
    catalog: str
    skip_catalog_create: bool
    seed_version: str
    state_schema: str
    state_table: str
    workspace_user_upns: tuple[str, ...]
    auth_mode: str
    arm_tenant_id: str | None
    arm_client_id: str | None
    arm_client_secret: str | None
    managed_identity_client_id: str | None
    managed_identity_principal_id: str | None
    bootstrap_principal_names: tuple[str, ...]
    warehouse_id: str | None

    @property
    def state_table_fqn(self) -> str:
        return f"{self.catalog}.{self.state_schema}.{self.state_table}"


def load_seed_config() -> DatabricksSeedConfig:
    sql_file_value = os.environ.get("DATABRICKS_SEED_SQL_FILE", "").strip()
    sql_file = Path(sql_file_value).expanduser() if sql_file_value else _DEFAULT_SQL_FILE

    auth_mode = os.environ.get("DATABRICKS_BOOTSTRAP_AUTH_MODE", _DEFAULT_AUTH_MODE).strip().lower() or _DEFAULT_AUTH_MODE
    if auth_mode not in {"azure_service_principal", "managed_identity"}:
        raise DatabricksSeedError(
            "DATABRICKS_BOOTSTRAP_AUTH_MODE must be azure_service_principal or managed_identity."
        )

    arm_tenant_id = os.environ.get("ARM_TENANT_ID", "").strip() or None
    arm_client_id = os.environ.get("ARM_CLIENT_ID", "").strip() or None
    arm_client_secret = os.environ.get("ARM_CLIENT_SECRET", "").strip() or None
    managed_identity_client_id = (
        os.environ.get("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "").strip()
        or os.environ.get("ARM_CLIENT_ID", "").strip()
        or None
    )
    managed_identity_principal_id = (
        os.environ.get("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_PRINCIPAL_ID", "").strip()
        or None
    )
    explicit_bootstrap_principal_name = (
        os.environ.get("DATABRICKS_BOOTSTRAP_PRINCIPAL_NAME", "").strip()
        or None
    )

    if auth_mode == "azure_service_principal" and not (arm_tenant_id and arm_client_id and arm_client_secret):
        raise DatabricksSeedError(
            "ARM_TENANT_ID, ARM_CLIENT_ID, and ARM_CLIENT_SECRET are required for azure_service_principal secure seeding."
        )
    if auth_mode == "managed_identity" and not managed_identity_client_id:
        raise DatabricksSeedError(
            "DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID or ARM_CLIENT_ID is required for managed_identity secure seeding."
        )
    bootstrap_principal_names: list[str] = []
    for candidate in (
        explicit_bootstrap_principal_name,
        managed_identity_client_id if auth_mode == "managed_identity" else arm_client_id,
        managed_identity_principal_id if auth_mode == "managed_identity" else None,
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in bootstrap_principal_names:
            bootstrap_principal_names.append(normalized)

    if not bootstrap_principal_names:
        raise DatabricksSeedError(
            "At least one Databricks bootstrap principal identifier is required for secure seeding."
        )

    workspace_user_upns = tuple(
        item.strip()
        for item in os.environ.get("DATABRICKS_WORKSPACE_USER_UPNS", _DEFAULT_WORKSPACE_USERS).split(",")
        if item.strip()
    )
    if not workspace_user_upns:
        raise DatabricksSeedError("At least one DATABRICKS_WORKSPACE_USER_UPNS value is required.")

    warehouse_id = (
        os.environ.get("DATABRICKS_BOOTSTRAP_WAREHOUSE_ID", "").strip()
        or os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or None
    )
    return DatabricksSeedConfig(
        sql_file=sql_file,
        catalog=os.environ.get("DATABRICKS_CATALOG", "veeam_demo").strip() or "veeam_demo",
        skip_catalog_create=_as_bool(os.environ.get("DATABRICKS_SKIP_CATALOG_CREATE")),
        seed_version=os.environ.get("DATABRICKS_SEED_VERSION", _DEFAULT_SEED_VERSION).strip() or _DEFAULT_SEED_VERSION,
        state_schema=os.environ.get("DATABRICKS_SEED_STATE_SCHEMA", _DEFAULT_STATE_SCHEMA).strip() or _DEFAULT_STATE_SCHEMA,
        state_table=os.environ.get("DATABRICKS_SEED_STATE_TABLE", _DEFAULT_STATE_TABLE).strip() or _DEFAULT_STATE_TABLE,
        workspace_user_upns=workspace_user_upns,
        auth_mode=auth_mode,
        arm_tenant_id=arm_tenant_id,
        arm_client_id=arm_client_id,
        arm_client_secret=arm_client_secret,
        managed_identity_client_id=managed_identity_client_id,
        managed_identity_principal_id=managed_identity_principal_id,
        bootstrap_principal_names=tuple(bootstrap_principal_names),
        warehouse_id=warehouse_id,
    )


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_statements(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False

    for raw_line in script.splitlines():
        stripped = raw_line.strip()
        if not current and (not stripped or stripped.startswith("--")):
            continue

        current.append(raw_line)
        for char in raw_line:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double

        if raw_line.rstrip().endswith(";") and not in_single and not in_double:
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement[:-1].strip())
            current = []

    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return [statement for statement in statements if statement]


def _quote_sql_string(value: str) -> str:
    return value.replace("'", "''")


def _is_catalog_create_statement(statement: str, catalog: str) -> bool:
    normalized_statement = " ".join(statement.strip().rstrip(";").split()).upper()
    normalized_catalog = catalog.strip().upper()
    return normalized_statement == f"CREATE CATALOG IF NOT EXISTS {normalized_catalog}"


def _render_seed_script(config: DatabricksSeedConfig) -> list[str]:
    if not config.sql_file.exists():
        raise DatabricksSeedError(f"Seed SQL file not found: {config.sql_file}")

    script = config.sql_file.read_text(encoding="utf-8")
    if config.catalog != "veeam_demo":
        script = script.replace("veeam_demo", config.catalog)
    statements = _split_statements(script)
    if config.skip_catalog_create:
        statements = [
            statement
            for statement in statements
            if not _is_catalog_create_statement(statement, config.catalog)
        ]
    return statements


async def _ensure_workspace_principals(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for user_upn in config.workspace_user_upns:
        results[user_upn] = await admin_client.ensure_workspace_user(user_upn)
    return results


async def _ensure_bootstrap_workspace_principal(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
) -> str | None:
    application_id: str | None
    if config.auth_mode == "managed_identity":
        application_id = config.managed_identity_client_id
    else:
        application_id = config.arm_client_id

    normalized_application_id = str(application_id or "").strip()
    if not normalized_application_id:
        return None

    return await admin_client.ensure_workspace_service_principal(
        normalized_application_id,
        display_name=config.bootstrap_principal_names[0] if config.bootstrap_principal_names else None,
        entitlements=_DEFAULT_BOOTSTRAP_REQUIRED_ENTITLEMENTS,
    )


async def _ensure_bootstrap_workspace_principal_entitlements(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
) -> dict[str, Any] | None:
    application_id: str | None
    if config.auth_mode == "managed_identity":
        application_id = config.managed_identity_client_id
    else:
        application_id = config.arm_client_id

    normalized_application_id = str(application_id or "").strip()
    if not normalized_application_id:
        return None

    return await admin_client.ensure_workspace_service_principal_entitlements(
        normalized_application_id,
        required_entitlements=_DEFAULT_BOOTSTRAP_REQUIRED_ENTITLEMENTS,
    )


async def _ensure_bootstrap_warehouse_access(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
    warehouse_id: str,
) -> dict[str, Any]:
    if not warehouse_id or not config.bootstrap_principal_names:
        raise DatabricksSeedError(
            "Databricks bootstrap warehouse access requires a warehouse id and at least one bootstrap principal identifier."
        )
    applied_principals: list[str] = []
    errors: list[str] = []
    for principal_name in config.bootstrap_principal_names:
        try:
            await admin_client.ensure_sql_warehouse_permission(
                warehouse_id,
                principal_name,
            )
            applied_principals.append(principal_name)
        except DatabricksAdminError as exc:
            errors.append(f"{principal_name}: {exc}")

    if applied_principals:
        return {
            "status": "applied",
            "applied_principals": applied_principals,
            "errors": errors,
        }

    # Some workspaces do not expose a mutable warehouse-permissions endpoint for
    # this identity. In that case we continue and let the SQL bootstrap path be
    # the source of truth for effective warehouse access.
    if errors and all("HTTP 404" in error for error in errors):
        return {
            "status": "skipped",
            "reason": "permissions_endpoint_not_available",
            "applied_principals": [],
            "errors": errors,
        }

    attempted = ", ".join(config.bootstrap_principal_names)
    detail = "; ".join(errors) if errors else "no successful warehouse permission assignments"
    raise DatabricksSeedError(
        "Failed to grant Databricks SQL warehouse access for bootstrap principal identifiers "
        f"[{attempted}]: {detail}"
    )


async def _ensure_catalog_exists(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> None:
    await client.execute(f"CREATE CATALOG IF NOT EXISTS {config.catalog}")


async def _ensure_state_storage(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> None:
    await client.execute(f"CREATE SCHEMA IF NOT EXISTS {config.catalog}.{config.state_schema}")
    await client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {config.state_table_fqn} (
          seed_name STRING NOT NULL,
          seed_version STRING NOT NULL,
          applied_at TIMESTAMP NOT NULL,
          applied_by STRING NOT NULL,
          details STRING
        )
        """
    )


async def _seed_already_current(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> bool:
    rows = await client.execute(
        f"""
        SELECT seed_version
        FROM {config.state_table_fqn}
        WHERE seed_name = 'daily_account_planner_secure'
        ORDER BY applied_at DESC
        LIMIT 1
        """
    )
    if not rows:
        return False
    return str(rows[0].get("seed_version", "")).strip() == config.seed_version


async def _record_seed_success(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> None:
    details = json.dumps(
        {
            "catalog": config.catalog,
            "workspace_users": list(config.workspace_user_upns),
        },
        sort_keys=True,
    )
    await client.execute(
        f"""
        DELETE FROM {config.state_table_fqn}
        WHERE seed_name = 'daily_account_planner_secure'
        """
    )
    await client.execute(
        f"""
        INSERT INTO {config.state_table_fqn}
        VALUES (
          'daily_account_planner_secure',
          '{_quote_sql_string(config.seed_version)}',
          current_timestamp(),
          session_user(),
          '{_quote_sql_string(details)}'
        )
        """
    )


async def _validate_seed_output(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> None:
    rows = await client.execute(
        f"""
        SELECT 'accounts' AS object_name, COUNT(*) AS row_count FROM {config.catalog}.ri.accounts
        UNION ALL SELECT 'reps', COUNT(*) FROM {config.catalog}.ri.reps
        UNION ALL SELECT 'opportunities', COUNT(*) FROM {config.catalog}.ri.opportunities
        UNION ALL SELECT 'contacts', COUNT(*) FROM {config.catalog}.ri.contacts
        UNION ALL SELECT 'entitlements', COUNT(*) FROM {config.catalog}.ri_security.user_territory_entitlements
        """
    )
    counts = {str(row.get("object_name")): int(row.get("row_count") or 0) for row in rows}
    missing = [
        name
        for name in ("accounts", "reps", "opportunities", "contacts", "entitlements")
        if counts.get(name, 0) <= 0
    ]
    if missing:
        raise DatabricksSeedError(
            "Seed completed but base tables or entitlements are missing expected data: "
            + ", ".join(sorted(missing))
        )

    view_rows = await client.execute(f"SHOW TABLES IN {config.catalog}.ri_secure")
    available_views = {
        str(row.get("tableName", "") or row.get("table_name", "")).strip().lower()
        for row in view_rows
    }
    required_views = {"accounts", "reps", "opportunities", "contacts"}
    missing_views = sorted(view_name for view_name in required_views if view_name not in available_views)
    if missing_views:
        raise DatabricksSeedError(
            "Seed completed but secure views are missing: " + ", ".join(missing_views)
        )


def _build_bootstrap_credential(config: DatabricksSeedConfig):
    if config.auth_mode == "managed_identity":
        return ManagedIdentityCredential(client_id=config.managed_identity_client_id)
    return ClientSecretCredential(
        tenant_id=str(config.arm_tenant_id),
        client_id=str(config.arm_client_id),
        client_secret=str(config.arm_client_secret),
    )


async def run_secure_seed() -> dict[str, Any]:
    config = load_seed_config()
    base_settings = load_settings()
    settings = DatabricksSqlSettings(
        host=base_settings.host,
        token_scope=base_settings.token_scope,
        azure_management_scope=base_settings.azure_management_scope,
        azure_workspace_resource_id=base_settings.azure_workspace_resource_id,
        warehouse_id=config.warehouse_id or base_settings.warehouse_id,
        timeout_seconds=base_settings.timeout_seconds,
        retry_count=base_settings.retry_count,
        poll_attempts=base_settings.poll_attempts,
        poll_interval_seconds=base_settings.poll_interval_seconds,
        pat=None,
    )
    credential = _build_bootstrap_credential(config)
    client = DatabricksSqlClient(settings=settings, credential=credential)
    admin_client = DatabricksAdminClient(
        settings=DatabricksAdminSettings.from_sql_settings(settings),
        credential=credential,
    )
    try:
        principal_results = await _ensure_workspace_principals(admin_client, config)
        bootstrap_service_principal_result = await _ensure_bootstrap_workspace_principal(
            admin_client,
            config,
        )
        bootstrap_service_principal_entitlement_result = (
            await _ensure_bootstrap_workspace_principal_entitlements(
                admin_client,
                config,
            )
        )
        resolved_warehouse_id = config.warehouse_id or await client.resolve_warehouse_id()
        warehouse_permission_result = await _ensure_bootstrap_warehouse_access(
            admin_client,
            config,
            resolved_warehouse_id,
        )
        if not config.skip_catalog_create:
            await _ensure_catalog_exists(client, config)
        await _ensure_state_storage(client, config)
        if await _seed_already_current(client, config):
            return {
                "auth_mode": config.auth_mode,
                "catalog": config.catalog,
                "principal_results": principal_results,
                "bootstrap_service_principal_result": bootstrap_service_principal_result,
                "bootstrap_service_principal_entitlement_result": bootstrap_service_principal_entitlement_result,
                "warehouse_permission_result": warehouse_permission_result,
                "status": "skipped",
                "reason": "already_current",
                "seed_version": config.seed_version,
                "skip_catalog_create": config.skip_catalog_create,
                "warehouse_id": resolved_warehouse_id,
            }

        statements = _render_seed_script(config)
        for statement in statements:
            await client.execute(statement)

        await _validate_seed_output(client, config)
        await _record_seed_success(client, config)
        return {
            "auth_mode": config.auth_mode,
            "catalog": config.catalog,
            "principal_results": principal_results,
            "bootstrap_service_principal_result": bootstrap_service_principal_result,
            "bootstrap_service_principal_entitlement_result": bootstrap_service_principal_entitlement_result,
            "warehouse_permission_result": warehouse_permission_result,
            "status": "seeded",
            "seed_version": config.seed_version,
            "skip_catalog_create": config.skip_catalog_create,
            "statement_count": len(statements),
            "warehouse_id": resolved_warehouse_id,
        }
    except DatabricksSqlError as exc:
        raise DatabricksSeedError(str(exc)) from exc
    except DatabricksAdminError as exc:
        raise DatabricksSeedError(str(exc)) from exc
    finally:
        await admin_client.close()
        await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(run_secure_seed()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
