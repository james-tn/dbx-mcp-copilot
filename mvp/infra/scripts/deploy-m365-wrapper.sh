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

derive_demo_user_csv() {
  if [[ -n "${WRAPPER_DEBUG_ALLOWED_UPNS:-}" ]]; then
    printf '%s\n' "$WRAPPER_DEBUG_ALLOWED_UPNS"
    return 0
  fi
  if [[ -n "${SELLER_A_UPN:-}" && -n "${SELLER_B_UPN:-}" ]]; then
    printf '%s,%s\n' "$SELLER_A_UPN" "$SELLER_B_UPN"
    return 0
  fi
  printf '\n'
}

WRAPPER_ACA_APP_NAME="${WRAPPER_ACA_APP_NAME:-daily-planner-m365}"
SECURE_MODE="false"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  SECURE_MODE="true"
fi
BOT_AUTH_TYPE="${BOT_AUTH_TYPE:-}"
if [[ -z "$BOT_AUTH_TYPE" ]]; then
  if [[ -n "${BOT_APP_PASSWORD:-}" ]]; then
    BOT_AUTH_TYPE="client_secret"
  else
    BOT_AUTH_TYPE="user_managed_identity"
  fi
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_LOCATION
  ACA_ENVIRONMENT_NAME
  WRAPPER_IMAGE
  AZURE_TENANT_ID
  BOT_APP_ID
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

if [[ -z "${PLANNER_SERVICE_BASE_URL:-}" && "$SECURE_MODE" != "true" && -n "${PLANNER_API_BASE_URL:-}" ]]; then
  PLANNER_SERVICE_BASE_URL="$PLANNER_API_BASE_URL"
fi

if [[ -n "${PLANNER_SERVICE_BASE_URL:-}" ]]; then
  upsert_env_value "PLANNER_SERVICE_BASE_URL" "$PLANNER_SERVICE_BASE_URL"
fi

if [[ -z "${PLANNER_SERVICE_BASE_URL:-}" ]]; then
  echo "PLANNER_SERVICE_BASE_URL is required in $ENV_FILE or the environment." >&2
  exit 1
fi

demo_debug_allowed_upns="$(derive_demo_user_csv)"
common_env_vars=(
  "AZURE_TENANT_ID=$AZURE_TENANT_ID"
  "BOT_APP_ID=$BOT_APP_ID"
  "BOT_AUTH_TYPE=$BOT_AUTH_TYPE"
  "BOT_MANAGED_IDENTITY_CLIENT_ID=${BOT_MANAGED_IDENTITY_CLIENT_ID:-}"
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
  "WRAPPER_DEBUG_ALLOWED_UPNS=${demo_debug_allowed_upns}"
  "WRAPPER_DEBUG_EXPECTED_AUDIENCE=${WRAPPER_DEBUG_EXPECTED_AUDIENCE:-${BOT_SSO_RESOURCE:-api://botid-${BOT_APP_ID}}}"
)

secret_env_vars=()
secret_pairs=()
if [[ "$BOT_AUTH_TYPE" == "client_secret" ]]; then
  if [[ -z "${BOT_APP_PASSWORD:-}" ]]; then
    echo "BOT_APP_PASSWORD is required when BOT_AUTH_TYPE=client_secret." >&2
    exit 1
  fi
  secret_env_vars=(
    "BOT_APP_PASSWORD=secretref:bot-app-password"
  )
  secret_pairs=(
    "bot-app-password=$BOT_APP_PASSWORD"
  )
elif [[ "$BOT_AUTH_TYPE" == "user_managed_identity" ]]; then
  if [[ -z "${BOT_MANAGED_IDENTITY_CLIENT_ID:-}" ]]; then
    echo "BOT_MANAGED_IDENTITY_CLIENT_ID is required when BOT_AUTH_TYPE=user_managed_identity." >&2
    exit 1
  fi
  if [[ -z "${BOT_MANAGED_IDENTITY_RESOURCE_ID:-}" ]]; then
    echo "BOT_MANAGED_IDENTITY_RESOURCE_ID is required when BOT_AUTH_TYPE=user_managed_identity for hosted wrapper deployment." >&2
    exit 1
  fi
fi

if resource_exists "$AZURE_RESOURCE_GROUP" "$WRAPPER_ACA_APP_NAME" "Microsoft.App/containerApps"; then
  if [[ "${#secret_pairs[@]}" -gt 0 ]]; then
    az containerapp secret set \
      --name "$WRAPPER_ACA_APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --secrets "${secret_pairs[@]}" \
      >/dev/null
  fi
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
    --env-vars "${common_env_vars[@]}" "${secret_env_vars[@]}" \
    >/dev/null
fi

if [[ "$BOT_AUTH_TYPE" == "user_managed_identity" && -n "${BOT_MANAGED_IDENTITY_RESOURCE_ID:-}" ]]; then
  az containerapp identity assign \
    --name "$WRAPPER_ACA_APP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --user-assigned "$BOT_MANAGED_IDENTITY_RESOURCE_ID" \
    >/dev/null
fi

fqdn="$(get_resource_field "$AZURE_RESOURCE_GROUP" "$WRAPPER_ACA_APP_NAME" "Microsoft.App/containerApps" "properties.configuration.ingress.fqdn")"
upsert_env_value "WRAPPER_BASE_URL" "https://$fqdn"
echo "Daily Account Planner M365 wrapper deployed to: https://$fqdn"
echo "Set WRAPPER_BASE_URL=https://$fqdn in $ENV_FILE and use https://$fqdn/api/messages for the Azure Bot endpoint."
