#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  BOT_RESOURCE_NAME
  BOT_APP_ID
  AZURE_TENANT_ID
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

if [[ -z "${WRAPPER_BASE_URL:-}" && -n "${WRAPPER_ACA_APP_NAME:-}" ]]; then
  wrapper_fqdn="$(
    az resource show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$WRAPPER_ACA_APP_NAME" \
      --resource-type Microsoft.App/containerApps \
      --query properties.configuration.ingress.fqdn \
      -o tsv 2>/dev/null || true
  )"
  if [[ -n "$wrapper_fqdn" ]]; then
    WRAPPER_BASE_URL="https://$wrapper_fqdn"
  fi
fi

if [[ -z "${WRAPPER_BASE_URL:-}" ]]; then
  echo "WRAPPER_BASE_URL is required in $ENV_FILE or the environment." >&2
  exit 1
fi

if ! az resource show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BOT_RESOURCE_NAME" \
  --resource-type Microsoft.BotService/botServices \
  >/dev/null 2>&1; then
  az bot create \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$BOT_RESOURCE_NAME" \
    --appid "$BOT_APP_ID" \
    --app-type SingleTenant \
    --tenant-id "$AZURE_TENANT_ID" \
    --endpoint "$WRAPPER_BASE_URL/api/messages" \
    --display-name "${BOT_DISPLAY_NAME:-Daily Planner Bot}" \
    --description "${BOT_DESCRIPTION:-Bot service for Daily Account Planner wrapper}" \
    --sku "${BOT_SKU:-F0}" \
    >/dev/null
fi

az bot update \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BOT_RESOURCE_NAME" \
  --endpoint "$WRAPPER_BASE_URL/api/messages" \
  >/dev/null

az bot msteams create \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BOT_RESOURCE_NAME" \
  >/dev/null 2>&1 || true

cat <<EOF
Azure Bot resource created or updated.
BOT_RESOURCE_NAME=$BOT_RESOURCE_NAME
BOT_APP_ID=$BOT_APP_ID
BOT_ENDPOINT=$WRAPPER_BASE_URL/api/messages
EOF
