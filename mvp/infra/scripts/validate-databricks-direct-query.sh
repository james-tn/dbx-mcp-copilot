#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

if [[ -n "${DATABRICKS_RESOURCE_GROUP:-}" && -n "${DATABRICKS_WORKSPACE_NAME:-}" ]]; then
  resolved_workspace_url="$(az databricks workspace show \
    --resource-group "$DATABRICKS_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    --query workspaceUrl \
    -o tsv 2>/dev/null || true)"
  if [[ -n "$resolved_workspace_url" ]]; then
    DATABRICKS_HOST="https://${resolved_workspace_url}"
  fi
fi

if [[ "${SECURE_DEPLOYMENT:-false}" == "true" && "${DATABRICKS_SKIP_CATALOG_CREATE:-false}" == "true" && -n "${DATABRICKS_WORKSPACE_NAME:-}" ]]; then
  workspace_id="$(
    az databricks workspace show \
      --resource-group "$DATABRICKS_RESOURCE_GROUP" \
      --name "$DATABRICKS_WORKSPACE_NAME" \
      --query workspaceId \
      -o tsv
  )"
  export DATABRICKS_CATALOG="$(
    python - <<'PY' "$DATABRICKS_WORKSPACE_NAME" "$workspace_id" "${DATABRICKS_CATALOG:-}"
import re
import sys

workspace_name = sys.argv[1].strip()
workspace_id = sys.argv[2].strip()
configured_catalog = sys.argv[3].strip()
normalized_name = re.sub(r"[^0-9A-Za-z_]", "_", workspace_name)
workspace_catalog = f"{normalized_name}_{workspace_id}" if normalized_name and workspace_id else ""
print(workspace_catalog or configured_catalog or "veeam_demo")
PY
  )"
else
  export DATABRICKS_CATALOG="${DATABRICKS_CATALOG:-veeam_demo}"
fi

export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY'
import asyncio
import json
import os

from mcp_server.auth_context import acquire_databricks_access_token
from mcp_server.databricks_sql import DatabricksSqlClient


async def main() -> None:
    planner_assertion = os.environ.get("PLANNER_API_BEARER_TOKEN", "").strip() or None
    catalog = os.environ.get("DATABRICKS_CATALOG", "veeam_demo").strip() or "veeam_demo"
    delegated_token = acquire_databricks_access_token(planner_assertion) if planner_assertion else None
    client = DatabricksSqlClient(access_token=delegated_token)
    try:
        current_user_rows = await client.query_sql(
            "SELECT current_user() AS current_user, session_user() AS session_user"
        )
        count_rows = await client.query_sql(
            f"""
SELECT 'accounts' AS table_name, COUNT(*) AS row_count FROM {catalog}.ri_secure.accounts
UNION ALL
SELECT 'reps' AS table_name, COUNT(*) AS row_count FROM {catalog}.ri_secure.reps
UNION ALL
SELECT 'opportunities' AS table_name, COUNT(*) AS row_count FROM {catalog}.ri_secure.opportunities
UNION ALL
SELECT 'contacts' AS table_name, COUNT(*) AS row_count FROM {catalog}.ri_secure.contacts
ORDER BY table_name
""".strip()
        )
        territory_rows = await client.query_sql(
            f"""
SELECT sales_team, COUNT(*) AS account_count
FROM {catalog}.ri_secure.accounts
GROUP BY sales_team
ORDER BY sales_team
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
                "session_user": current_user_rows[0].get("session_user") if current_user_rows else None,
                "catalog": catalog,
                "counts": count_rows,
                "territories": territory_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


asyncio.run(main())
PY
