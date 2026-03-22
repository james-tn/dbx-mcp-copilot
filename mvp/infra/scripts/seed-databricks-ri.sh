#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
SQL_FILE="${SQL_FILE:-$ROOT_DIR/infra/databricks/seed-databricks-ri.sql}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}"
CONTAINERAPPS_JOB_API_VERSION="${CONTAINERAPPS_JOB_API_VERSION:-2025-07-01}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ ! -f "$SQL_FILE" ]]; then
  echo "Seed SQL file not found: $SQL_FILE" >&2
  exit 1
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

DATABRICKS_CATALOG="${DATABRICKS_CATALOG:-veeam_demo}"
DATABRICKS_SKIP_CATALOG_CREATE="${DATABRICKS_SKIP_CATALOG_CREATE:-false}"
DATABRICKS_WORKSPACE_USER_UPNS="${DATABRICKS_WORKSPACE_USER_UPNS:-}"

SECURE_MODE="false"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  SECURE_MODE="true"
fi

if [[ "$SECURE_MODE" == "true" ]]; then
  required_secure_vars=(
    AZURE_SUBSCRIPTION_ID
    AZURE_RESOURCE_GROUP
    DATABRICKS_SEED_JOB_NAME
  )
  for var_name in "${required_secure_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
      echo "$var_name is required for secure Databricks seeding." >&2
      exit 1
    fi
  done

  timeout_seconds="${DATABRICKS_SEED_TIMEOUT_SECONDS:-1800}"
  poll_seconds="${DATABRICKS_SEED_POLL_SECONDS:-15}"
  if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]]; then
    echo "DATABRICKS_SEED_TIMEOUT_SECONDS must be an integer." >&2
    exit 1
  fi
  if ! [[ "$poll_seconds" =~ ^[0-9]+$ ]] || [[ "$poll_seconds" -le 0 ]]; then
    echo "DATABRICKS_SEED_POLL_SECONDS must be a positive integer." >&2
    exit 1
  fi

  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
  echo "Starting secure Databricks seed job: $DATABRICKS_SEED_JOB_NAME"
  execution_name="$(
    az rest \
      --method post \
      --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/start?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
      --query name \
      -o tsv
  )"

  if [[ -z "$execution_name" ]]; then
    echo "Failed to start secure Databricks seed job." >&2
    exit 1
  fi

  deadline=$(( $(date +%s) + timeout_seconds ))
  while true; do
    status="$(
      az rest \
        --method get \
        --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/executions/${execution_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
        --query properties.status \
        -o tsv 2>/dev/null || true
    )"

    case "$status" in
      Succeeded)
        echo "Secure Databricks seed completed successfully via ACA Job: $execution_name"
        exit 0
        ;;
      Failed|Stopped)
        echo "Secure Databricks seed job failed with status: $status" >&2
        az rest \
          --method get \
          --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/executions/${execution_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
          -o json >&2 || true
        exit 1
        ;;
      *)
        :
        ;;
    esac

    if [[ "$(date +%s)" -ge "$deadline" ]]; then
      echo "Secure Databricks seed job timed out after ${timeout_seconds}s." >&2
      exit 1
    fi
    sleep "$poll_seconds"
  done
fi

if [[ -n "${DATABRICKS_PAT:-}" ]]; then
  DBX_TOKEN="$DATABRICKS_PAT"
else
  DBX_TOKEN="$(az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv)"
fi

export DBX_TOKEN

if [[ -n "$DATABRICKS_WORKSPACE_USER_UPNS" ]]; then
  export DATABRICKS_WORKSPACE_USER_UPNS
  python - <<'PY'
import json
import os
import urllib.parse
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
user_upns = [
    item.strip()
    for item in os.environ.get("DATABRICKS_WORKSPACE_USER_UPNS", "").split(",")
    if item.strip()
]

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/scim+json",
}

for user_upn in user_upns:
    filter_value = urllib.parse.quote(f'userName eq "{user_upn}"', safe="")
    get_request = urllib.request.Request(
        f"{host}/api/2.0/preview/scim/v2/Users?filter={filter_value}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(get_request, timeout=60) as response:
        payload = json.load(response)
    if int(payload.get("totalResults", 0) or 0) > 0:
        print(f"Workspace user already present: {user_upn}")
        continue

    create_payload = json.dumps(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": user_upn,
            "displayName": user_upn.split("@", 1)[0],
        }
    ).encode("utf-8")
    create_request = urllib.request.Request(
        f"{host}/api/2.0/preview/scim/v2/Users",
        data=create_payload,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(create_request, timeout=60):
        pass
    print(f"Created workspace user: {user_upn}")
PY
fi

resolve_warehouse_id() {
  python - <<'PY'
import json
import os
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
request = urllib.request.Request(
    f"{host}/api/2.0/sql/warehouses",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)

warehouses = payload.get("warehouses", [])
preferred = None
for warehouse in warehouses:
    state = str(warehouse.get("state", "")).upper()
    if state in {"RUNNING", "STARTING", "STARTED"}:
        preferred = warehouse
        break
if preferred is None and warehouses:
    preferred = warehouses[0]
if preferred is None:
    raise SystemExit("No Databricks SQL warehouse was found.")
print(preferred["id"])
PY
}

warehouse_exists() {
  local warehouse_id="$1"
  [[ -n "$warehouse_id" ]] || return 1
  export DBX_WAREHOUSE_ID_CHECK="$warehouse_id"
  python - <<'PY'
import json
import os
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
warehouse_id = os.environ["DBX_WAREHOUSE_ID_CHECK"]

request = urllib.request.Request(
    f"{host}/api/2.0/sql/warehouses",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)

warehouses = payload.get("warehouses", [])
for warehouse in warehouses:
    if str(warehouse.get("id", "")).strip() == warehouse_id:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

if [[ -z "${DATABRICKS_WAREHOUSE_ID:-}" ]] || ! warehouse_exists "$DATABRICKS_WAREHOUSE_ID"; then
  DATABRICKS_WAREHOUSE_ID="$(resolve_warehouse_id)"
fi

export DBX_TOKEN DATABRICKS_WAREHOUSE_ID SQL_FILE DATABRICKS_CATALOG DATABRICKS_SKIP_CATALOG_CREATE

python - <<'PY'
import json
import os
import time
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
sql_file = os.environ["SQL_FILE"]
catalog = os.environ.get("DATABRICKS_CATALOG", "veeam_demo")
skip_catalog_create = os.environ.get("DATABRICKS_SKIP_CATALOG_CREATE", "false").strip().lower() == "true"
script = open(sql_file, encoding="utf-8").read().replace("veeam_demo", catalog)
if skip_catalog_create:
    lines = script.splitlines()
    lines = [line for line in lines if line.strip().upper() != f"CREATE CATALOG IF NOT EXISTS {catalog.upper()};"]
    script = "\n".join(lines)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{host}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)

for raw_statement in script.split(";"):
    statement = raw_statement.strip()
    if not statement or statement.startswith("--"):
        continue
    payload = request(
        "POST",
        "/api/2.0/sql/statements",
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "0s",
            "disposition": "INLINE",
        },
    )
    status = payload.get("status", {}).get("state", "")
    statement_id = payload.get("statement_id")
    while status in {"PENDING", "RUNNING", "QUEUED"} and statement_id:
        time.sleep(1)
        payload = request("GET", f"/api/2.0/sql/statements/{statement_id}")
        status = payload.get("status", {}).get("state", "")
    if status not in {"SUCCEEDED", "FINISHED"}:
        raise SystemExit(
            f"Statement failed with status {status}: {statement[:120]}"
        )

print(f"Seed completed successfully using warehouse {warehouse_id}")
PY
