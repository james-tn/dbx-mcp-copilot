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

PLANNER_ACA_APP_NAME="${PLANNER_ACA_APP_NAME:-${ACA_APP_NAME:-daily-account-planner-service}}"
AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-${AZURE_OPENAI_MODEL:-}}"

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

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null

mapfile -t registry_settings < <(resolve_registry_settings "$PLANNER_API_IMAGE")
registry_args=()
if [[ -n "${registry_settings[0]:-}" && -n "${registry_settings[1]:-}" && -n "${registry_settings[2]:-}" ]]; then
  registry_args=(
    --registry-server "${registry_settings[0]}"
    --registry-username "${registry_settings[1]}"
    --registry-password "${registry_settings[2]}"
  )
fi

if ! az containerapp env show --name "$ACA_ENVIRONMENT_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp env create \
    --name "$ACA_ENVIRONMENT_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    >/dev/null
fi

common_env_vars=(
  "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT"
  "AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT"
  "PLANNER_API_CLIENT_ID=$PLANNER_API_CLIENT_ID"
  "PLANNER_API_EXPECTED_AUDIENCE=$PLANNER_API_EXPECTED_AUDIENCE"
  "DATABRICKS_HOST=$DATABRICKS_HOST"
  "DATABRICKS_OBO_SCOPE=${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
  "DATABRICKS_WAREHOUSE_ID=${DATABRICKS_WAREHOUSE_ID:-}"
  "DATABRICKS_SQL_TIMEOUT_SECONDS=${DATABRICKS_SQL_TIMEOUT_SECONDS:-30}"
  "DATABRICKS_SQL_RETRY_COUNT=${DATABRICKS_SQL_RETRY_COUNT:-1}"
  "DATABRICKS_SQL_POLL_ATTEMPTS=${DATABRICKS_SQL_POLL_ATTEMPTS:-6}"
  "DATABRICKS_SQL_POLL_INTERVAL_SECONDS=${DATABRICKS_SQL_POLL_INTERVAL_SECONDS:-1}"
  "SESSION_STORE_MODE=${SESSION_STORE_MODE:-memory}"
  "SESSION_MAX_TURNS=${SESSION_MAX_TURNS:-20}"
  "RI_SCOPE_MODE=${RI_SCOPE_MODE:-user}"
  "RI_DEMO_TERRITORY=${RI_DEMO_TERRITORY:-GreatLakes-ENT-Named-1}"
  "ACCOUNT_PULSE_EXECUTION_MODE=${ACCOUNT_PULSE_EXECUTION_MODE:-legacy_sequential}"
  "ACCOUNT_PULSE_MAX_CONCURRENCY=${ACCOUNT_PULSE_MAX_CONCURRENCY:-8}"
  "ACCOUNT_PULSE_SOURCE_MODE=${ACCOUNT_PULSE_SOURCE_MODE:-live}"
  "ACCOUNT_PULSE_REPLAY_FIXTURE_SET=${ACCOUNT_PULSE_REPLAY_FIXTURE_SET:-small_parent_set}"
  "ACCOUNT_PULSE_ENABLE_INTERNAL_AGGREGATOR=${ACCOUNT_PULSE_ENABLE_INTERNAL_AGGREGATOR:-true}"
)

secret_env_vars=(
  "PLANNER_API_CLIENT_SECRET=secretref:planner-api-client-secret"
)

if az containerapp show --name "$PLANNER_ACA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  if [[ ${#registry_args[@]} -gt 0 ]]; then
    az containerapp registry set \
      --name "$PLANNER_ACA_APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --server "${registry_settings[0]}" \
      --username "${registry_settings[1]}" \
      --password "${registry_settings[2]}" \
      >/dev/null
  fi
  az containerapp secret set \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "planner-api-client-secret=$PLANNER_API_CLIENT_SECRET" \
    >/dev/null
  az containerapp update \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$PLANNER_API_IMAGE" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --set-env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
else
  az containerapp create \
    --name "$PLANNER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$ACA_ENVIRONMENT_NAME" \
    --image "$PLANNER_API_IMAGE" \
    "${registry_args[@]}" \
    --target-port 8080 \
    --ingress external \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --secrets \
      "planner-api-client-secret=$PLANNER_API_CLIENT_SECRET" \
    --env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
fi

fqdn="$(az containerapp show --name "$PLANNER_ACA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)"
echo "Daily Account Planner planner service deployed to: https://$fqdn"
echo "Set PLANNER_API_BASE_URL=https://$fqdn in $ENV_FILE for validation and M365 publishing steps."
