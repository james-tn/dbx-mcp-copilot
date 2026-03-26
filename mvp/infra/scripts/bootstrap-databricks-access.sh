#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
TARGET="${1:-foundation}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

case "$TARGET" in
  foundation)
    if [[ -z "${DATABRICKS_HOST:-}" ]]; then
      echo "DATABRICKS_HOST is required for foundation Databricks access bootstrap." >&2
      exit 1
    fi
    export DATABRICKS_HOST="${DATABRICKS_HOST}"
    export DATABRICKS_AZURE_RESOURCE_ID="${DATABRICKS_AZURE_RESOURCE_ID:-}"
    export DATABRICKS_OBO_SCOPE="${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
    export DATABRICKS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-}"
    export DATABRICKS_ACCESS_GRANT_SOURCES="${DATABRICKS_ACCESS_GRANT_SOURCES:-${CUSTOMER_TOP_OPPORTUNITIES_SOURCE:-},${CUSTOMER_CONTACTS_SOURCE:-}}"
    export DATABRICKS_BOOTSTRAP_AUTH_MODE="${DATABRICKS_BOOTSTRAP_AUTH_MODE:-azure_cli}"
    ;;
  *)
    echo "Usage: bash mvp/infra/scripts/bootstrap-databricks-access.sh <foundation>" >&2
    exit 1
    ;;
esac

export PYTHONPATH="$ROOT_DIR/agents${PYTHONPATH:+:$PYTHONPATH}"

python3 - <<'PY'
import asyncio
import json

from databricks_seed import run_secure_access_bootstrap


def main() -> None:
    print(json.dumps(asyncio.run(run_secure_access_bootstrap()), indent=2, sort_keys=True))


main()
PY
