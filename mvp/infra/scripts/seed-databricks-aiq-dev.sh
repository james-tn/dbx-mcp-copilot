#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
SQL_TEMPLATE="${SQL_TEMPLATE:-$ROOT_DIR/infra/databricks/seed-databricks-aiq-dev.sql}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "${DATABRICKS_HOST:-}" ]]; then
  echo "DATABRICKS_HOST is required." >&2
  exit 1
fi

export DATABRICKS_HOST="${DATABRICKS_HOST}"
export DATABRICKS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-}"
export DATABRICKS_OBO_SCOPE="${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
export DATABRICKS_AZURE_RESOURCE_ID="${DATABRICKS_AZURE_RESOURCE_ID:-}"
AIQ_DEV_CATALOG="${AIQ_DEV_CATALOG:-dev_catalog}"
AIQ_DEV_SKIP_CATALOG_CREATE="${AIQ_DEV_SKIP_CATALOG_CREATE:-${DATABRICKS_SKIP_CATALOG_CREATE:-false}}"

if [[ ! -f "$SQL_TEMPLATE" ]]; then
  echo "AIQ dev seed SQL file not found: $SQL_TEMPLATE" >&2
  exit 1
fi

rendered_sql="$(mktemp)"
trap 'rm -f "$rendered_sql"' EXIT

python3 - <<'PY' "$SQL_TEMPLATE" "$rendered_sql" "$AIQ_DEV_CATALOG" "$AIQ_DEV_SKIP_CATALOG_CREATE"
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
output = Path(sys.argv[2])
catalog = sys.argv[3]
skip_catalog_create = sys.argv[4].strip().lower() in {"1", "true", "yes", "on"}
rendered = template.replace("__CATALOG__", catalog)
if skip_catalog_create:
    rendered = "\n".join(
        line
        for line in rendered.splitlines()
        if line.strip().upper() != f"CREATE CATALOG IF NOT EXISTS {catalog.upper()};"
    )
output.write_text(rendered, encoding="utf-8")
PY

export PYTHONPATH="$ROOT_DIR/agents${PYTHONPATH:+:$PYTHONPATH}"
export SQL_FILE="$rendered_sql"

python3 - <<'PY'
import asyncio
import json
import os

from databricks_sql import DatabricksSqlClient


def split_statements(script: str) -> list[str]:
    statements = []
    current = []
    in_single = False
    in_double = False

    for raw_line in script.splitlines():
        current.append(raw_line)
        for char in raw_line:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
        if raw_line.rstrip().endswith(";") and not in_single and not in_double:
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []

    trailing = "\n".join(current).strip().rstrip(";").strip()
    if trailing:
        statements.append(trailing)
    return statements


async def main() -> None:
    script = open(os.environ["SQL_FILE"], encoding="utf-8").read()
    statements = split_statements(script)
    client = DatabricksSqlClient()
    try:
        last_rows = []
        for statement in statements:
            last_rows = await client.execute(statement)
        print(json.dumps({"status": "ok", "catalog": os.environ.get("AIQ_DEV_CATALOG", ""), "result": last_rows}, indent=2))
    finally:
        await client.close()


asyncio.run(main())
PY
