from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import databricks_seed


def test_load_seed_config_requires_managed_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABRICKS_SEED_SQL_FILE", str(tmp_path / "seed.sql"))
    monkeypatch.delenv("ARM_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setenv("DATABRICKS_BOOTSTRAP_AUTH_MODE", "managed_identity")

    with pytest.raises(databricks_seed.DatabricksSeedError, match="managed_identity"):
        databricks_seed.load_seed_config()


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
        "ri-test-na@m365cpi89838450.onmicrosoft.com": "existing",
        "DaichiM@M365CPI89838450.OnMicrosoft.com": "existing",
    }
    assert ensured_users == [
        "ri-test-na@m365cpi89838450.onmicrosoft.com",
        "DaichiM@M365CPI89838450.OnMicrosoft.com",
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

        async def close(self) -> None:
            return None

    monkeypatch.setattr(databricks_seed, "DatabricksSqlClient", FakeClient)
    monkeypatch.setattr(databricks_seed, "DatabricksAdminClient", FakeAdminClient)
    monkeypatch.setattr(databricks_seed, "_build_bootstrap_credential", lambda config: object())

    result = asyncio.run(databricks_seed.run_secure_seed())

    assert result["catalog"] == "workspace_catalog"
    assert result["skip_catalog_create"] is True
    assert result["status"] == "seeded"
    assert all("CREATE CATALOG IF NOT EXISTS" not in statement for statement in executed)
    assert any("CREATE SCHEMA IF NOT EXISTS workspace_catalog.ri" in statement for statement in executed)
    assert any("CREATE SCHEMA IF NOT EXISTS workspace_catalog.ri_ops" in statement for statement in executed)


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
