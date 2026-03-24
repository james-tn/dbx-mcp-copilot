#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
SQL_FILE="${SQL_FILE:-$ROOT_DIR/infra/databricks/seed-databricks-ri.sql}"
HELPER_SCRIPT="$ROOT_DIR/infra/bootstrap_helpers.py"
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
SELLER_A_UPN="${SELLER_A_UPN:-}"
SELLER_B_UPN="${SELLER_B_UPN:-}"
DATABRICKS_AUTO_CREATE_WAREHOUSE="${DATABRICKS_AUTO_CREATE_WAREHOUSE:-true}"
DATABRICKS_WAREHOUSE_NAME="${DATABRICKS_WAREHOUSE_NAME:-${INFRA_NAME_PREFIX:-dailyacctplanner}-sql}"
DATABRICKS_WAREHOUSE_CLUSTER_SIZE="${DATABRICKS_WAREHOUSE_CLUSTER_SIZE:-Small}"
DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS="${DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS:-1}"
DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS="${DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS:-1}"
DATABRICKS_WAREHOUSE_AUTO_STOP_MINS="${DATABRICKS_WAREHOUSE_AUTO_STOP_MINS:-10}"
DATABRICKS_WAREHOUSE_TYPE="${DATABRICKS_WAREHOUSE_TYPE:-PRO}"
DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS="${DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS:-false}"

if [[ -z "$SELLER_A_UPN" || -z "$SELLER_B_UPN" ]]; then
  IFS=',' read -r derived_seller_a derived_seller_b _ <<<"$DATABRICKS_WORKSPACE_USER_UPNS"
  SELLER_A_UPN="${SELLER_A_UPN:-${derived_seller_a:-}}"
  SELLER_B_UPN="${SELLER_B_UPN:-${derived_seller_b:-}}"
fi

if [[ -z "$DATABRICKS_WORKSPACE_USER_UPNS" && -n "$SELLER_A_UPN" && -n "$SELLER_B_UPN" ]]; then
  DATABRICKS_WORKSPACE_USER_UPNS="${SELLER_A_UPN},${SELLER_B_UPN}"
fi

if [[ -z "$SELLER_A_UPN" || -z "$SELLER_B_UPN" ]]; then
  echo "SELLER_A_UPN and SELLER_B_UPN are required for the seed entitlements and grants." >&2
  exit 1
fi

rendered_sql_file="$(mktemp)"
cleanup_rendered_sql_file() {
  rm -f "$rendered_sql_file"
}
trap cleanup_rendered_sql_file EXIT

python_bin="python"
if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
fi

log_step() {
  echo "[seed-databricks-ri] STEP: $*"
}

log_success() {
  echo "[seed-databricks-ri] OK: $*"
}

fail_step() {
  echo "[seed-databricks-ri] ERROR: $*" >&2
  exit 1
}

upsert_env_value() {
  local key="$1"
  local value="$2"

  "$python_bin" - <<'PY' "$ENV_FILE" "$key" "$value"
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

get_secure_seed_job_identity_summary() {
  az rest \
    --method get \
    --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
    --query "{type:identity.type, assigned:keys(identity.userAssignedIdentities)}" \
    -o json 2>/dev/null || true
}

verify_secure_seed_job_identity_ready() {
  local identity_summary
  identity_summary="$(get_secure_seed_job_identity_summary)"

  mapfile -t identity_state < <("$python_bin" - <<'PY' "$identity_summary"
import json
import sys

raw = sys.argv[1].strip()
payload = json.loads(raw) if raw else {}
identity_type = str(payload.get("type") or "").strip()
assigned = payload.get("assigned") or []
assigned = [str(item).strip() for item in assigned if str(item).strip()]
ok = bool(identity_type) and identity_type.lower() != "none" and bool(assigned)
print("true" if ok else "false")
print(identity_type)
print(", ".join(assigned))
PY
)

  local identity_ok="${identity_state[0]:-false}"
  local identity_type="${identity_state[1]:-}"
  local assigned_identities="${identity_state[2]:-}"
  if [[ "$identity_ok" != "true" ]]; then
    fail_step "Secure Databricks seed job '$DATABRICKS_SEED_JOB_NAME' does not have a usable managed identity. identity.type='${identity_type:-<empty>}', assigned='${assigned_identities:-<none>}'"
  fi

  log_success "Verified secure Databricks seed job identity: type='${identity_type}', assigned='${assigned_identities}'"
}

"$python_bin" "$HELPER_SCRIPT" render-seed-sql \
  --template "$SQL_FILE" \
  --output "$rendered_sql_file" \
  --seller-a-upn "$SELLER_A_UPN" \
  --seller-b-upn "$SELLER_B_UPN"
SQL_FILE="$rendered_sql_file"

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
  log_step "Verifying secure Databricks seed job identity before start"
  verify_secure_seed_job_identity_ready
  log_step "Starting secure Databricks seed job: $DATABRICKS_SEED_JOB_NAME"
  execution_name="$(
    az rest \
      --method post \
      --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/start?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
      --query name \
      -o tsv
  )"

  if [[ -z "$execution_name" ]]; then
    fail_step "Failed to start secure Databricks seed job '$DATABRICKS_SEED_JOB_NAME'."
  fi
  log_success "Started secure Databricks seed job execution '$execution_name'"

  deadline=$(( $(date +%s) + timeout_seconds ))
  last_status=""
  while true; do
    status="$(
      az rest \
        --method get \
        --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/executions/${execution_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
        --query properties.status \
        -o tsv 2>/dev/null || true
    )"

    if [[ -n "$status" && "$status" != "$last_status" ]]; then
      echo "[seed-databricks-ri] STATUS: execution '$execution_name' is $status"
      last_status="$status"
    fi

    case "$status" in
      Succeeded)
        log_success "Secure Databricks seed completed successfully via ACA Job: $execution_name"
        exit 0
        ;;
      Failed|Stopped)
        echo "[seed-databricks-ri] ERROR: Secure Databricks seed job failed with status: $status" >&2
        echo "[seed-databricks-ri] ERROR: Execution name: $execution_name" >&2
        echo "[seed-databricks-ri] ERROR: Job identity summary:" >&2
        get_secure_seed_job_identity_summary >&2 || true
        echo "[seed-databricks-ri] ERROR: Execution details:" >&2
        az rest \
          --method get \
          --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${DATABRICKS_SEED_JOB_NAME}/executions/${execution_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
          -o json >&2 || true
        echo "[seed-databricks-ri] ERROR: Follow-up check:" >&2
        echo "az containerapp job identity show -g ${AZURE_RESOURCE_GROUP} -n ${DATABRICKS_SEED_JOB_NAME}" >&2
        exit 1
        ;;
      *)
        :
        ;;
    esac

    if [[ "$(date +%s)" -ge "$deadline" ]]; then
      fail_step "Secure Databricks seed job timed out after ${timeout_seconds}s. Execution name: $execution_name"
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
  export DBX_AUTO_CREATE_WAREHOUSE="$DATABRICKS_AUTO_CREATE_WAREHOUSE"
  export DBX_WAREHOUSE_NAME="$DATABRICKS_WAREHOUSE_NAME"
  export DBX_WAREHOUSE_CLUSTER_SIZE="$DATABRICKS_WAREHOUSE_CLUSTER_SIZE"
  export DBX_WAREHOUSE_MIN_CLUSTERS="$DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS"
  export DBX_WAREHOUSE_MAX_CLUSTERS="$DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS"
  export DBX_WAREHOUSE_AUTO_STOP_MINS="$DATABRICKS_WAREHOUSE_AUTO_STOP_MINS"
  export DBX_WAREHOUSE_TYPE="$DATABRICKS_WAREHOUSE_TYPE"
  export DBX_WAREHOUSE_ENABLE_SERVERLESS="$DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS"
  python - <<'PY'
import json
import os
import urllib.error
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
auto_create = os.environ.get("DBX_AUTO_CREATE_WAREHOUSE", "").strip().lower() in {"1", "true", "yes", "on"}


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{host}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = detail or exc.reason or "request failed"
        raise SystemExit(f"Databricks SQL warehouse request failed ({exc.code}): {message}") from exc


payload = request("GET", "/api/2.0/sql/warehouses")

warehouses = payload.get("warehouses", [])
preferred = None
for warehouse in warehouses:
    state = str(warehouse.get("state", "")).upper()
    if state in {"RUNNING", "STARTING", "STARTED"}:
        preferred = warehouse
        break
if preferred is None and warehouses:
    preferred = warehouses[0]
if preferred is not None:
    print(preferred["id"])
    raise SystemExit(0)

if not auto_create:
    raise SystemExit(
        "No Databricks SQL warehouse was found. Set DATABRICKS_WAREHOUSE_ID, create a SQL warehouse in the workspace, "
        "or enable DATABRICKS_AUTO_CREATE_WAREHOUSE=true."
    )

create_payload = {
    "name": os.environ.get("DBX_WAREHOUSE_NAME", "dailyacctplanner-sql").strip() or "dailyacctplanner-sql",
    "cluster_size": os.environ.get("DBX_WAREHOUSE_CLUSTER_SIZE", "Small").strip() or "Small",
    "min_num_clusters": int(os.environ.get("DBX_WAREHOUSE_MIN_CLUSTERS", "1")),
    "max_num_clusters": int(os.environ.get("DBX_WAREHOUSE_MAX_CLUSTERS", "1")),
    "auto_stop_mins": int(os.environ.get("DBX_WAREHOUSE_AUTO_STOP_MINS", "10")),
    "warehouse_type": os.environ.get("DBX_WAREHOUSE_TYPE", "PRO").strip() or "PRO",
}
if os.environ.get("DBX_WAREHOUSE_ENABLE_SERVERLESS", "").strip().lower() in {"1", "true", "yes", "on"}:
    create_payload["enable_serverless_compute"] = True

try:
    created = request("POST", "/api/2.0/sql/warehouses", create_payload)
except SystemExit as exc:
    raise SystemExit(
        "No Databricks SQL warehouse was found, and automatic warehouse creation failed. "
        "Ensure the current identity can create SQL warehouses in Databricks, or set DATABRICKS_WAREHOUSE_ID to an existing warehouse.\n"
        f"{exc}"
    ) from exc

warehouse_id = str(created.get("id") or created.get("warehouse_id") or "").strip()
if not warehouse_id:
    raise SystemExit(
        "Databricks returned a successful warehouse-create response without a warehouse id. "
        "Create a warehouse manually and set DATABRICKS_WAREHOUSE_ID before rerunning."
    )
print(warehouse_id)
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

detect_workspace_catalog() {
  export DBX_WAREHOUSE_ID_CHECK="$DATABRICKS_WAREHOUSE_ID"
  export DBX_WORKSPACE_NAME_HINT="${DATABRICKS_WORKSPACE_NAME:-}"
  "$python_bin" - <<'PY'
import json
import os
import time
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
warehouse_id = os.environ["DBX_WAREHOUSE_ID_CHECK"]
workspace_name_hint = os.environ.get("DBX_WORKSPACE_NAME_HINT", "").strip().replace("-", "_")
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


payload = request(
    "POST",
    "/api/2.0/sql/statements",
    {
        "statement": "SHOW CATALOGS",
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
    raise SystemExit("")

catalog_rows = payload.get("result", {}).get("data_array") or []
catalogs = [str(row[0]).strip() for row in catalog_rows if row and str(row[0]).strip()]
preferred = {"samples", "system", "hive_metastore"}
workspace_catalogs = [item for item in catalogs if item not in preferred]
if workspace_name_hint and workspace_name_hint in workspace_catalogs:
    print(workspace_name_hint)
elif len(workspace_catalogs) == 1:
    print(workspace_catalogs[0])
PY
}

if [[ "$SECURE_MODE" != "true" && "${DATABRICKS_CATALOG:-veeam_demo}" == "veeam_demo" ]]; then
  detected_catalog="$(detect_workspace_catalog 2>/dev/null || true)"
  if [[ -n "$detected_catalog" ]]; then
    DATABRICKS_CATALOG="$detected_catalog"
    DATABRICKS_SKIP_CATALOG_CREATE="true"
    upsert_env_value "DATABRICKS_CATALOG" "$DATABRICKS_CATALOG"
    upsert_env_value "DATABRICKS_SKIP_CATALOG_CREATE" "$DATABRICKS_SKIP_CATALOG_CREATE"
    echo "Open Databricks seed detected workspace catalog '$DATABRICKS_CATALOG'; skipping catalog creation."
  fi
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
