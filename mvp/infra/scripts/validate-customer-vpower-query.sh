#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
VALIDATE_USER_UPN="${VALIDATE_USER_UPN:-${1:-}}"
TOP_OPPS_LIMIT="${TOP_OPPS_LIMIT:-5}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "$VALIDATE_USER_UPN" ]]; then
  echo "Set VALIDATE_USER_UPN or pass the user UPN/email as the first argument." >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/agents${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY' "$VALIDATE_USER_UPN" "$TOP_OPPS_LIMIT"
import asyncio
import json
import sys

import config
import customer_backend
from databricks_sql import DatabricksSqlClient, DatabricksSqlSettings


async def main(user_upn: str, top_opps_limit: int) -> None:
    settings = customer_backend.load_customer_databricks_query_settings()
    if not settings.host:
        raise SystemExit("DATABRICKS_HOST is required.")

    client = DatabricksSqlClient(
        settings=DatabricksSqlSettings(
            host=settings.host,
            token_scope=settings.scope,
            azure_management_scope="https://management.core.windows.net//.default",
            azure_workspace_resource_id=settings.azure_resource_id,
            warehouse_id=settings.warehouse_id,
            timeout_seconds=30.0,
            retry_count=1,
            poll_attempts=6,
            poll_interval_seconds=1.0,
            pat=settings.pat,
        )
    )
    try:
        sales_team_query = customer_backend._render_sql_template(
            customer_backend._build_builtin_sales_team_mapping_query(),
            {"user_upn": user_upn},
        )
        sales_team_rows = await client.query_sql(sales_team_query)
        territories = sorted(
            {
                str(row.get("sales_team", "")).strip()
                for row in sales_team_rows
                if str(row.get("sales_team", "")).strip()
            }
        )

        scoped_accounts_query = customer_backend._render_sql_template(
            customer_backend._build_builtin_scoped_accounts_query(),
            {"user_upn": user_upn},
        )
        scoped_account_rows = await client.query_sql(scoped_accounts_query)

        top_opportunities_source = config.get_customer_top_opportunities_source()
        top_opportunity_rows = []
        if top_opportunities_source and territories:
            top_opportunities_query = customer_backend._render_sql_template(
                f"""
SELECT
  account_id,
  account_name,
  sales_team,
  xf_score_previous_day
FROM {top_opportunities_source}
WHERE {{{{sales_team_filter}}}}
ORDER BY xf_score_previous_day DESC
LIMIT {{{{limit}}}}
""".strip(),
                {
                    "sales_team_filter": customer_backend._sales_team_filter_clause(territories),
                    "limit": str(top_opps_limit),
                },
                raw_keys={"sales_team_filter"},
            )
            top_opportunity_rows = await client.query_sql(top_opportunities_query)

        print(
            json.dumps(
                {
                    "user_upn": user_upn,
                    "host": settings.host,
                    "warehouse_id": settings.warehouse_id,
                    "territories": territories,
                    "territory_count": len(territories),
                    "scoped_account_count": len(scoped_account_rows),
                    "top_opportunities_source": top_opportunities_source,
                    "top_opportunities_row_count": len(top_opportunity_rows),
                    "sample_scoped_accounts": scoped_account_rows[:5],
                    "sample_top_opportunities": top_opportunity_rows[:5],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await client.close()


asyncio.run(main(sys.argv[1], int(sys.argv[2])))
PY
