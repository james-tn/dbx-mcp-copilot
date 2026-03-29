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

from azure.identity import AzureCliCredential, ClientSecretCredential, ManagedIdentityCredential

from databricks_admin import (
    DatabricksAdminClient,
    DatabricksAdminError,
    DatabricksAdminSettings,
)
from customer_scope_seed import default_scope_workbook_path, load_scope_workbook_rows, render_mock_customer_seed_sql
from databricks_sql import DatabricksSqlClient, DatabricksSqlError, DatabricksSqlSettings, load_settings
from infra.bootstrap_helpers import derive_demo_users, render_seed_sql_template

_MODULE_DIR = Path(__file__).resolve().parent


def _resolve_root_dir() -> Path:
    for candidate in (_MODULE_DIR.parent, _MODULE_DIR):
        if (candidate / "infra").exists():
            return candidate
    return _MODULE_DIR.parent


_ROOT_DIR = _resolve_root_dir()
_DEFAULT_SQL_FILE = _ROOT_DIR / "infra" / "databricks" / "seed-databricks-aiq-dev.sql"
_DEFAULT_SEED_VERSION = "2026-03-secure-bootstrap-v2"
_DEFAULT_STATE_SCHEMA = "ri_ops"
_DEFAULT_STATE_TABLE = "bootstrap_state"
_DEFAULT_AUTH_MODE = "managed_identity"
_DEFAULT_WORKSPACE_USER_REQUIRED_ENTITLEMENTS = ("workspace-access", "databricks-sql-access")
_DEFAULT_BOOTSTRAP_REQUIRED_ENTITLEMENTS = ("workspace-access", "databricks-sql-access")
_LEGACY_METASTORE_CATALOG = "spark_catalog"


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
    seller_a_upn: str
    seller_b_upn: str
    workspace_user_upns: tuple[str, ...]
    auth_mode: str
    arm_tenant_id: str | None
    arm_client_id: str | None
    arm_client_secret: str | None
    managed_identity_client_id: str | None
    managed_identity_principal_id: str | None
    bootstrap_principal_names: tuple[str, ...]
    warehouse_id: str | None
    source_objects: tuple[str, ...]

    @property
    def state_table_fqn(self) -> str:
        return f"{self.catalog}.{self.state_schema}.{self.state_table}"


def load_seed_config() -> DatabricksSeedConfig:
    sql_file_value = os.environ.get("DATABRICKS_SEED_SQL_FILE", "").strip()
    sql_file = Path(sql_file_value).expanduser() if sql_file_value else _DEFAULT_SQL_FILE

    auth_mode = os.environ.get("DATABRICKS_BOOTSTRAP_AUTH_MODE", _DEFAULT_AUTH_MODE).strip().lower() or _DEFAULT_AUTH_MODE
    if auth_mode not in {"azure_service_principal", "managed_identity", "azure_cli"}:
        raise DatabricksSeedError(
            "DATABRICKS_BOOTSTRAP_AUTH_MODE must be azure_service_principal, managed_identity, or azure_cli."
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

    if auth_mode != "azure_cli" and not bootstrap_principal_names:
        raise DatabricksSeedError(
            "At least one Databricks bootstrap principal identifier is required for secure seeding."
        )

    seller_a_upn, seller_b_upn = derive_demo_users(dict(os.environ))
    if not seller_a_upn or not seller_b_upn:
        raise DatabricksSeedError(
            "SELLER_A_UPN and SELLER_B_UPN, or DATABRICKS_WORKSPACE_USER_UPNS with two users, are required."
        )

    workspace_user_upns = tuple(
        item.strip()
        for item in os.environ.get("DATABRICKS_WORKSPACE_USER_UPNS", f"{seller_a_upn},{seller_b_upn}").split(",")
        if item.strip()
    )
    if not workspace_user_upns:
        raise DatabricksSeedError("At least one DATABRICKS_WORKSPACE_USER_UPNS value is required.")

    warehouse_id = (
        os.environ.get("DATABRICKS_BOOTSTRAP_WAREHOUSE_ID", "").strip()
        or os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or None
    )
    source_objects = _load_source_objects()
    return DatabricksSeedConfig(
        sql_file=sql_file,
        catalog=os.environ.get("DATABRICKS_CATALOG", "veeam_demo").strip() or "veeam_demo",
        skip_catalog_create=_as_bool(os.environ.get("DATABRICKS_SKIP_CATALOG_CREATE")),
        seed_version=os.environ.get("DATABRICKS_SEED_VERSION", _DEFAULT_SEED_VERSION).strip() or _DEFAULT_SEED_VERSION,
        state_schema=os.environ.get("DATABRICKS_SEED_STATE_SCHEMA", _DEFAULT_STATE_SCHEMA).strip() or _DEFAULT_STATE_SCHEMA,
        state_table=os.environ.get("DATABRICKS_SEED_STATE_TABLE", _DEFAULT_STATE_TABLE).strip() or _DEFAULT_STATE_TABLE,
        seller_a_upn=seller_a_upn,
        seller_b_upn=seller_b_upn,
        workspace_user_upns=workspace_user_upns,
        auth_mode=auth_mode,
        arm_tenant_id=arm_tenant_id,
        arm_client_id=arm_client_id,
        arm_client_secret=arm_client_secret,
        managed_identity_client_id=managed_identity_client_id,
        managed_identity_principal_id=managed_identity_principal_id,
        bootstrap_principal_names=tuple(bootstrap_principal_names),
        warehouse_id=warehouse_id,
        source_objects=source_objects,
    )


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_source_objects() -> tuple[str, ...]:
    configured = os.environ.get("DATABRICKS_ACCESS_GRANT_SOURCES", "").strip()
    if configured:
        candidates = configured.split(",")
    else:
        scope_catalog = (
            os.environ.get("CUSTOMER_SCOPE_ACCOUNTS_CATALOG", "").strip()
            or os.environ.get("CUSTOMER_SALES_TEAM_MAPPING_CATALOG", "").strip()
            or os.environ.get("DATABRICKS_CATALOG", "").strip()
        )
        vpower_sources = []
        if scope_catalog:
            vpower_sources = [
                f"{scope_catalog}.sf_vpower_bronze.account",
                f"{scope_catalog}.sf_vpower_bronze.objectterritory2association",
                f"{scope_catalog}.sf_vpower_bronze.territory2",
                f"{scope_catalog}.sf_vpower_bronze.userterritory2association",
                f"{scope_catalog}.sf_vpower_bronze.user",
            ]
        candidates = [
            os.environ.get("TOP_OPPORTUNITIES_SOURCE", "").strip()
            or os.environ.get("CUSTOMER_TOP_OPPORTUNITIES_SOURCE", "").strip(),
            os.environ.get("CONTACTS_SOURCE", "").strip()
            or os.environ.get("CUSTOMER_CONTACTS_SOURCE", "").strip(),
            *vpower_sources,
        ]

    source_objects: list[str] = []
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in source_objects:
            source_objects.append(normalized)
    return tuple(source_objects)


async def _resolve_bootstrap_warehouse_id(
    client: DatabricksSqlClient,
    configured_warehouse_id: str | None,
) -> str:
    if configured_warehouse_id:
        client._resolved_warehouse_id = configured_warehouse_id
        return configured_warehouse_id

    payload = await client._request("GET", "/api/2.0/sql/warehouses")
    warehouses = payload.get("warehouses", [])
    if isinstance(warehouses, list):
        for warehouse in warehouses:
            if not isinstance(warehouse, dict):
                continue
            state = str(warehouse.get("state", "")).upper()
            if state in {"RUNNING", "STARTING", "STARTED"}:
                warehouse_id = str(warehouse.get("id", "")).strip()
                if warehouse_id:
                    client._resolved_warehouse_id = warehouse_id
                    return warehouse_id
        for warehouse in warehouses:
            if not isinstance(warehouse, dict):
                continue
            warehouse_id = str(warehouse.get("id", "")).strip()
            if warehouse_id:
                client._resolved_warehouse_id = warehouse_id
                return warehouse_id

    if not _as_bool(os.environ.get("DATABRICKS_AUTO_CREATE_WAREHOUSE", "true")):
        raise DatabricksSeedError(
            "No Databricks SQL warehouse was found. Set DATABRICKS_WAREHOUSE_ID, create a SQL warehouse in the workspace, "
            "or enable DATABRICKS_AUTO_CREATE_WAREHOUSE=true before rerunning secure seeding."
        )

    create_payload = {
        "name": (
            os.environ.get("DATABRICKS_WAREHOUSE_NAME", "").strip()
            or f"{os.environ.get('INFRA_NAME_PREFIX', 'dailyacctplanner')}-sql"
        ),
        "cluster_size": os.environ.get("DATABRICKS_WAREHOUSE_CLUSTER_SIZE", "Small").strip() or "Small",
        "min_num_clusters": int(os.environ.get("DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS", "1")),
        "max_num_clusters": int(os.environ.get("DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS", "1")),
        "auto_stop_mins": int(os.environ.get("DATABRICKS_WAREHOUSE_AUTO_STOP_MINS", "10")),
        "warehouse_type": os.environ.get("DATABRICKS_WAREHOUSE_TYPE", "PRO").strip() or "PRO",
    }
    if _as_bool(os.environ.get("DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS")):
        create_payload["enable_serverless_compute"] = True

    try:
        created = await client._request(
            "POST",
            "/api/2.0/sql/warehouses",
            json_payload=create_payload,
        )
    except DatabricksSqlError as exc:
        raise DatabricksSeedError(
            "No Databricks SQL warehouse was found, and automatic warehouse creation failed. "
            "Ensure the bootstrap identity can create SQL warehouses in Databricks, or set DATABRICKS_WAREHOUSE_ID to an existing warehouse."
        ) from exc

    warehouse_id = str(created.get("id") or created.get("warehouse_id") or "").strip()
    if not warehouse_id:
        raise DatabricksSeedError(
            "Databricks returned a successful warehouse-create response without a warehouse id. "
            "Create a warehouse manually and set DATABRICKS_WAREHOUSE_ID before rerunning secure seeding."
        )

    client._resolved_warehouse_id = warehouse_id
    return warehouse_id


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


def _quote_sql_principal(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _parse_source_object(source: str) -> tuple[str, str, str]:
    parts = [item.strip() for item in source.split(".") if item.strip()]
    if len(parts) != 3:
        raise DatabricksSeedError(
            f"Databricks source object '{source}' must use catalog.schema.table format."
        )
    return parts[0], parts[1], parts[2]


def _build_manual_grant_sql(config: DatabricksSeedConfig) -> list[str]:
    statements: list[str] = []
    for source in config.source_objects:
        catalog, schema, table = _parse_source_object(source)
        for user_upn in config.workspace_user_upns:
            principal = _quote_sql_principal(user_upn)
            statements.extend(
                [
                    f"GRANT USE CATALOG ON CATALOG {catalog} TO {principal};",
                    f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO {principal};",
                    f"GRANT SELECT ON TABLE {catalog}.{schema}.{table} TO {principal};",
                ]
            )
    return statements


def _is_catalog_create_statement(statement: str, catalog: str) -> bool:
    normalized_statement = " ".join(statement.strip().rstrip(";").split()).upper()
    normalized_catalog = catalog.strip().upper()
    return normalized_statement == f"CREATE CATALOG IF NOT EXISTS {normalized_catalog}"


def _render_seed_script(config: DatabricksSeedConfig) -> list[str]:
    if not config.sql_file.exists():
        raise DatabricksSeedError(f"Seed SQL file not found: {config.sql_file}")

    script = config.sql_file.read_text(encoding="utf-8")
    script = render_seed_sql_template(script, config.seller_a_upn, config.seller_b_upn)
    scope_workbook = os.environ.get("CUSTOMER_SCOPE_SEED_WORKBOOK", "").strip()
    scope_rows = load_scope_workbook_rows(scope_workbook or default_scope_workbook_path())
    script = f"{script.rstrip()}\n\n{render_mock_customer_seed_sql(scope_rows)}\n"
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


async def _ensure_workspace_user_entitlements(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for user_upn in config.workspace_user_upns:
        results[user_upn] = await admin_client.ensure_workspace_user_entitlements(
            user_upn,
            required_entitlements=_DEFAULT_WORKSPACE_USER_REQUIRED_ENTITLEMENTS,
        )
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


async def _ensure_sql_warehouse_access(
    admin_client: DatabricksAdminClient,
    config: DatabricksSeedConfig,
    warehouse_id: str,
) -> dict[str, Any]:
    seller_principals = [
        ("user", user_upn)
        for user_upn in config.workspace_user_upns
        if str(user_upn).strip()
    ]
    bootstrap_principals = [
        ("service_principal", principal_name)
        for principal_name in config.bootstrap_principal_names
        if str(principal_name).strip()
    ]
    principal_specs = seller_principals + bootstrap_principals
    if not warehouse_id or not principal_specs:
        raise DatabricksSeedError(
            "Databricks SQL warehouse access requires a warehouse id and at least one seller or bootstrap principal identifier."
        )
    applied_principals: list[str] = []
    errors: list[str] = []
    for principal_type, principal_name in principal_specs:
        try:
            await admin_client.ensure_sql_warehouse_permission(
                warehouse_id,
                principal_name,
                principal_type=principal_type,
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

    attempted = ", ".join(principal_name for _, principal_name in principal_specs)
    detail = "; ".join(errors) if errors else "no successful warehouse permission assignments"
    raise DatabricksSeedError(
        "Failed to grant Databricks SQL warehouse access for seller or bootstrap principal identifiers "
        f"[{attempted}]: {detail}"
    )


async def _ensure_source_object_permissions(
    client: DatabricksSqlClient,
    config: DatabricksSeedConfig,
) -> dict[str, Any]:
    if not config.source_objects:
        return {
            "status": "skipped",
            "reason": "no_source_objects",
            "source_objects": [],
            "granted_principals": [],
        }

    granted_pairs: list[dict[str, str]] = []
    errors: list[str] = []
    for source in config.source_objects:
        catalog, schema, table = _parse_source_object(source)
        for user_upn in config.workspace_user_upns:
            principal = _quote_sql_principal(user_upn)
            statements = (
                f"GRANT USE CATALOG ON CATALOG {catalog} TO {principal}",
                f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO {principal}",
                f"GRANT SELECT ON TABLE {catalog}.{schema}.{table} TO {principal}",
            )
            try:
                for statement in statements:
                    await client.execute(statement)
                granted_pairs.append({"source": source, "principal": user_upn})
            except DatabricksSqlError as exc:
                errors.append(f"{source} -> {user_upn}: {exc}")

    if errors:
        manual_sql = "\n".join(_build_manual_grant_sql(config))
        raise DatabricksSeedError(
            "Failed to grant Databricks Unity Catalog access for one or more planner seller principals. "
            "Run the following SQL as a Databricks catalog or metastore admin, then rerun the bootstrap:\n"
            f"{manual_sql}\n"
            f"Errors: {'; '.join(errors)}"
        )

    return {
        "status": "applied",
        "source_objects": list(config.source_objects),
        "granted_principals": list(config.workspace_user_upns),
        "grant_count": len(granted_pairs),
    }


async def _ensure_catalog_exists(client: DatabricksSqlClient, config: DatabricksSeedConfig) -> None:
    await client.execute(f"CREATE CATALOG IF NOT EXISTS {config.catalog}")


async def _validate_catalog_namespace_support(
    client: DatabricksSqlClient,
    config: DatabricksSeedConfig,
) -> None:
    try:
        rows = await client.execute("SHOW CATALOGS")
    except DatabricksSqlError as exc:
        if _is_single_part_namespace_error(str(exc)):
            raise DatabricksSeedError(_build_unity_catalog_error_message(config)) from exc
        raise

    available_catalogs = _extract_catalog_names(rows)
    if available_catalogs == {_LEGACY_METASTORE_CATALOG}:
        raise DatabricksSeedError(_build_unity_catalog_error_message(config))

    normalized_configured_catalog = config.catalog.strip().lower()
    if (
        config.skip_catalog_create
        and available_catalogs
        and normalized_configured_catalog not in available_catalogs
    ):
        raise DatabricksSeedError(
            f"Configured Databricks catalog '{config.catalog}' is not visible from the SQL warehouse used for secure seeding. "
            f"Available catalogs: {', '.join(sorted(available_catalogs))}. Verify DATABRICKS_CATALOG, the workspace metastore "
            "attachment, and the SQL warehouse permissions before rerunning the seed."
        )


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
        SELECT 'account_iq_scores' AS object_name, COUNT(*) AS row_count FROM {config.catalog}.data_science_account_iq_gold.account_iq_scores
        UNION ALL SELECT 'aiq_contact', COUNT(*) FROM {config.catalog}.account_iq_gold.aiq_contact
        UNION ALL SELECT 'vpower_account', COUNT(*) FROM {config.catalog}.sf_vpower_bronze.account
        UNION ALL SELECT 'objectterritory2association', COUNT(*) FROM {config.catalog}.sf_vpower_bronze.objectterritory2association
        UNION ALL SELECT 'territory2', COUNT(*) FROM {config.catalog}.sf_vpower_bronze.territory2
        UNION ALL SELECT 'userterritory2association', COUNT(*) FROM {config.catalog}.sf_vpower_bronze.userterritory2association
        UNION ALL SELECT 'user', COUNT(*) FROM {config.catalog}.sf_vpower_bronze.`user`
        """
    )
    counts = {str(row.get("object_name")): int(row.get("row_count") or 0) for row in rows}
    missing = [
        name
        for name in (
            "account_iq_scores",
            "aiq_contact",
            "vpower_account",
            "objectterritory2association",
            "territory2",
            "userterritory2association",
            "user",
        )
        if counts.get(name, 0) <= 0
    ]
    if missing:
        raise DatabricksSeedError(
            "Seed completed but the AIQ or customer-scope mock tables are missing expected data: "
            + ", ".join(sorted(missing))
        )


def _build_bootstrap_credential(config: DatabricksSeedConfig):
    if config.auth_mode == "azure_cli":
        return AzureCliCredential()
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
        workspace_user_entitlement_results = await _ensure_workspace_user_entitlements(
            admin_client,
            config,
        )
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
        resolved_warehouse_id = await _resolve_bootstrap_warehouse_id(client, config.warehouse_id)
        warehouse_permission_result = await _ensure_sql_warehouse_access(
            admin_client,
            config,
            resolved_warehouse_id,
        )
        await _validate_catalog_namespace_support(client, config)
        if not config.skip_catalog_create:
            await _ensure_catalog_exists(client, config)
        await _ensure_state_storage(client, config)
        if await _seed_already_current(client, config):
            source_permission_result = await _ensure_source_object_permissions(client, config)
            return {
                "auth_mode": config.auth_mode,
                "catalog": config.catalog,
                "principal_results": principal_results,
                "workspace_user_entitlement_results": workspace_user_entitlement_results,
                "bootstrap_service_principal_result": bootstrap_service_principal_result,
                "bootstrap_service_principal_entitlement_result": bootstrap_service_principal_entitlement_result,
                "warehouse_permission_result": warehouse_permission_result,
                "source_permission_result": source_permission_result,
                "status": "skipped",
                "reason": "already_current",
                "seed_version": config.seed_version,
                "skip_catalog_create": config.skip_catalog_create,
                "warehouse_id": resolved_warehouse_id,
            }

        statements = _render_seed_script(config)
        for statement in statements:
            await client.execute(statement)

        source_permission_result = await _ensure_source_object_permissions(client, config)
        await _validate_seed_output(client, config)
        await _record_seed_success(client, config)
        return {
            "auth_mode": config.auth_mode,
            "catalog": config.catalog,
            "principal_results": principal_results,
            "workspace_user_entitlement_results": workspace_user_entitlement_results,
            "bootstrap_service_principal_result": bootstrap_service_principal_result,
            "bootstrap_service_principal_entitlement_result": bootstrap_service_principal_entitlement_result,
            "warehouse_permission_result": warehouse_permission_result,
            "source_permission_result": source_permission_result,
            "status": "seeded",
            "seed_version": config.seed_version,
            "skip_catalog_create": config.skip_catalog_create,
            "statement_count": len(statements),
            "warehouse_id": resolved_warehouse_id,
        }
    except DatabricksSqlError as exc:
        if _is_single_part_namespace_error(str(exc)):
            raise DatabricksSeedError(_build_unity_catalog_error_message(config)) from exc
        raise DatabricksSeedError(str(exc)) from exc
    except DatabricksAdminError as exc:
        raise DatabricksSeedError(str(exc)) from exc
    finally:
        await admin_client.close()
        await client.close()


async def run_secure_access_bootstrap() -> dict[str, Any]:
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
        workspace_user_entitlement_results = await _ensure_workspace_user_entitlements(
            admin_client,
            config,
        )
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
        resolved_warehouse_id = await _resolve_bootstrap_warehouse_id(client, config.warehouse_id)
        warehouse_permission_result = await _ensure_sql_warehouse_access(
            admin_client,
            config,
            resolved_warehouse_id,
        )
        source_permission_result = await _ensure_source_object_permissions(client, config)
        return {
            "auth_mode": config.auth_mode,
            "principal_results": principal_results,
            "workspace_user_entitlement_results": workspace_user_entitlement_results,
            "bootstrap_service_principal_result": bootstrap_service_principal_result,
            "bootstrap_service_principal_entitlement_result": bootstrap_service_principal_entitlement_result,
            "warehouse_permission_result": warehouse_permission_result,
            "source_permission_result": source_permission_result,
            "status": "bootstrapped",
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


def _extract_catalog_names(rows: list[dict[str, Any]]) -> set[str]:
    catalog_names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("catalog", "catalog_name", "name"):
            raw_value = row.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                catalog_names.add(raw_value.strip().lower())
                break
    return catalog_names


def _is_single_part_namespace_error(error_message: str) -> bool:
    normalized = error_message.lower()
    return (
        "requires_single_part_namespace" in normalized
        or ("spark_catalog" in normalized and "single-part namespace" in normalized)
    )


def _build_unity_catalog_error_message(config: DatabricksSeedConfig) -> str:
    return (
        f"Secure Databricks seed requires a Unity Catalog-capable SQL warehouse, but the current workspace or warehouse "
        f"is resolving '{config.catalog}' against legacy spark_catalog semantics. Verify the workspace is attached to a "
        "Unity Catalog metastore, the selected SQL warehouse can see the configured catalog, and DATABRICKS_CATALOG points "
        "to a real catalog before rerunning the seed."
    )


if __name__ == "__main__":
    main()
