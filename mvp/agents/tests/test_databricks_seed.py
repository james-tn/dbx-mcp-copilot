from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mcp_server import databricks_seed

SELLER_A_UPN = "seller-a@example.com"
SELLER_B_UPN = "seller-b@example.com"


@pytest.fixture(autouse=True)
def _demo_sellers(monkeypatch) -> None:
    monkeypatch.setenv("SELLER_A_UPN", SELLER_A_UPN)
    monkeypatch.setenv("SELLER_B_UPN", SELLER_B_UPN)
    monkeypatch.setenv("DATABRICKS_WORKSPACE_USER_UPNS", f"{SELLER_A_UPN},{SELLER_B_UPN}")


def test_load_seed_config_requires_managed_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(tmp_path / "seed.sql"))
    monkeypatch.delenv("ARM_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")

    with pytest.raises(databricks_seed.DatabricksSeedError, match="managed_identity"):
        databricks_seed.load_seed_config()


def test_load_seed_config_allows_missing_warehouse_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(tmp_path / "seed.sql"))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.delenv("DATABRICKS_BOOTSTRAP_WAREHOUSE_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

    config = databricks_seed.load_seed_config()

    assert config.warehouse_id is None


def test_load_seed_config_includes_managed_identity_principal_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(tmp_path / "seed.sql"))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_PRINCIPAL_ID", "mi-principal")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")

    config = databricks_seed.load_seed_config()

    assert config.bootstrap_principal_names == ("mi-client", "mi-principal")


def test_resolve_bootstrap_warehouse_id_creates_when_missing(monkeypatch) -> None:
    monkeypatch.setenv("INFRA_NAME_PREFIX", "dailyacctplannersec")
    monkeypatch.setenv("DATABRICKS_AUTO_CREATE_WAREHOUSE", "true")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_CLUSTER_SIZE", "Small")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict | None]] = []
            self._resolved_warehouse_id: str | None = None

        async def _request(self, method: str, path: str, json_payload=None):
            self.calls.append((method, path, json_payload))
            if method == "GET":
                return {"warehouses": []}
            return {"id": "warehouse-created"}

    client = FakeClient()

    warehouse_id = asyncio.run(databricks_seed._resolve_bootstrap_warehouse_id(client, None))

    assert warehouse_id == "warehouse-created"
    assert client._resolved_warehouse_id == "warehouse-created"
    assert client.calls == [
        ("GET", "/api/2.0/sql/warehouses", None),
        (
            "POST",
            "/api/2.0/sql/warehouses",
            {
                "name": "dailyacctplannersec-sql",
                "cluster_size": "Small",
                "min_num_clusters": 1,
                "max_num_clusters": 1,
                "auto_stop_mins": 10,
                "warehouse_type": "PRO",
            },
        ),
    ]


def test_split_statements_skips_comments_and_keeps_tail() -> None:
    statements = databricks_seed._split_statements(
        """
        -- comment
        CREATE TABLE foo (id INT);
        INSERT INTO foo VALUES (1);
        SELECT * FROM foo
        """
    )

    assert statements == [
        "CREATE TABLE foo (id INT)",
        "INSERT INTO foo VALUES (1)",
        "SELECT * FROM foo",
    ]


def test_render_seed_script_removes_catalog_create_when_skipping(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text(
        """
        CREATE CATALOG IF NOT EXISTS veeam_demo;
        CREATE SCHEMA IF NOT EXISTS veeam_demo.ri;
        SELECT * FROM veeam_demo.ri.accounts;
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_CATALOG", "workspace_catalog")
    monkeypatch.setenv("DATABRICKS_SKIP_CATALOG_CREATE", "true")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")

    config = databricks_seed.load_seed_config()
    statements = databricks_seed._render_seed_script(config)

    assert "CREATE CATALOG IF NOT EXISTS workspace_catalog" not in statements
    assert "CREATE SCHEMA IF NOT EXISTS workspace_catalog.ri" in statements
    assert "SELECT * FROM workspace_catalog.ri.accounts" in statements


def test_run_secure_seed_skips_when_current(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    monkeypatch.setenv("DATABRICKS_SEED_VERSION", "v-current")

    executed: list[str] = []
    ensured_users: list[str] = []
    ensured_user_entitlements: list[tuple[str, tuple[str, ...]]] = []
    ensured_service_principals: list[str] = []
    ensured_service_principal_entitlements: list[tuple[str, tuple[str, ...]]] = []
    warehouse_permissions: list[tuple[str, str, str | None]] = []

    class FakeClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def execute(self, statement: str):
            executed.append(" ".join(statement.split()))
            if "SELECT seed_version" in statement:
                return [{"seed_version": "v-current"}]
            return []

        async def resolve_warehouse_id(self) -> str:
            return "wh-1"

        async def close(self) -> None:
            return None

    class FakeAdminClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def ensure_workspace_user(self, user_upn: str) -> str:
            ensured_users.append(user_upn)
            return "existing"

        async def ensure_workspace_user_entitlements(
            self,
            user_upn: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_user_entitlements.append((user_upn, required_entitlements))
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_workspace_service_principal(
            self,
            application_id: str,
            *,
            display_name: str | None = None,
            entitlements: tuple[str, ...] = (),
        ) -> str:
            ensured_service_principals.append(application_id)
            return "existing"

        async def ensure_workspace_service_principal_entitlements(
            self,
            application_id: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_service_principal_entitlements.append((application_id, required_entitlements))
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_sql_warehouse_permission(
            self,
            warehouse_id: str,
            principal_name: str,
            *,
            permission_level: str = "CAN_USE",
            principal_type: str | None = None,
        ) -> None:
            warehouse_permissions.append((warehouse_id, principal_name, principal_type))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(databricks_seed, "DatabricksSqlClient", FakeClient)
    monkeypatch.setattr(databricks_seed, "DatabricksAdminClient", FakeAdminClient)
    monkeypatch.setattr(databricks_seed, "_build_bootstrap_credential", lambda config: object())

    result = asyncio.run(databricks_seed.run_secure_seed())

    assert result["auth_mode"] == "managed_identity"
    assert result["catalog"] == "veeam_demo"
    assert result["status"] == "skipped"
    assert result["reason"] == "already_current"
    assert result["skip_catalog_create"] is False
    assert result["principal_results"] == {
        SELLER_A_UPN: "existing",
        SELLER_B_UPN: "existing",
    }
    assert result["workspace_user_entitlement_results"] == {
        SELLER_A_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
        SELLER_B_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
    }
    assert result["warehouse_permission_result"] == {
        "status": "applied",
        "applied_principals": [SELLER_A_UPN, SELLER_B_UPN, "mi-client"],
        "errors": [],
    }
    assert result["bootstrap_service_principal_entitlement_result"] == {
        "status": "already_set",
        "applied": [],
        "required": ["workspace-access", "databricks-sql-access"],
    }
    assert ensured_users == [
        SELLER_A_UPN,
        SELLER_B_UPN,
    ]
    assert ensured_user_entitlements == [
        (SELLER_A_UPN, ("workspace-access", "databricks-sql-access")),
        (SELLER_B_UPN, ("workspace-access", "databricks-sql-access")),
    ]
    assert ensured_service_principals == ["mi-client"]
    assert ensured_service_principal_entitlements == [
        ("mi-client", ("workspace-access", "databricks-sql-access"))
    ]
    assert warehouse_permissions == [
        ("wh-1", SELLER_A_UPN, "user"),
        ("wh-1", SELLER_B_UPN, "user"),
        ("wh-1", "mi-client", "service_principal"),
    ]
    assert all("CREATE USER IF NOT EXISTS" not in statement for statement in executed)
    assert any("CREATE TABLE IF NOT EXISTS veeam_demo.ri_ops.bootstrap_state" in statement for statement in executed)


def test_run_secure_seed_honors_skip_catalog_create(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text(
        """
        CREATE CATALOG IF NOT EXISTS veeam_demo;
        CREATE SCHEMA IF NOT EXISTS veeam_demo.ri;
        SELECT 1;
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    monkeypatch.setenv("DATABRICKS_CATALOG", "workspace_catalog")
    monkeypatch.setenv("DATABRICKS_SKIP_CATALOG_CREATE", "true")

    executed: list[str] = []
    ensured_user_entitlements: list[tuple[str, tuple[str, ...]]] = []
    ensured_service_principals: list[str] = []
    ensured_service_principal_entitlements: list[tuple[str, tuple[str, ...]]] = []
    warehouse_permissions: list[tuple[str, str, str | None]] = []

    class FakeClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def execute(self, statement: str):
            executed.append(" ".join(statement.split()))
            if "SELECT seed_version" in statement:
                return [{"seed_version": "stale"}]
            if "SELECT 'accounts'" in statement:
                return [
                    {"object_name": "accounts", "row_count": 1},
                    {"object_name": "reps", "row_count": 1},
                    {"object_name": "opportunities", "row_count": 1},
                    {"object_name": "contacts", "row_count": 1},
                    {"object_name": "entitlements", "row_count": 2},
                ]
            if "SHOW TABLES IN workspace_catalog.ri_secure" in statement:
                return [
                    {"tableName": "accounts"},
                    {"tableName": "reps"},
                    {"tableName": "opportunities"},
                    {"tableName": "contacts"},
                ]
            return []

        async def resolve_warehouse_id(self) -> str:
            return "wh-1"

        async def close(self) -> None:
            return None

    class FakeAdminClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def ensure_workspace_user(self, user_upn: str) -> str:
            return "existing"

        async def ensure_workspace_user_entitlements(
            self,
            user_upn: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_user_entitlements.append((user_upn, required_entitlements))
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_workspace_service_principal(
            self,
            application_id: str,
            *,
            display_name: str | None = None,
            entitlements: tuple[str, ...] = (),
        ) -> str:
            ensured_service_principals.append(application_id)
            return "existing"

        async def ensure_workspace_service_principal_entitlements(
            self,
            application_id: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_service_principal_entitlements.append((application_id, required_entitlements))
            return {"status": "patched", "applied": ["workspace-access"], "required": list(required_entitlements)}

        async def ensure_sql_warehouse_permission(
            self,
            warehouse_id: str,
            principal_name: str,
            *,
            permission_level: str = "CAN_USE",
            principal_type: str | None = None,
        ) -> None:
            warehouse_permissions.append((warehouse_id, principal_name, principal_type))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(databricks_seed, "DatabricksSqlClient", FakeClient)
    monkeypatch.setattr(databricks_seed, "DatabricksAdminClient", FakeAdminClient)
    monkeypatch.setattr(databricks_seed, "_build_bootstrap_credential", lambda config: object())

    result = asyncio.run(databricks_seed.run_secure_seed())

    assert result["catalog"] == "workspace_catalog"
    assert result["skip_catalog_create"] is True
    assert result["status"] == "seeded"
    assert result["warehouse_permission_result"] == {
        "status": "applied",
        "applied_principals": [SELLER_A_UPN, SELLER_B_UPN, "mi-client"],
        "errors": [],
    }
    assert result["workspace_user_entitlement_results"] == {
        SELLER_A_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
        SELLER_B_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
    }
    assert result["bootstrap_service_principal_entitlement_result"] == {
        "status": "patched",
        "applied": ["workspace-access"],
        "required": ["workspace-access", "databricks-sql-access"],
    }
    assert ensured_user_entitlements == [
        (SELLER_A_UPN, ("workspace-access", "databricks-sql-access")),
        (SELLER_B_UPN, ("workspace-access", "databricks-sql-access")),
    ]
    assert ensured_service_principals == ["mi-client"]
    assert ensured_service_principal_entitlements == [
        ("mi-client", ("workspace-access", "databricks-sql-access"))
    ]
    assert warehouse_permissions == [
        ("wh-1", SELLER_A_UPN, "user"),
        ("wh-1", SELLER_B_UPN, "user"),
        ("wh-1", "mi-client", "service_principal"),
    ]
    assert all("CREATE CATALOG IF NOT EXISTS" not in statement for statement in executed)
    assert any("CREATE SCHEMA IF NOT EXISTS workspace_catalog.ri" in statement for statement in executed)
    assert any("CREATE SCHEMA IF NOT EXISTS workspace_catalog.ri_ops" in statement for statement in executed)


def test_run_secure_seed_retries_warehouse_acl_with_multiple_bootstrap_principals(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_PRINCIPAL_ID", "mi-principal")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")

    executed: list[str] = []
    ensured_user_entitlements: list[tuple[str, tuple[str, ...]]] = []
    ensured_service_principals: list[str] = []
    ensured_service_principal_entitlements: list[tuple[str, tuple[str, ...]]] = []
    warehouse_permissions: list[tuple[str, str, str | None]] = []

    class FakeClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def execute(self, statement: str):
            executed.append(" ".join(statement.split()))
            if "SELECT seed_version" in statement:
                return [{"seed_version": "stale"}]
            if "SELECT 'accounts'" in statement:
                return [
                    {"object_name": "accounts", "row_count": 1},
                    {"object_name": "reps", "row_count": 1},
                    {"object_name": "opportunities", "row_count": 1},
                    {"object_name": "contacts", "row_count": 1},
                    {"object_name": "entitlements", "row_count": 2},
                ]
            if "SHOW TABLES IN veeam_demo.ri_secure" in statement:
                return [
                    {"tableName": "accounts"},
                    {"tableName": "reps"},
                    {"tableName": "opportunities"},
                    {"tableName": "contacts"},
                ]
            return []

        async def resolve_warehouse_id(self) -> str:
            return "wh-1"

        async def close(self) -> None:
            return None

    class FakeAdminClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def ensure_workspace_user(self, user_upn: str) -> str:
            return "existing"

        async def ensure_workspace_user_entitlements(
            self,
            user_upn: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_user_entitlements.append((user_upn, required_entitlements))
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_workspace_service_principal(
            self,
            application_id: str,
            *,
            display_name: str | None = None,
            entitlements: tuple[str, ...] = (),
        ) -> str:
            ensured_service_principals.append(application_id)
            return "created"

        async def ensure_workspace_service_principal_entitlements(
            self,
            application_id: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            ensured_service_principal_entitlements.append((application_id, required_entitlements))
            return {"status": "patched", "applied": ["databricks-sql-access"], "required": list(required_entitlements)}

        async def ensure_sql_warehouse_permission(
            self,
            warehouse_id: str,
            principal_name: str,
            *,
            permission_level: str = "CAN_USE",
            principal_type: str | None = None,
        ) -> None:
            warehouse_permissions.append((warehouse_id, principal_name, principal_type))
            if principal_name in {SELLER_A_UPN, "mi-client"}:
                raise databricks_seed.DatabricksAdminError("principal not found")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(databricks_seed, "DatabricksSqlClient", FakeClient)
    monkeypatch.setattr(databricks_seed, "DatabricksAdminClient", FakeAdminClient)
    monkeypatch.setattr(databricks_seed, "_build_bootstrap_credential", lambda config: object())

    result = asyncio.run(databricks_seed.run_secure_seed())

    assert result["status"] == "seeded"
    assert result["warehouse_permission_result"] == {
        "status": "applied",
        "applied_principals": [SELLER_B_UPN, "mi-principal"],
        "errors": [
            f"{SELLER_A_UPN}: principal not found",
            "mi-client: principal not found",
        ],
    }
    assert result["workspace_user_entitlement_results"] == {
        SELLER_A_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
        SELLER_B_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
    }
    assert result["bootstrap_service_principal_entitlement_result"] == {
        "status": "patched",
        "applied": ["databricks-sql-access"],
        "required": ["workspace-access", "databricks-sql-access"],
    }
    assert ensured_user_entitlements == [
        (SELLER_A_UPN, ("workspace-access", "databricks-sql-access")),
        (SELLER_B_UPN, ("workspace-access", "databricks-sql-access")),
    ]
    assert ensured_service_principals == ["mi-client"]
    assert ensured_service_principal_entitlements == [
        ("mi-client", ("workspace-access", "databricks-sql-access"))
    ]
    assert warehouse_permissions == [
        ("wh-1", SELLER_A_UPN, "user"),
        ("wh-1", SELLER_B_UPN, "user"),
        ("wh-1", "mi-client", "service_principal"),
        ("wh-1", "mi-principal", "service_principal"),
    ]
    assert any("CREATE TABLE IF NOT EXISTS veeam_demo.ri_ops.bootstrap_state" in statement for statement in executed)


def test_run_secure_seed_continues_when_warehouse_permissions_endpoint_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_PRINCIPAL_ID", "mi-principal")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")

    class FakeClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def execute(self, statement: str):
            if "SELECT seed_version" in statement:
                return [{"seed_version": "stale"}]
            if "SELECT 'accounts'" in statement:
                return [
                    {"object_name": "accounts", "row_count": 1},
                    {"object_name": "reps", "row_count": 1},
                    {"object_name": "opportunities", "row_count": 1},
                    {"object_name": "contacts", "row_count": 1},
                    {"object_name": "entitlements", "row_count": 2},
                ]
            if "SHOW TABLES IN veeam_demo.ri_secure" in statement:
                return [
                    {"tableName": "accounts"},
                    {"tableName": "reps"},
                    {"tableName": "opportunities"},
                    {"tableName": "contacts"},
                ]
            return []

        async def resolve_warehouse_id(self) -> str:
            return "wh-1"

        async def close(self) -> None:
            return None

    class FakeAdminClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def ensure_workspace_user(self, user_upn: str) -> str:
            return "existing"

        async def ensure_workspace_user_entitlements(
            self,
            user_upn: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_workspace_service_principal(
            self,
            application_id: str,
            *,
            display_name: str | None = None,
            entitlements: tuple[str, ...] = (),
        ) -> str:
            return "existing"

        async def ensure_workspace_service_principal_entitlements(
            self,
            application_id: str,
            *,
            required_entitlements: tuple[str, ...],
        ) -> dict[str, str | list[str]]:
            return {"status": "already_set", "applied": [], "required": list(required_entitlements)}

        async def ensure_sql_warehouse_permission(
            self,
            warehouse_id: str,
            principal_name: str,
            *,
            permission_level: str = "CAN_USE",
            principal_type: str | None = None,
        ) -> None:
            raise databricks_seed.DatabricksAdminError(
                "Databricks admin API request failed with HTTP 404: warehouses wh-1 does not exist"
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(databricks_seed, "DatabricksSqlClient", FakeClient)
    monkeypatch.setattr(databricks_seed, "DatabricksAdminClient", FakeAdminClient)
    monkeypatch.setattr(databricks_seed, "_build_bootstrap_credential", lambda config: object())

    result = asyncio.run(databricks_seed.run_secure_seed())

    assert result["status"] == "seeded"
    assert result["warehouse_permission_result"] == {
        "status": "skipped",
        "reason": "permissions_endpoint_not_available",
        "applied_principals": [],
        "errors": [
            f"{SELLER_A_UPN}: Databricks admin API request failed with HTTP 404: warehouses wh-1 does not exist",
            f"{SELLER_B_UPN}: Databricks admin API request failed with HTTP 404: warehouses wh-1 does not exist",
            "mi-client: Databricks admin API request failed with HTTP 404: warehouses wh-1 does not exist",
            "mi-principal: Databricks admin API request failed with HTTP 404: warehouses wh-1 does not exist",
        ],
    }
    assert result["workspace_user_entitlement_results"] == {
        SELLER_A_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
        SELLER_B_UPN: {
            "status": "already_set",
            "applied": [],
            "required": ["workspace-access", "databricks-sql-access"],
        },
    }
    assert result["bootstrap_service_principal_entitlement_result"] == {
        "status": "already_set",
        "applied": [],
        "required": ["workspace-access", "databricks-sql-access"],
    }


def test_validate_seed_output_checks_base_tables_and_view_existence(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(sql_file))
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", "mi-client")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    monkeypatch.setenv("DATABRICKS_CATALOG", "workspace_catalog")

    class FakeClient:
        def __init__(self, settings=None, **kwargs) -> None:
            self.settings = settings

        async def execute(self, statement: str):
            if "SELECT 'accounts'" in statement:
                return [
                    {"object_name": "accounts", "row_count": 1},
                    {"object_name": "reps", "row_count": 1},
                    {"object_name": "opportunities", "row_count": 1},
                    {"object_name": "contacts", "row_count": 1},
                    {"object_name": "entitlements", "row_count": 2},
                ]
            if "SHOW TABLES IN workspace_catalog.ri_secure" in statement:
                return [
                    {"tableName": "accounts"},
                    {"tableName": "reps"},
                    {"tableName": "opportunities"},
                    {"tableName": "contacts"},
                ]
            return []

    config = databricks_seed.load_seed_config()
    asyncio.run(databricks_seed._validate_seed_output(FakeClient(), config))
