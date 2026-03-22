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

WRAPPER_ACA_APP_NAME="${WRAPPER_ACA_APP_NAME:-daily-planner-m365}"
SECURE_MODE="false"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  SECURE_MODE="true"
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_LOCATION
  ACA_ENVIRONMENT_NAME
  WRAPPER_IMAGE
  AZURE_TENANT_ID
  BOT_APP_ID
  BOT_APP_PASSWORD
  PLANNER_API_EXPECTED_AUDIENCE
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null

mapfile -t registry_settings < <(resolve_registry_settings "$WRAPPER_IMAGE")
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

if [[ -z "${PLANNER_SERVICE_BASE_URL:-}" && "$SECURE_MODE" == "true" ]]; then
  planner_fqdn="$(get_resource_field \
    "$AZURE_RESOURCE_GROUP" \
    "${PLANNER_ACA_APP_NAME:-daily-account-planner-service}" \
    "Microsoft.App/containerApps" \
    "properties.configuration.ingress.fqdn" 2>/dev/null || true)"
  if [[ -n "$planner_fqdn" ]]; then
    PLANNER_SERVICE_BASE_URL="https://$planner_fqdn"
  fi
fi

if [[ -z "${PLANNER_SERVICE_BASE_URL:-}" ]]; then
  echo "PLANNER_SERVICE_BASE_URL is required in $ENV_FILE or the environment." >&2
  exit 1
fi

common_env_vars=(
  "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  "BOT_APP_ID=$BOT_APP_ID"
  "SECURE_DEPLOYMENT=$SECURE_MODE"
  "PLANNER_API_EXPECTED_AUDIENCE=$PLANNER_API_EXPECTED_AUDIENCE"
  "PLANNER_API_SCOPE=${PLANNER_API_SCOPE:-${PLANNER_API_EXPECTED_AUDIENCE}/access_as_user}"
  "PLANNER_SERVICE_BASE_URL=$PLANNER_SERVICE_BASE_URL"
  "AZUREBOTOAUTHCONNECTIONNAME=${AZUREBOTOAUTHCONNECTIONNAME:-SERVICE_CONNECTION}"
  "OBOCONNECTIONNAME=${OBOCONNECTIONNAME:-PLANNER_API_CONNECTION}"
  "M365_AUTH_HANDLER_ID=${M365_AUTH_HANDLER_ID:-planner_api}"
  "WRAPPER_FORWARD_TIMEOUT_SECONDS=${WRAPPER_FORWARD_TIMEOUT_SECONDS:-300}"
  "WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS=${WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS:-10}"
  "WRAPPER_ENABLE_LONG_RUNNING_MESSAGES=${WRAPPER_ENABLE_LONG_RUNNING_MESSAGES:-true}"
  "WRAPPER_ENABLE_DEBUG_CHAT=${WRAPPER_ENABLE_DEBUG_CHAT:-$SECURE_MODE}"
  "WRAPPER_DEBUG_ALLOWED_UPNS=${WRAPPER_DEBUG_ALLOWED_UPNS:-ri-test-na@m365cpi89838450.onmicrosoft.com,DaichiM@M365CPI89838450.OnMicrosoft.com}"
  "WRAPPER_DEBUG_EXPECTED_AUDIENCE=${WRAPPER_DEBUG_EXPECTED_AUDIENCE:-${BOT_SSO_RESOURCE:-api://botid-${BOT_APP_ID}}}"
)

secret_env_vars=(
  "BOT_APP_PASSWORD=secretref:bot-app-password"
)

if resource_exists "$AZURE_RESOURCE_GROUP" "$WRAPPER_ACA_APP_NAME" "Microsoft.App/containerApps"; then
  az containerapp secret set \
    --name "$WRAPPER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "bot-app-password=$BOT_APP_PASSWORD" \
    >/dev/null
  az containerapp update \
    --name "$WRAPPER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$WRAPPER_IMAGE" \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --set-env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
else
  az containerapp create \
    --name "$WRAPPER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$ACA_ENVIRONMENT_NAME" \
    --image "$WRAPPER_IMAGE" \
    "${registry_args[@]}" \
    --target-port 3978 \
    --ingress external \
    --min-replicas "${ACA_MIN_REPLICAS:-1}" \
    --max-replicas "${ACA_MAX_REPLICAS:-1}" \
    --secrets "bot-app-password=$BOT_APP_PASSWORD" \
    --env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
fi

fqdn="$(get_resource_field "$AZURE_RESOURCE_GROUP" "$WRAPPER_ACA_APP_NAME" "Microsoft.App/containerApps" "properties.configuration.ingress.fqdn")"
echo "Daily Account Planner M365 wrapper deployed to: https://$fqdn"
echo "Set WRAPPER_BASE_URL=https://$fqdn in $ENV_FILE and use https://$fqdn/api/messages for the Azure Bot endpoint."
