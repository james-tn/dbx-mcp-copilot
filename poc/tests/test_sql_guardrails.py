import importlib.util
from pathlib import Path


module_path = Path(__file__).resolve().parents[1] / 'services' / 'revenue-mcp' / 'app' / 'sql_guardrails.py'
spec = importlib.util.spec_from_file_location('sql_guardrails', module_path)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)
validate_sql = module.validate_sql


def test_validate_sql_allows_select_in_allowed_schema() -> None:
    sql = "SELECT * FROM ri_poc.revenue.v_fact_revenue_secure"
    validate_sql(sql, "ri_poc.revenue")


def test_validate_sql_blocks_ddl() -> None:
    sql = "DROP TABLE ri_poc.revenue.fact_revenue"
    try:
        validate_sql(sql, "ri_poc.revenue")
    except ValueError:
        return
    raise AssertionError("Expected guardrail to block DDL")


def test_validate_sql_blocks_other_schema() -> None:
    sql = "SELECT * FROM other_catalog.other_schema.table1"
    try:
        validate_sql(sql, "ri_poc.revenue")
    except ValueError:
        return
    raise AssertionError("Expected guardrail to block non-allowed schema")
