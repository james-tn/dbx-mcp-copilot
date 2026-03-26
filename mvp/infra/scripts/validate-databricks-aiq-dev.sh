#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
AIQ_DEV_CATALOG="${AIQ_DEV_CATALOG:-dev_catalog}"
VALIDATION_TERRITORY="${VALIDATION_TERRITORY:-Germany-ENT-Named-5}"

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

python3 - <<'PY' "$AIQ_DEV_CATALOG" "$VALIDATION_TERRITORY"
import asyncio
import json
import sys

from databricks_sql import DatabricksSqlClient

catalog = sys.argv[1]
territory = sys.argv[2]


async def main() -> None:
    client = DatabricksSqlClient()
    try:
        counts = await client.query_sql(
            f"""
SELECT 'account_iq_scores' AS object_name, COUNT(*) AS row_count
FROM {catalog}.data_science_account_iq_gold.account_iq_scores
UNION ALL
SELECT 'aiq_contact' AS object_name, COUNT(*) AS row_count
FROM {catalog}.account_iq_gold.aiq_contact
ORDER BY object_name
""".strip()
        )
        sample = await client.query_sql(
            f"""
SELECT
  account_id,
  account_name,
  sales_team,
  xf_score_previous_day,
  intent,
  upsell
FROM {catalog}.data_science_account_iq_gold.account_iq_scores
WHERE sales_team = '{territory.replace("'", "''")}'
ORDER BY xf_score_previous_day DESC
LIMIT 5
""".strip()
        )
    finally:
        await client.close()

    print(
        json.dumps(
            {
                "catalog": catalog,
                "territory": territory,
                "counts": counts,
                "top_accounts": sample,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


asyncio.run(main())
PY
