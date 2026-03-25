#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
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

MCP_ACA_APP_NAME="${MCP_ACA_APP_NAME:-daily-account-planner-mcp}"
SECURE_MODE="false"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  SECURE_MODE="true"
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_LOCATION
  ACA_ENVIRONMENT_NAME
  MCP_IMAGE
  AZURE_TENANT_ID
  MCP_CLIENT_ID
  MCP_EXPECTED_AUDIENCE
  TOP_OPPORTUNITIES_APP_BASE_URL
  DATABRICKS_HOST
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

if [[ -z "${MCP_CLIENT_SECRET:-}" && -z "${MCP_MANAGED_IDENTITY_CLIENT_ID:-}" ]]; then
  echo "Either MCP_CLIENT_SECRET or MCP_MANAGED_IDENTITY_CLIENT_ID is required for hosted MCP Databricks OBO." >&2
  exit 1
fi

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null

mapfile -t registry_settings < <(resolve_registry_settings "$MCP_IMAGE")
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
  "MCP_CLIENT_ID=$MCP_CLIENT_ID"
  "MCP_EXPECTED_AUDIENCE=$MCP_EXPECTED_AUDIENCE"
  "MCP_MANAGED_IDENTITY_CLIENT_ID=${MCP_MANAGED_IDENTITY_CLIENT_ID:-}"
  "MCP_CLIENT_ASSERTION_SCOPE=${MCP_CLIENT_ASSERTION_SCOPE:-api://AzureADTokenExchange/.default}"
  "DATABRICKS_HOST=$DATABRICKS_HOST"
  "DATABRICKS_CATALOG=${DATABRICKS_CATALOG:-veeam_demo}"
  "DATABRICKS_OBO_SCOPE=${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
  "DATABRICKS_WAREHOUSE_ID=${DATABRICKS_WAREHOUSE_ID:-}"
  "DATABRICKS_SQL_TIMEOUT_SECONDS=${DATABRICKS_SQL_TIMEOUT_SECONDS:-30}"
  "DATABRICKS_SQL_RETRY_COUNT=${DATABRICKS_SQL_RETRY_COUNT:-1}"
  "DATABRICKS_SQL_POLL_ATTEMPTS=${DATABRICKS_SQL_POLL_ATTEMPTS:-6}"
  "DATABRICKS_SQL_POLL_INTERVAL_SECONDS=${DATABRICKS_SQL_POLL_INTERVAL_SECONDS:-1}"
  "RI_SCOPE_MODE=${RI_SCOPE_MODE:-user}"
  "RI_DEMO_TERRITORY=${RI_DEMO_TERRITORY:-GreatLakes-ENT-Named-1}"
  "SECURE_DEPLOYMENT=$SECURE_MODE"
  "TOP_OPPORTUNITIES_APP_BASE_URL=$TOP_OPPORTUNITIES_APP_BASE_URL"
  "TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE=${TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE:-$MCP_EXPECTED_AUDIENCE}"
)

secret_env_vars=()
secret_pairs=()
if [[ -n "${MCP_CLIENT_SECRET:-}" ]]; then
  secret_env_vars+=("MCP_CLIENT_SECRET=secretref:mcp-client-secret")
  secret_pairs+=("mcp-client-secret=$MCP_CLIENT_SECRET")
fi
if [[ -n "${DATABRICKS_PAT:-}" ]]; then
  secret_env_vars+=("DATABRICKS_PAT=secretref:databricks-pat")
  secret_pairs+=("databricks-pat=$DATABRICKS_PAT")
fi

if resource_exists "$AZURE_RESOURCE_GROUP" "$MCP_ACA_APP_NAME" "Microsoft.App/containerApps"; then
  if [[ "${#secret_pairs[@]}" -gt 0 ]]; then
    az containerapp secret set \
      --name "$MCP_ACA_APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --secrets "${secret_pairs[@]}" \
      >/dev/null
  fi
  az containerapp update \
    --name "$MCP_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$MCP_IMAGE" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --set-env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
else
  ingress_mode="external"
  if [[ "$SECURE_MODE" == "true" ]]; then
    ingress_mode="internal"
  fi
  az containerapp create \
    --name "$MCP_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$ACA_ENVIRONMENT_NAME" \
    --image "$MCP_IMAGE" \
    "${registry_args[@]}" \
    --target-port 8001 \
    --ingress "$ingress_mode" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
fi

if [[ -n "${MCP_MANAGED_IDENTITY_RESOURCE_ID:-}" ]]; then
  az containerapp identity assign \
    --name "$MCP_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --user-assigned "$MCP_MANAGED_IDENTITY_RESOURCE_ID" \
    >/dev/null
fi

fqdn="$(get_resource_field "$AZURE_RESOURCE_GROUP" "$MCP_ACA_APP_NAME" "Microsoft.App/containerApps" "properties.configuration.ingress.fqdn")"
base_url="https://$fqdn/mcp"
upsert_env_value "MCP_BASE_URL" "$base_url"
echo "Daily Account Planner MCP deployed to: $base_url"
echo "Set MCP_BASE_URL=$base_url in $ENV_FILE for planner runtime use."
