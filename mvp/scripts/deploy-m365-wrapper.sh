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

WRAPPER_ACA_APP_NAME="${WRAPPER_ACA_APP_NAME:-daily-planner-m365}"

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
  PLANNER_SERVICE_BASE_URL
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

if ! az containerapp env show --name "$ACA_ENVIRONMENT_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp env create \
    --name "$ACA_ENVIRONMENT_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    >/dev/null
fi

common_env_vars=(
  "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  "BOT_APP_ID=$BOT_APP_ID"
  "PLANNER_API_EXPECTED_AUDIENCE=$PLANNER_API_EXPECTED_AUDIENCE"
  "PLANNER_API_SCOPE=${PLANNER_API_SCOPE:-${PLANNER_API_EXPECTED_AUDIENCE}/access_as_user}"
  "PLANNER_SERVICE_BASE_URL=$PLANNER_SERVICE_BASE_URL"
  "AZUREBOTOAUTHCONNECTIONNAME=${AZUREBOTOAUTHCONNECTIONNAME:-SERVICE_CONNECTION}"
  "OBOCONNECTIONNAME=${OBOCONNECTIONNAME:-PLANNER_API_CONNECTION}"
  "M365_AUTH_HANDLER_ID=${M365_AUTH_HANDLER_ID:-planner_api}"
  "WRAPPER_FORWARD_TIMEOUT_SECONDS=${WRAPPER_FORWARD_TIMEOUT_SECONDS:-45}"
)

secret_env_vars=(
  "BOT_APP_PASSWORD=secretref:bot-app-password"
)

if az containerapp show --name "$WRAPPER_ACA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  if [[ ${#registry_args[@]} -gt 0 ]]; then
    az containerapp registry set \
      --name "$WRAPPER_ACA_APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --server "${registry_settings[0]}" \
      --username "${registry_settings[1]}" \
      --password "${registry_settings[2]}" \
      >/dev/null
  fi
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

fqdn="$(az containerapp show --name "$WRAPPER_ACA_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)"
echo "Daily Account Planner M365 wrapper deployed to: https://$fqdn"
echo "Set WRAPPER_BASE_URL=https://$fqdn in $ENV_FILE and use https://$fqdn/api/messages for the Azure Bot endpoint."
