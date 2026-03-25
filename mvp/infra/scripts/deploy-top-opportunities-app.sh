#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
APP_DIR="$ROOT_DIR/databricks_apps/top_opportunities_app"
APP_NAME="${TOP_OPPORTUNITIES_DATABRICKS_APP_NAME:-${INFRA_NAME_PREFIX:-dailyacctplannermcpdev}-top-opportunities}"
DEPLOY_MODE="${TOP_OPPORTUNITIES_APP_DEPLOY_MODE:-cli}"
HAS_DATABRICKS_CLI="false"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if command -v databricks >/dev/null 2>&1; then
  HAS_DATABRICKS_CLI="true"
fi

upsert_env_value() {
  local key="$1"
  local value="$2"

  python - <<'PY' "$ENV_FILE" "$key" "$value"
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
updated = False
rendered = f"{key}={value}"
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = rendered
        updated = True
        break

if not updated:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(rendered)

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

render_staging_env() {
  local output_path="$1"
  cat >"$output_path" <<EOF
AZURE_TENANT_ID=${AZURE_TENANT_ID:-}
MCP_CLIENT_ID=${MCP_CLIENT_ID:-}
MCP_CLIENT_SECRET=${MCP_CLIENT_SECRET:-}
MCP_EXPECTED_AUDIENCE=${TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE:-${MCP_EXPECTED_AUDIENCE:-}}
MCP_MANAGED_IDENTITY_CLIENT_ID=${MCP_MANAGED_IDENTITY_CLIENT_ID:-}
MCP_CLIENT_ASSERTION_SCOPE=${MCP_CLIENT_ASSERTION_SCOPE:-api://AzureADTokenExchange/.default}
DATABRICKS_HOST=${DATABRICKS_HOST:-}
DATABRICKS_CATALOG=${DATABRICKS_CATALOG:-veeam_demo}
DATABRICKS_OBO_SCOPE=${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}
DATABRICKS_WAREHOUSE_ID=${DATABRICKS_WAREHOUSE_ID:-}
DATABRICKS_SQL_TIMEOUT_SECONDS=${DATABRICKS_SQL_TIMEOUT_SECONDS:-30}
DATABRICKS_SQL_RETRY_COUNT=${DATABRICKS_SQL_RETRY_COUNT:-1}
DATABRICKS_SQL_POLL_ATTEMPTS=${DATABRICKS_SQL_POLL_ATTEMPTS:-6}
DATABRICKS_SQL_POLL_INTERVAL_SECONDS=${DATABRICKS_SQL_POLL_INTERVAL_SECONDS:-1}
RI_SCOPE_MODE=${RI_SCOPE_MODE:-user}
RI_DEMO_TERRITORY=${RI_DEMO_TERRITORY:-GreatLakes-ENT-Named-1}
SECURE_DEPLOYMENT=${SECURE_DEPLOYMENT:-false}
TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE=${TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE:-${MCP_EXPECTED_AUDIENCE:-}}
EOF
}

if [[ "$DEPLOY_MODE" == "external_url" || ( -n "${TOP_OPPORTUNITIES_APP_BASE_URL:-}" && "$HAS_DATABRICKS_CLI" != "true" ) ]]; then
  if [[ -z "${TOP_OPPORTUNITIES_APP_BASE_URL:-}" ]]; then
    echo "TOP_OPPORTUNITIES_APP_BASE_URL is required when TOP_OPPORTUNITIES_APP_DEPLOY_MODE=external_url." >&2
    exit 1
  fi
  upsert_env_value "TOP_OPPORTUNITIES_DATABRICKS_APP_NAME" "$APP_NAME"
  echo "Using preconfigured Databricks App URL: $TOP_OPPORTUNITIES_APP_BASE_URL"
  exit 0
fi

if [[ "$HAS_DATABRICKS_CLI" != "true" ]]; then
  echo "The databricks CLI is required to deploy the top opportunities Databricks App, or set TOP_OPPORTUNITIES_APP_BASE_URL and TOP_OPPORTUNITIES_APP_DEPLOY_MODE=external_url." >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Databricks app directory not found: $APP_DIR" >&2
  exit 1
fi

staging_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$staging_dir"
}
trap cleanup EXIT

mkdir -p "$staging_dir/shared"
cp "$APP_DIR"/app.py "$APP_DIR"/app.yaml "$APP_DIR"/requirements.txt "$staging_dir"/
cp -R "$ROOT_DIR/shared"/. "$staging_dir/shared/"
render_staging_env "$staging_dir/.env"

databricks apps deploy "$APP_NAME" --source-code-path "$staging_dir" >/dev/null

app_json="$(databricks apps get "$APP_NAME" -o json)"
app_url="$(python - <<'PY' "$app_json"
import json
import sys

payload = json.loads(sys.argv[1])
for key in ("url", "app_url", "default_url"):
    value = str(payload.get(key) or "").strip()
    if value:
        print(value.rstrip("/"))
        raise SystemExit(0)
status = payload.get("status") or {}
for key in ("url", "app_url"):
    value = str(status.get(key) or "").strip()
    if value:
        print(value.rstrip("/"))
        raise SystemExit(0)
raise SystemExit(1)
PY
)"

if [[ -z "$app_url" ]]; then
  echo "Databricks app deploy succeeded but the app URL could not be resolved." >&2
  exit 1
fi

upsert_env_value "TOP_OPPORTUNITIES_DATABRICKS_APP_NAME" "$APP_NAME"
upsert_env_value "TOP_OPPORTUNITIES_APP_BASE_URL" "$app_url"
echo "Top Opportunities Databricks App deployed to: $app_url"
