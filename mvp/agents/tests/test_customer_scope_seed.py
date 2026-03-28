from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customer_scope_seed


def _sample_scope_workbook_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "accont_scope_query_result.csv"


def test_scope_workbook_loader_accepts_mislabeled_xlsx_sample() -> None:
    rows = customer_scope_seed.load_scope_workbook_rows(_sample_scope_workbook_path())

    assert len(rows) == 100
    assert rows[0]["vpower_account_id"] == "001cx00000PN3J4AAL"
    assert rows[0]["salesteam"] == "NEE-VEL-Velocity-3"
    assert rows[0]["Email"] == "bartek.niezabitowski@veeam.com"


def test_mock_customer_seed_dataset_matches_sample_shape() -> None:
    rows = customer_scope_seed.load_scope_workbook_rows(_sample_scope_workbook_path())
    dataset = customer_scope_seed.build_mock_customer_seed_dataset(rows)

    assert len(dataset.accounts) == 101
    assert len(dataset.territories) == 64
    assert len(dataset.users) == 59
    assert len(dataset.aiq_rows) == 100
    assert len(dataset.contacts) == 100
    assert len(dataset.object_territory_associations) == 100
    assert len(dataset.user_territory_associations) == 65


def test_mock_customer_seed_dataset_adds_daichi_alias_with_territory_access() -> None:
    rows = customer_scope_seed.load_scope_workbook_rows(_sample_scope_workbook_path())
    dataset = customer_scope_seed.build_mock_customer_seed_dataset(rows)

    alias_user = next(
        row for row in dataset.users if row["Email"] == "DaichiM@M365CPI89838450.OnMicrosoft.com"
    )
    alias_territory_ids = {
        row["Territory2Id"]
        for row in dataset.user_territory_associations
        if row["UserId"] == alias_user["Id"]
    }

    assert alias_territory_ids


def test_render_mock_customer_seed_sql_includes_bronze_and_aiq_tables() -> None:
    rows = customer_scope_seed.load_scope_workbook_rows(_sample_scope_workbook_path())
    sql = customer_scope_seed.render_mock_customer_seed_sql(rows, catalog_placeholder="workspace_catalog")

    assert "CREATE SCHEMA IF NOT EXISTS workspace_catalog.sf_vpower_bronze;" in sql
    assert "CREATE OR REPLACE TABLE workspace_catalog.sf_vpower_bronze.`user`" in sql
    assert "INSERT OVERWRITE workspace_catalog.sf_vpower_bronze.account VALUES" in sql
    assert "INSERT INTO workspace_catalog.data_science_account_iq_gold.account_iq_scores VALUES" in sql
    assert "INSERT INTO workspace_catalog.account_iq_gold.aiq_contact VALUES" in sql
