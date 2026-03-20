#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
APP_NAME_PREFIX="${APP_NAME_PREFIX:-daily-account-planner}"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
PERMISSION_MODE="${PERMISSION_MODE:-readwrite}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "${AZURE_TENANT_ID:-}" ]]; then
  echo "AZURE_TENANT_ID must be set in $ENV_FILE or the environment." >&2
  exit 1
fi

az account show >/dev/null

app_name="$APP_NAME_PREFIX-m365-cli"
app_json="$(az ad app list --display-name "$app_name" --query "[0]" -o json)"
if [[ "$app_json" == "null" || -z "$app_json" ]]; then
  app_json="$(az ad app create --display-name "$app_name" --sign-in-audience AzureADMyOrg -o json)"
fi

app_object_id="$(python - <<'PY' "$app_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
app_id="$(python - <<'PY' "$app_json"
import json, sys
print(json.loads(sys.argv[1])["appId"])
PY
)"

patch_file="$(mktemp)"
cat >"$patch_file" <<JSON
{
  "isFallbackPublicClient": true,
  "publicClient": {
    "redirectUris": [
      "http://localhost"
    ]
  }
}
JSON
az rest \
  --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$app_object_id" \
  --headers "Content-Type=application/json" \
  --body "@$patch_file" \
  >/dev/null
rm -f "$patch_file"

graph_sp_file="$(mktemp)"
az ad sp list --filter "appId eq '$GRAPH_APP_ID'" --query "[0]" -o json >"$graph_sp_file"

resolve_scope_id() {
  local scope_name="$1"
  python - <<'PY' "$graph_sp_file" "$scope_name"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    sp = json.load(handle)
target = sys.argv[2]
for item in sp.get("oauth2PermissionScopes", []):
    if item.get("value") == target:
        print(item["id"])
        break
PY
}

publish_scope_name="AppCatalog.ReadWrite.All"
if [[ "$PERMISSION_MODE" == "submit" ]]; then
  publish_scope_name="AppCatalog.Submit"
fi

publish_scope_id="$(resolve_scope_id "$publish_scope_name")"
install_scope_name="TeamsAppInstallation.ReadWriteForUser"
install_scope_id="$(resolve_scope_id "$install_scope_name")"
user_read_scope_id="$(resolve_scope_id "User.Read")"

required_access_file="$(mktemp)"
cat >"$required_access_file" <<JSON
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "$GRAPH_APP_ID",
      "resourceAccess": [
        {
          "id": "$publish_scope_id",
          "type": "Scope"
        },
        {
          "id": "$install_scope_id",
          "type": "Scope"
        },
        {
          "id": "$user_read_scope_id",
          "type": "Scope"
        }
      ]
    }
  ]
}
JSON

az rest \
  --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$app_object_id" \
  --headers "Content-Type=application/json" \
  --body "@$required_access_file" \
  >/dev/null
rm -f "$required_access_file"

if az ad app permission admin-consent --id "$app_id" >/dev/null 2>&1; then
  admin_consent_status="granted"
else
  admin_consent_status="pending"
fi

cat <<EOF
M365 CLI publisher app created or reused.

Add this value to $ENV_FILE:
M365_GRAPH_PUBLISHER_CLIENT_ID=$app_id

Graph delegated permissions configured:
- $publish_scope_name
- $install_scope_name
- User.Read

Admin consent status: $admin_consent_status
EOF

rm -f "$graph_sp_file"
