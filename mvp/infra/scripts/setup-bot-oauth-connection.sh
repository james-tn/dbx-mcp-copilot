#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
APP_NAME_PREFIX="${APP_NAME_PREFIX:-daily-account-planner}"
BOT_RESOURCE_NAME="${BOT_RESOURCE_NAME:-dailyplannerbot2026}"
BOT_OAUTH_CONNECTION_NAME="${BOT_OAUTH_CONNECTION_NAME:-${AZUREBOTOAUTHCONNECTIONNAME:-SERVICE_CONNECTION}}"
FAIL_ON_MISSING_ADMIN_CONSENT="${FAIL_ON_MISSING_ADMIN_CONSENT:-false}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

required_vars=(
  AZURE_TENANT_ID
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  BOT_APP_ID
  BOT_APP_PASSWORD
  BOT_SSO_RESOURCE
  PLANNER_API_CLIENT_ID
  PLANNER_API_SCOPE
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az account show >/dev/null

if az ad app permission admin-consent --id "$BOT_APP_ID" >/dev/null 2>&1; then
  admin_consent_status="granted"
else
  admin_consent_status="pending"
fi

if az bot authsetting show -g "$AZURE_RESOURCE_GROUP" -n "$BOT_RESOURCE_NAME" -c "$BOT_OAUTH_CONNECTION_NAME" >/dev/null 2>&1; then
  az bot authsetting delete -g "$AZURE_RESOURCE_GROUP" -n "$BOT_RESOURCE_NAME" -c "$BOT_OAUTH_CONNECTION_NAME" >/dev/null
fi

az bot authsetting create \
  -g "$AZURE_RESOURCE_GROUP" \
  -n "$BOT_RESOURCE_NAME" \
  -c "$BOT_OAUTH_CONNECTION_NAME" \
  --service Aadv2 \
  --client-id "$BOT_APP_ID" \
  --client-secret "$BOT_APP_PASSWORD" \
  --provider-scope-string "$PLANNER_API_SCOPE offline_access openid profile" \
  --parameters TenantId="$AZURE_TENANT_ID" TokenExchangeUrl="$BOT_SSO_RESOURCE" \
  >/dev/null

cat <<EOF
Azure Bot OAuth connection created or updated.

Bot resource:
BOT_RESOURCE_NAME=$BOT_RESOURCE_NAME
OAuth connection name:
AZUREBOTOAUTHCONNECTIONNAME=$BOT_OAUTH_CONNECTION_NAME

OAuth client:
BOT_APP_ID=$BOT_APP_ID

Admin consent status:
$admin_consent_status
EOF

if [[ "$FAIL_ON_MISSING_ADMIN_CONSENT" == "true" && "$admin_consent_status" != "granted" ]]; then
  echo "Required Entra admin consent is still pending for BOT_APP_ID=$BOT_APP_ID. Complete 'az ad app permission admin-consent --id $BOT_APP_ID' and rerun." >&2
  exit 1
fi
