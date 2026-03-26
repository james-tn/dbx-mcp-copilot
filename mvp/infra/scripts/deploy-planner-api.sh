#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}"
CONTAINERAPPS_JOB_API_VERSION="${CONTAINERAPPS_JOB_API_VERSION:-2025-07-01}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

env_file_declares_key() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] && rg -q "^${key}=" "$ENV_FILE"
}

# Hosted deployments should default to Entra ID for Azure OpenAI unless the
# runtime env file explicitly opts into API-key auth.
if ! env_file_declares_key "AZURE_OPENAI_API_KEY"; then
  unset AZURE_OPENAI_API_KEY || true
fi

CUSTOMER_DEPLOYMENT_MODE="false"
if [[ "${SECURE_DEPLOYMENT:-false}" =~ ^([Tt][Rr][Uu][Ee]|1|[Yy][Ee]?[Ss]|[Oo][Nn])$ || "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" || -n "${CUSTOMER_DATABRICKS_HOST:-}" ]]; then
  CUSTOMER_DEPLOYMENT_MODE="true"
  DATABRICKS_HOST="${CUSTOMER_DATABRICKS_HOST:-${DATABRICKS_HOST:-}}"
  DATABRICKS_WAREHOUSE_ID="${CUSTOMER_DATABRICKS_WAREHOUSE_ID:-${DATABRICKS_WAREHOUSE_ID:-}}"
  DATABRICKS_OBO_SCOPE="${CUSTOMER_DATABRICKS_OBO_SCOPE:-${DATABRICKS_OBO_SCOPE:-}}"
  DATABRICKS_AZURE_RESOURCE_ID="${CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID:-${DATABRICKS_AZURE_RESOURCE_ID:-}}"
fi

configured_databricks_host="${DATABRICKS_HOST:-}"

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

log_step() {
  echo "[deploy-planner-api] STEP: $*" >&2
}

log_success() {
  echo "[deploy-planner-api] OK: $*" >&2
}

log_warn() {
  echo "[deploy-planner-api] WARN: $*" >&2
}

fail_step() {
  echo "[deploy-planner-api] ERROR: $*" >&2
  exit 1
}

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

workspace_host_changed="false"
if [[ -n "${configured_databricks_host:-}" && "${DATABRICKS_HOST:-}" != "${configured_databricks_host:-}" ]]; then
  workspace_host_changed="true"
fi

resolve_databricks_token() {
  if [[ -n "${DATABRICKS_PAT:-}" ]]; then
    printf '%s\n' "$DATABRICKS_PAT"
    return 0
  fi

  az account get-access-token \
    --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
    --query accessToken \
    -o tsv
}

resolve_valid_warehouse_id() {
  local current_warehouse_id="${DATABRICKS_WAREHOUSE_ID:-}"
  local dbx_token=""
  dbx_token="$(resolve_databricks_token)"
  export DBX_HOST="$DATABRICKS_HOST"
  export DBX_TOKEN="$dbx_token"
  export DBX_CURRENT_WAREHOUSE_ID="$current_warehouse_id"
  export DBX_AUTO_CREATE_WAREHOUSE="${DATABRICKS_AUTO_CREATE_WAREHOUSE:-true}"
  export DBX_WAREHOUSE_NAME="${DATABRICKS_WAREHOUSE_NAME:-${INFRA_NAME_PREFIX:-dailyacctplanner}-sql}"
  export DBX_WAREHOUSE_CLUSTER_SIZE="${DATABRICKS_WAREHOUSE_CLUSTER_SIZE:-Small}"
  export DBX_WAREHOUSE_MIN_CLUSTERS="${DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS:-1}"
  export DBX_WAREHOUSE_MAX_CLUSTERS="${DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS:-1}"
  export DBX_WAREHOUSE_AUTO_STOP_MINS="${DATABRICKS_WAREHOUSE_AUTO_STOP_MINS:-10}"
  export DBX_WAREHOUSE_TYPE="${DATABRICKS_WAREHOUSE_TYPE:-PRO}"
  export DBX_WAREHOUSE_ENABLE_SERVERLESS="${DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS:-false}"

  python - <<'PY'
import json
import os
import urllib.error
import urllib.request

host = os.environ["DBX_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
current = os.environ.get("DBX_CURRENT_WAREHOUSE_ID", "").strip()
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
if current:
    for warehouse in warehouses:
        if str(warehouse.get("id", "")).strip() == current:
            preferred = warehouse
            break

if preferred is None:
    for warehouse in warehouses:
        state = str(warehouse.get("state", "")).upper()
        if state in {"RUNNING", "STARTING", "STARTED"}:
            preferred = warehouse
            break

if preferred is None and warehouses:
    preferred = warehouses[0]

warehouse_id = str((preferred or {}).get("id", "")).strip()
if warehouse_id:
    print(warehouse_id)
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
        "Ensure the operator can create SQL warehouses in Databricks, or set DATABRICKS_WAREHOUSE_ID to an existing warehouse.\n"
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

resolve_secure_workspace_catalog() {
  local workspace_name="$1"
  local workspace_id="$2"
  local configured_catalog="${3:-}"

  python - <<'PY' "$workspace_name" "$workspace_id" "$configured_catalog"
import re
import sys

workspace_name = sys.argv[1].strip()
workspace_id = sys.argv[2].strip()
configured_catalog = sys.argv[3].strip()

normalized_name = re.sub(r"[^0-9A-Za-z_]", "_", workspace_name)
workspace_catalog = f"{normalized_name}_{workspace_id}" if normalized_name and workspace_id else ""

print(workspace_catalog or configured_catalog or "veeam_demo")
PY
}

resource_exists() {
  local resource_group="$1"
  local resource_name="$2"
  local resource_type="$3"

  az resource show \
    --resource-group "$resource_group" \
    --name "$resource_name" \
    --resource-type "$resource_type" \
    >/dev/null 2>&1
}

get_resource_field() {
  local resource_group="$1"
  local resource_name="$2"
  local resource_type="$3"
  local query="$4"

  az resource show \
    --resource-group "$resource_group" \
    --name "$resource_name" \
    --resource-type "$resource_type" \
    --query "$query" \
    -o tsv
}

wait_for_containerapp_job() {
  local resource_group="$1"
  local job_name="$2"
  local timeout_seconds="${3:-300}"
  local deadline=$(( $(date +%s) + timeout_seconds ))

  while true; do
    if job_exists "$resource_group" "$job_name"; then
      return 0
    fi

    if [[ "$(date +%s)" -ge "$deadline" ]]; then
      echo "Timed out waiting for container app job '$job_name' in resource group '$resource_group'." >&2
      return 1
    fi
    sleep 5
  done
}

job_exists() {
  local resource_group="$1"
  local job_name="$2"
  az rest \
    --method get \
    --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${resource_group}/providers/Microsoft.App/jobs/${job_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
    >/dev/null 2>&1
}

identity_exists() {
  local resource_group="$1"
  local identity_name="$2"
  az identity show \
    --resource-group "$resource_group" \
    --name "$identity_name" \
    >/dev/null 2>&1
}

ensure_role_assignment_for_object_id() {
  local assignee_object_id="$1"
  local assignee_principal_type="$2"
  local role_name="$3"
  local scope="$4"
  local description="$5"
  local existing_count=""

  existing_count="$(az role assignment list \
    --assignee-object-id "$assignee_object_id" \
    --scope "$scope" \
    --query "[?roleDefinitionName=='$role_name'] | length(@)" \
    -o tsv 2>/dev/null || true)"
  if [[ "$existing_count" =~ ^[0-9]+$ ]] && [[ "$existing_count" -gt 0 ]]; then
    log_success "$description already present"
    return 0
  fi

  log_step "$description"
  if ! az role assignment create \
    --assignee-object-id "$assignee_object_id" \
    --assignee-principal-type "$assignee_principal_type" \
    --role "$role_name" \
    --scope "$scope" \
    >/dev/null 2>&1; then
    fail_step "$description failed. Ensure the signed-in operator can create Azure role assignments on scope '$scope'."
  fi
  log_success "$description"
}

verify_arm_resource_user_assigned_identity() {
  local resource_url="$1"
  local expected_identity_resource_id="$2"
  local resource_label="$3"
  local identity_json

  if ! identity_json="$(az rest --method get --url "$resource_url" --query identity -o json 2>/dev/null)"; then
    fail_step "Unable to read identity state for $resource_label."
  fi

  mapfile -t identity_state < <(python - <<'PY' "$identity_json" "$expected_identity_resource_id"
import json
import sys

payload = json.loads(sys.argv[1] or "{}")
expected = sys.argv[2].strip().lower()
identity_type = str(payload.get("type") or "").strip()
assigned = list((payload.get("userAssignedIdentities") or {}).keys())
assigned_normalized = {item.strip().lower() for item in assigned if str(item).strip()}
ok = bool(identity_type) and identity_type.lower() != "none" and (not expected or expected in assigned_normalized)
print("true" if ok else "false")
print(identity_type)
print(", ".join(assigned))
PY
)

  local identity_ok="${identity_state[0]:-false}"
  local identity_type="${identity_state[1]:-}"
  local assigned_identities="${identity_state[2]:-}"
  if [[ "$identity_ok" != "true" ]]; then
    fail_step "$resource_label is missing the expected user-assigned identity. identity.type='${identity_type:-<empty>}', assigned='${assigned_identities:-<none>}'"
  fi
}

ensure_user_assigned_identity() {
  local resource_group="$1"
  local identity_name="$2"

  if ! identity_exists "$resource_group" "$identity_name"; then
    log_step "Creating user-assigned managed identity '$identity_name'"
    az identity create \
      --resource-group "$resource_group" \
      --name "$identity_name" \
      --location "$AZURE_LOCATION" \
      >/dev/null
    log_success "Created user-assigned managed identity '$identity_name'"
  else
    log_success "Using existing user-assigned managed identity '$identity_name'"
  fi

  az identity show \
    --resource-group "$resource_group" \
    --name "$identity_name" \
    -o json
}

job_payload_path=""
cleanup_job_payload() {
  if [[ -n "$job_payload_path" && -f "$job_payload_path" ]]; then
    rm -f "$job_payload_path"
  fi
}
trap cleanup_job_payload EXIT

render_seed_job_payload() {
  local output_path="$1"
  python - <<'PY' "$output_path" "$AZURE_LOCATION" "$ACA_ENVIRONMENT_NAME" "$AZURE_SUBSCRIPTION_ID" "$AZURE_RESOURCE_GROUP" "$PLANNER_API_IMAGE" "${registry_settings[0]:-}" "${registry_settings[1]:-}" "${registry_settings[2]:-}" "$PLANNER_API_CLIENT_SECRET" "${DATABRICKS_SEED_TIMEOUT_SECONDS:-1800}" "${PLANNER_ACA_APP_NAME}" "${PLANNER_SEED_COMMAND}" "$(printf '%s\n' "${common_env_vars[@]}")" "$(printf '%s\n' "${seed_job_env_vars[@]}")"
import json
import sys

(
    output_path,
    azure_location,
    aca_environment_name,
    subscription_id,
    resource_group,
    planner_api_image,
    registry_server,
    registry_username,
    registry_password,
    planner_api_client_secret,
    replica_timeout,
    planner_app_name,
    planner_seed_command,
    common_env_serialized,
    seed_env_serialized,
) = sys.argv[1:]

environment_id = (
    f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    f"/providers/Microsoft.App/managedEnvironments/{aca_environment_name}"
)

def parse_env_items(serialized: str):
    items = []
    for raw in serialized.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env_item = {"name": key}
        if value.startswith("secretref:"):
            env_item["secretRef"] = value[len("secretref:"):]
        else:
            env_item["value"] = value
        items.append(env_item)
    return items

command = ["/bin/sh"]
args = ["-c", planner_seed_command]

configuration = {
    "triggerType": "Manual",
    "replicaTimeout": int(replica_timeout),
    "replicaRetryLimit": 0,
    "manualTriggerConfig": {
        "parallelism": 1,
        "replicaCompletionCount": 1,
    },
    "secrets": [
        {
            "name": "planner-api-client-secret",
            "value": planner_api_client_secret,
        }
    ],
}

if registry_server and registry_username and registry_password:
    configuration["secrets"].append(
        {
            "name": "registry-password",
            "value": registry_password,
        }
    )
    configuration["registries"] = [
        {
            "server": registry_server,
            "username": registry_username,
            "passwordSecretRef": "registry-password",
        }
    ]

template = {
    "containers": [
        {
            "name": planner_app_name,
            "image": planner_api_image,
            "command": command,
            "args": args,
            "env": parse_env_items(common_env_serialized) + parse_env_items(seed_env_serialized),
            "resources": {
                "cpu": 1.0,
                "memory": "2Gi",
            },
        }
    ]
}

payload = {
    "location": azure_location,
    "identity": {
        "type": "None",
    },
    "properties": {
        "environmentId": environment_id,
        "configuration": configuration,
        "template": template,
    },
}

with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

create_or_update_seed_job_via_rest() {
  local seed_job_name="$1"
  job_payload_path="$(mktemp)"
  render_seed_job_payload "$job_payload_path"

  az rest \
    --method put \
    --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/jobs/${seed_job_name}?api-version=${CONTAINERAPPS_JOB_API_VERSION}" \
    --body @"$job_payload_path" \
    >/dev/null
}

resolve_registry_settings() {
  local image_ref="$1"
  local server="${CONTAINER_REGISTRY_SERVER:-}"
  local username="${CONTAINER_REGISTRY_USERNAME:-}"
  local password="${CONTAINER_REGISTRY_PASSWORD:-}"

  if [[ -z "$server" ]]; then
    server="${image_ref%%/*}"
  fi

  if [[ "$server" == "$image_ref" || "$server" != *.* ]]; then
    return 0
  fi

  if [[ -n "$username" && -n "$password" ]]; then
    echo "$server"$'\n'"$username"$'\n'"$password"
    return 0
  fi

  if [[ "$server" == *.azurecr.io ]]; then
    local acr_name="${server%%.azurecr.io}"
    username="$(az acr credential show --name "$acr_name" --query username -o tsv)"
    password="$(az acr credential show --name "$acr_name" --query 'passwords[0].value' -o tsv)"
    echo "$server"$'\n'"$username"$'\n'"$password"
    return 0
  fi

  echo "$server"$'\n'"$username"$'\n'"$password"
}

derive_demo_user_csv() {
  if [[ -n "${DATABRICKS_WORKSPACE_USER_UPNS:-}" ]]; then
    printf '%s\n' "$DATABRICKS_WORKSPACE_USER_UPNS"
    return 0
  fi
  if [[ -n "${SELLER_A_UPN:-}" && -n "${SELLER_B_UPN:-}" ]]; then
    printf '%s,%s\n' "$SELLER_A_UPN" "$SELLER_B_UPN"
    return 0
  fi
  printf '\n'
}

PLANNER_ACA_APP_NAME="${PLANNER_ACA_APP_NAME:-${ACA_APP_NAME:-daily-account-planner-service}}"
AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-${AZURE_OPENAI_MODEL:-}}"
BOOTSTRAP_MANAGED_IDENTITY_NAME="${DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_NAME:-${PLANNER_ACA_APP_NAME}-seed-mi}"
SECURE_MODE="false"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  SECURE_MODE="true"
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_LOCATION
  ACA_ENVIRONMENT_NAME
  PLANNER_API_IMAGE
  AZURE_TENANT_ID
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_DEPLOYMENT
  PLANNER_API_CLIENT_ID
  PLANNER_API_CLIENT_SECRET
  PLANNER_API_EXPECTED_AUDIENCE
  DATABRICKS_HOST
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

if [[ "$CUSTOMER_DEPLOYMENT_MODE" == "true" ]]; then
  customer_required_vars=(
    CUSTOMER_DATABRICKS_HOST
  )

  for var_name in "${customer_required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
      echo "$var_name is required for secure customer planner deployment." >&2
      exit 1
    fi
  done
fi

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null

DATABRICKS_AUTO_CREATE_WAREHOUSE="${DATABRICKS_AUTO_CREATE_WAREHOUSE:-true}"
DATABRICKS_WAREHOUSE_NAME="${DATABRICKS_WAREHOUSE_NAME:-${INFRA_NAME_PREFIX:-$PLANNER_ACA_APP_NAME}-sql}"
DATABRICKS_WAREHOUSE_CLUSTER_SIZE="${DATABRICKS_WAREHOUSE_CLUSTER_SIZE:-Small}"
DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS="${DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS:-1}"
DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS="${DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS:-1}"
DATABRICKS_WAREHOUSE_AUTO_STOP_MINS="${DATABRICKS_WAREHOUSE_AUTO_STOP_MINS:-10}"
DATABRICKS_WAREHOUSE_TYPE="${DATABRICKS_WAREHOUSE_TYPE:-PRO}"
DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS="${DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS:-false}"
upsert_env_value "DATABRICKS_AUTO_CREATE_WAREHOUSE" "$DATABRICKS_AUTO_CREATE_WAREHOUSE"
upsert_env_value "DATABRICKS_WAREHOUSE_NAME" "$DATABRICKS_WAREHOUSE_NAME"
upsert_env_value "DATABRICKS_WAREHOUSE_CLUSTER_SIZE" "$DATABRICKS_WAREHOUSE_CLUSTER_SIZE"
upsert_env_value "DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS" "$DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS"
upsert_env_value "DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS" "$DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS"
upsert_env_value "DATABRICKS_WAREHOUSE_AUTO_STOP_MINS" "$DATABRICKS_WAREHOUSE_AUTO_STOP_MINS"
upsert_env_value "DATABRICKS_WAREHOUSE_TYPE" "$DATABRICKS_WAREHOUSE_TYPE"
upsert_env_value "DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS" "$DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS"

effective_databricks_catalog="${DATABRICKS_CATALOG:-veeam_demo}"
if [[ "$CUSTOMER_DEPLOYMENT_MODE" == "true" ]]; then
  effective_databricks_catalog="${DATABRICKS_CATALOG:-customer_existing_databricks}"
fi
effective_skip_catalog_create="${DATABRICKS_SKIP_CATALOG_CREATE:-}"
if [[ -z "$effective_skip_catalog_create" && "$SECURE_MODE" == "true" ]]; then
  effective_skip_catalog_create="true"
fi
effective_skip_catalog_create="${effective_skip_catalog_create:-false}"
upsert_env_value "DATABRICKS_SKIP_CATALOG_CREATE" "$effective_skip_catalog_create"
if [[ "$CUSTOMER_DEPLOYMENT_MODE" != "true" && "$SECURE_MODE" == "true" && -n "${DATABRICKS_RESOURCE_GROUP:-}" && -n "${DATABRICKS_WORKSPACE_NAME:-}" && "$effective_skip_catalog_create" == "true" ]]; then
  databricks_workspace_id="$(
    az databricks workspace show \
    --resource-group "$DATABRICKS_RESOURCE_GROUP" \
      --name "$DATABRICKS_WORKSPACE_NAME" \
      --query workspaceId \
      -o tsv
  )"
  effective_databricks_catalog="$(resolve_secure_workspace_catalog "$DATABRICKS_WORKSPACE_NAME" "$databricks_workspace_id" "${DATABRICKS_CATALOG:-}")"
  DATABRICKS_CATALOG="$effective_databricks_catalog"
  echo "Using secure Databricks workspace catalog: $effective_databricks_catalog"
fi

upsert_env_value "DATABRICKS_HOST" "$DATABRICKS_HOST"
upsert_env_value "DATABRICKS_CATALOG" "$effective_databricks_catalog"
upsert_env_value "DATABRICKS_WAREHOUSE_ID" "${DATABRICKS_WAREHOUSE_ID:-}"
if [[ "$SECURE_MODE" == "true" ]]; then
  if [[ "$workspace_host_changed" == "true" ]]; then
    DATABRICKS_WAREHOUSE_ID=""
    DATABRICKS_BOOTSTRAP_WAREHOUSE_ID=""
    upsert_env_value "DATABRICKS_WAREHOUSE_ID" ""
    upsert_env_value "DATABRICKS_BOOTSTRAP_WAREHOUSE_ID" ""
  fi
else
  DATABRICKS_WAREHOUSE_ID="$(resolve_valid_warehouse_id)"
  upsert_env_value "DATABRICKS_WAREHOUSE_ID" "$DATABRICKS_WAREHOUSE_ID"
  upsert_env_value "DATABRICKS_BOOTSTRAP_WAREHOUSE_ID" "$DATABRICKS_WAREHOUSE_ID"
fi

mapfile -t registry_settings < <(resolve_registry_settings "$PLANNER_API_IMAGE")
registry_args=()
if [[ -n "${registry_settings[0]:-}" && -n "${registry_settings[1]:-}" && -n "${registry_settings[2]:-}" ]]; then
  registry_args=(
    --registry-server "${registry_settings[0]}"
    --registry-username "${registry_settings[1]}"
    --registry-password "${registry_settings[2]}"
  )
fi

if ! resource_exists "$AZURE_RESOURCE_GROUP" "$ACA_ENVIRONMENT_NAME" "Microsoft.App/managedEnvironments"; then
  if [[ "$SECURE_MODE" == "true" ]]; then
    if [[ -z "${SECURE_ACA_SUBNET_ID:-}" ]]; then
      echo "SECURE_ACA_SUBNET_ID is required when DEPLOYMENT_MODE=secure." >&2
      exit 1
    fi
    az containerapp env create \
      --name "$ACA_ENVIRONMENT_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --infrastructure-subnet-resource-id "$SECURE_ACA_SUBNET_ID" \
      >/dev/null
  else
    az containerapp env create \
      --name "$ACA_ENVIRONMENT_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      >/dev/null
  fi
fi

common_env_vars=(
  "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT"
  "AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT"
  "AZURE_OPENAI_TIMEOUT_SECONDS=${AZURE_OPENAI_TIMEOUT_SECONDS:-120}"
  "AZURE_OPENAI_MAX_RETRIES=${AZURE_OPENAI_MAX_RETRIES:-6}"
  "AZURE_OPENAI_RATE_LIMIT_RETRY_COUNT=${AZURE_OPENAI_RATE_LIMIT_RETRY_COUNT:-4}"
  "AZURE_OPENAI_RATE_LIMIT_BACKOFF_SECONDS=${AZURE_OPENAI_RATE_LIMIT_BACKOFF_SECONDS:-2}"
  "PLANNER_API_CLIENT_ID=$PLANNER_API_CLIENT_ID"
  "PLANNER_API_EXPECTED_AUDIENCE=$PLANNER_API_EXPECTED_AUDIENCE"
  "DATABRICKS_HOST=$DATABRICKS_HOST"
  "DATABRICKS_CATALOG=${effective_databricks_catalog}"
  "DATABRICKS_SKIP_CATALOG_CREATE=${effective_skip_catalog_create}"
  "DATABRICKS_OBO_SCOPE=${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
  "DATABRICKS_WAREHOUSE_ID=${DATABRICKS_WAREHOUSE_ID:-}"
  "DATABRICKS_AUTO_CREATE_WAREHOUSE=${DATABRICKS_AUTO_CREATE_WAREHOUSE}"
  "DATABRICKS_WAREHOUSE_NAME=${DATABRICKS_WAREHOUSE_NAME}"
  "DATABRICKS_WAREHOUSE_CLUSTER_SIZE=${DATABRICKS_WAREHOUSE_CLUSTER_SIZE}"
  "DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS=${DATABRICKS_WAREHOUSE_MIN_NUM_CLUSTERS}"
  "DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS=${DATABRICKS_WAREHOUSE_MAX_NUM_CLUSTERS}"
  "DATABRICKS_WAREHOUSE_AUTO_STOP_MINS=${DATABRICKS_WAREHOUSE_AUTO_STOP_MINS}"
  "DATABRICKS_WAREHOUSE_TYPE=${DATABRICKS_WAREHOUSE_TYPE}"
  "DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS=${DATABRICKS_WAREHOUSE_ENABLE_SERVERLESS}"
  "DATABRICKS_SQL_TIMEOUT_SECONDS=${DATABRICKS_SQL_TIMEOUT_SECONDS:-30}"
  "DATABRICKS_SQL_RETRY_COUNT=${DATABRICKS_SQL_RETRY_COUNT:-1}"
  "DATABRICKS_SQL_POLL_ATTEMPTS=${DATABRICKS_SQL_POLL_ATTEMPTS:-6}"
  "DATABRICKS_SQL_POLL_INTERVAL_SECONDS=${DATABRICKS_SQL_POLL_INTERVAL_SECONDS:-1}"
  "SESSION_STORE_MODE=${SESSION_STORE_MODE:-memory}"
  "SESSION_MAX_TURNS=${SESSION_MAX_TURNS:-20}"
  "SECURE_DEPLOYMENT=$SECURE_MODE"
  "RI_SCOPE_MODE=${RI_SCOPE_MODE:-user}"
  "RI_DEMO_TERRITORY=${RI_DEMO_TERRITORY:-GreatLakes-ENT-Named-1}"
  "ACCOUNT_PULSE_EXECUTION_MODE=${ACCOUNT_PULSE_EXECUTION_MODE:-dynamic_parallel}"
  "ACCOUNT_PULSE_MAX_CONCURRENCY=${ACCOUNT_PULSE_MAX_CONCURRENCY:-8}"
  "ACCOUNT_PULSE_MAX_MODEL_CONCURRENCY=${ACCOUNT_PULSE_MAX_MODEL_CONCURRENCY:-3}"
  "ACCOUNT_PULSE_SOURCE_MODE=${ACCOUNT_PULSE_SOURCE_MODE:-live}"
  "ACCOUNT_PULSE_REPLAY_FIXTURE_SET=${ACCOUNT_PULSE_REPLAY_FIXTURE_SET:-small_parent_set}"
  "ACCOUNT_PULSE_ENABLE_INTERNAL_AGGREGATOR=${ACCOUNT_PULSE_ENABLE_INTERNAL_AGGREGATOR:-true}"
  "DAP_API_BASE_URL=${DAP_API_BASE_URL:-}"
  "DAP_API_SCOPE=${DAP_API_SCOPE:-}"
  "DAP_API_EXPECTED_AUDIENCE=${DAP_API_EXPECTED_AUDIENCE:-}"
  "DAP_API_AUTH_MODE=${DAP_API_AUTH_MODE:-obo}"
  "DAP_API_TOKEN_HEADER_MODE=${DAP_API_TOKEN_HEADER_MODE:-authorization}"
  "DAP_HEALTHCHECK_PATH=${DAP_HEALTHCHECK_PATH:-/api/v1/healthcheck}"
  "DAP_ACCOUNTS_QUERY_PATH=${DAP_ACCOUNTS_QUERY_PATH:-/api/v1/accounts/query}"
  "DAP_DEBUG_HEADERS_PATH=${DAP_DEBUG_HEADERS_PATH:-/api/v1/debug/headers}"
  "CUSTOMER_DATABRICKS_HOST=${CUSTOMER_DATABRICKS_HOST:-}"
  "CUSTOMER_DATABRICKS_WAREHOUSE_ID=${CUSTOMER_DATABRICKS_WAREHOUSE_ID:-}"
  "CUSTOMER_DATABRICKS_OBO_SCOPE=${CUSTOMER_DATABRICKS_OBO_SCOPE:-}"
  "CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID=${CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID:-}"
  "CUSTOMER_TOP_OPPORTUNITIES_SOURCE=${CUSTOMER_TOP_OPPORTUNITIES_SOURCE:-}"
  "CUSTOMER_TOP_OPPORTUNITIES_QUERY=${CUSTOMER_TOP_OPPORTUNITIES_QUERY:-}"
  "CUSTOMER_TOP_OPPORTUNITIES_CATALOG=${CUSTOMER_TOP_OPPORTUNITIES_CATALOG:-}"
  "CUSTOMER_TOP_OPPORTUNITIES_SCHEMA=${CUSTOMER_TOP_OPPORTUNITIES_SCHEMA:-}"
  "CUSTOMER_TOP_OPPORTUNITIES_TABLE=${CUSTOMER_TOP_OPPORTUNITIES_TABLE:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_SOURCE=${CUSTOMER_SCOPE_ACCOUNTS_SOURCE:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH=${CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_QUERY=${CUSTOMER_SCOPE_ACCOUNTS_QUERY:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_CATALOG=${CUSTOMER_SCOPE_ACCOUNTS_CATALOG:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_SCHEMA=${CUSTOMER_SCOPE_ACCOUNTS_SCHEMA:-}"
  "CUSTOMER_SCOPE_ACCOUNTS_TABLE=${CUSTOMER_SCOPE_ACCOUNTS_TABLE:-}"
  "CUSTOMER_CONTACTS_SOURCE=${CUSTOMER_CONTACTS_SOURCE:-}"
  "CUSTOMER_CONTACTS_QUERY=${CUSTOMER_CONTACTS_QUERY:-}"
  "CUSTOMER_CONTACTS_CATALOG=${CUSTOMER_CONTACTS_CATALOG:-}"
  "CUSTOMER_CONTACTS_SCHEMA=${CUSTOMER_CONTACTS_SCHEMA:-}"
  "CUSTOMER_CONTACTS_TABLE=${CUSTOMER_CONTACTS_TABLE:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_SOURCE=${CUSTOMER_SALES_TEAM_MAPPING_SOURCE:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_QUERY=${CUSTOMER_SALES_TEAM_MAPPING_QUERY:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_CATALOG=${CUSTOMER_SALES_TEAM_MAPPING_CATALOG:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_SCHEMA=${CUSTOMER_SALES_TEAM_MAPPING_SCHEMA:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_TABLE=${CUSTOMER_SALES_TEAM_MAPPING_TABLE:-}"
  "CUSTOMER_SALES_TEAM_MAPPING_USER_COLUMN=${CUSTOMER_SALES_TEAM_MAPPING_USER_COLUMN:-user_upn}"
  "CUSTOMER_SALES_TEAM_MAPPING_TEAM_COLUMN=${CUSTOMER_SALES_TEAM_MAPPING_TEAM_COLUMN:-sales_team}"
  "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH=${CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH:-}"
  "CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH=${CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH:-}"
  "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON=${CUSTOMER_SALES_TEAM_STATIC_MAP_JSON:-}"
  "CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON=${CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON:-}"
)

secret_env_vars=(
  "PLANNER_API_CLIENT_SECRET=secretref:planner-api-client-secret"
)

secret_pairs=(
  "planner-api-client-secret=$PLANNER_API_CLIENT_SECRET"
)

if [[ "$SECURE_MODE" != "true" && -n "${AZURE_OPENAI_API_KEY:-}" ]]; then
  secret_env_vars+=(
    "AZURE_OPENAI_API_KEY=secretref:azure-openai-api-key"
  )
  secret_pairs+=(
    "azure-openai-api-key=$AZURE_OPENAI_API_KEY"
  )
fi

bootstrap_identity_resource_id=""
bootstrap_identity_client_id=""
bootstrap_identity_principal_id=""
demo_workspace_user_upns="$(derive_demo_user_csv)"

remove_env_args=(
  --remove-env-vars
  AZURE_OPENAI_API_KEY
  AZURE_CLIENT_ID
  AZURE_CLIENT_SECRET
)

assign_openai_managed_identity() {
  local app_name="$1"
  local openai_scope="$2"

  az containerapp identity assign \
    --name "$app_name" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --system-assigned \
    >/dev/null

  local principal_id=""
  principal_id="$(az containerapp show \
    --name "$app_name" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --query identity.principalId \
    -o tsv 2>/dev/null || true)"

  if [[ -n "$principal_id" && -n "$openai_scope" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "Cognitive Services OpenAI User" \
      --scope "$openai_scope" \
      >/dev/null 2>&1 || true
  fi
}

openai_resource_id=""
if [[ -n "${AZURE_OPENAI_ACCOUNT_NAME:-}" ]]; then
  openai_resource_id="$(az cognitiveservices account show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_OPENAI_ACCOUNT_NAME" \
    --query id \
    -o tsv)"
fi

if resource_exists "$AZURE_RESOURCE_GROUP" "$PLANNER_ACA_APP_NAME" "Microsoft.App/containerApps"; then
  az containerapp secret set \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "${secret_pairs[@]}" \
    >/dev/null
  az containerapp update \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$PLANNER_API_IMAGE" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    "${remove_env_args[@]}" \
    --set-env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
else
  ingress_mode="external"
  if [[ "$SECURE_MODE" == "true" ]]; then
    ingress_mode="internal"
  fi
  az containerapp create \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$ACA_ENVIRONMENT_NAME" \
    --image "$PLANNER_API_IMAGE" \
    "${registry_args[@]}" \
    --target-port 8080 \
    --ingress "$ingress_mode" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --secrets "${secret_pairs[@]}" \
    --env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
fi

assign_openai_managed_identity "$PLANNER_ACA_APP_NAME" "$openai_resource_id"

fqdn="$(get_resource_field "$AZURE_RESOURCE_GROUP" "$PLANNER_ACA_APP_NAME" "Microsoft.App/containerApps" "properties.configuration.ingress.fqdn")"
base_url="https://$fqdn"
upsert_env_value "PLANNER_API_BASE_URL" "$base_url"
if [[ "$SECURE_MODE" != "true" ]]; then
  upsert_env_value "PLANNER_SERVICE_BASE_URL" "$base_url"
fi
echo "Daily Account Planner planner service deployed to: $base_url"
echo "Set PLANNER_API_BASE_URL=$base_url in $ENV_FILE for validation."
