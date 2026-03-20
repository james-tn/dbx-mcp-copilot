#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

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

export PYTHONPATH="$ROOT_DIR/agents${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY'
import asyncio
import json
import os

from auth_context import acquire_databricks_access_token
from databricks_sql import DatabricksSqlClient


async def main() -> None:
    planner_assertion = os.environ.get("PLANNER_API_BEARER_TOKEN", "").strip() or None
    delegated_token = acquire_databricks_access_token(planner_assertion) if planner_assertion else None
    client = DatabricksSqlClient(access_token=delegated_token)
    try:
        current_user_rows = await client.query_sql("SELECT current_user() AS current_user")
        count_rows = await client.query_sql(
            """
SELECT 'accounts' AS table_name, COUNT(*) AS row_count FROM veeam_demo.ri_secure.accounts
UNION ALL
SELECT 'reps' AS table_name, COUNT(*) AS row_count FROM veeam_demo.ri_secure.reps
UNION ALL
SELECT 'opportunities' AS table_name, COUNT(*) AS row_count FROM veeam_demo.ri_secure.opportunities
UNION ALL
SELECT 'contacts' AS table_name, COUNT(*) AS row_count FROM veeam_demo.ri_secure.contacts
ORDER BY table_name
""".strip()
        )
    finally:
        await client.close()

    print(
        json.dumps(
            {
                "host": os.environ["DATABRICKS_HOST"].rstrip("/"),
                "auth_mode": (
                    "planner_obo"
                    if planner_assertion
                    else ("pat" if os.environ.get("DATABRICKS_PAT") else "azure_cli_or_managed_identity")
                ),
                "current_user": current_user_rows[0].get("current_user") if current_user_rows else None,
                "counts": count_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


asyncio.run(main())
PY
