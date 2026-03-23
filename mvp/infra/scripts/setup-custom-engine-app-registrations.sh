#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi
DATABRICKS_RESOURCE_APP_ID="${DATABRICKS_RESOURCE_APP_ID:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d}"
BOT_SSO_RESOURCE_PREFIX="${BOT_SSO_RESOURCE_PREFIX:-api://botid-}"
TEAMS_DESKTOP_MOBILE_CLIENT_ID="${TEAMS_DESKTOP_MOBILE_CLIENT_ID:-1fec8e78-bce4-4aaf-ab1b-5451cc387264}"
TEAMS_WEB_CLIENT_ID="${TEAMS_WEB_CLIENT_ID:-5e3ce6c0-2b1f-4285-8d4b-75ee78787346}"
FAIL_ON_MISSING_ADMIN_CONSENT="${FAIL_ON_MISSING_ADMIN_CONSENT:-false}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ "${DEPLOYMENT_MODE,,}" == "secure" ]]; then
  APP_NAME_PREFIX="${APP_NAME_PREFIX:-daily-account-planner-secure}"
else
  APP_NAME_PREFIX="${APP_NAME_PREFIX:-daily-account-planner}"
fi

REUSE_PLANNER_API_APP_ID="${REUSE_PLANNER_API_APP_ID:-}"

if [[ -z "${AZURE_TENANT_ID:-}" ]]; then
  echo "AZURE_TENANT_ID must be set in $ENV_FILE or the environment." >&2
  exit 1
fi

az account show >/dev/null

load_existing_app() {
  local app_id="$1"
  az ad app show --id "$app_id" -o json
}

load_existing_app_by_identifiers() {
  local object_id="${1:-}"
  local app_id="${2:-}"

  if [[ -n "$object_id" ]]; then
    az ad app show --id "$object_id" -o json 2>/dev/null && return 0
  fi

  if [[ -n "$app_id" ]]; then
    az ad app show --id "$app_id" -o json 2>/dev/null && return 0
  fi

  return 1
}

find_single_app_by_display_name() {
  local display_name="$1"
  local label="$2"
  local matches
  matches="$(az ad app list --display-name "$display_name" -o json)"
  python - <<'PY' "$matches" "$display_name" "$label"
import json
import sys

matches = json.loads(sys.argv[1])
display_name = sys.argv[2]
label = sys.argv[3]
if not matches:
    raise SystemExit(1)
if len(matches) > 1:
    print(
        f"Multiple existing Entra applications matched display name '{display_name}' for {label}. "
        "Set the persisted app id/object id in the runtime env or remove the ambiguous apps before rerunning.",
        file=sys.stderr,
    )
    for item in matches:
        app_id = str(item.get("appId") or "")
        object_id = str(item.get("id") or "")
        print(f"- displayName={display_name} appId={app_id} objectId={object_id}", file=sys.stderr)
    raise SystemExit(2)
print(json.dumps(matches[0]))
PY
}

ensure_app() {
  local display_name="$1"
  local expose_as_api="$2"
  local existing_app_id="${3:-}"
  local existing_object_id="${4:-}"
  local label="${5:-$display_name}"
  local existing
  local lookup_status=0

  if existing="$(load_existing_app_by_identifiers "$existing_object_id" "$existing_app_id")"; then
    echo "$existing"
    return 0
  fi

  existing="$(find_single_app_by_display_name "$display_name" "$label")" || lookup_status=$?
  if [[ "$lookup_status" -eq 0 && -n "$existing" ]]; then
    echo "$existing"
    return 0
  fi
  if [[ "$lookup_status" -gt 1 ]]; then
    return "$lookup_status"
  fi

  local created
  created="$(az ad app create --display-name "$display_name" --sign-in-audience AzureADMyOrg -o json)"
  if [[ "$expose_as_api" == "true" ]]; then
    local app_id object_id
    app_id="$(python - <<'PY' "$created"
import json, sys
print(json.loads(sys.argv[1])["appId"])
PY
)"
    object_id="$(python - <<'PY' "$created"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
    az ad app update --id "$object_id" --identifier-uris "api://$app_id" >/dev/null
    created="$(az ad app show --id "$object_id" -o json)"
  fi
  echo "$created"
}

ensure_service_principal() {
  local app_id="$1"
  local existing
  existing="$(az ad sp list --filter "appId eq '$app_id'" --query "[0].id" -o tsv)"
  if [[ -z "$existing" ]]; then
    az ad sp create --id "$app_id" >/dev/null
  fi
}

patch_application() {
  local application_object_id="$1"
  local body_file="$2"
  az rest \
    --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/$application_object_id" \
    --headers "Content-Type=application/json" \
    --body "@$body_file" \
    >/dev/null
}

try_admin_consent() {
  local app_id="$1"
  local label="$2"
  local stderr_file
  stderr_file="$(mktemp)"
  if az ad app permission admin-consent --id "$app_id" 2>"$stderr_file" >/dev/null; then
    rm -f "$stderr_file"
    echo "granted"
    return 0
  fi

  cat >&2 <<EOF
Warning: automatic admin consent failed for $label.
Grant it manually with:
az ad app permission admin-consent --id $app_id
Reason:
$(sed 's/^/  /' "$stderr_file")
EOF
  rm -f "$stderr_file"
  echo "manual-required"
  return 1
}

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

if [[ -n "$REUSE_PLANNER_API_APP_ID" ]]; then
planner_json="$(load_existing_app "$REUSE_PLANNER_API_APP_ID")"
else
  planner_json="$(ensure_app \
    "$APP_NAME_PREFIX-planner-api" \
    true \
    "${PLANNER_API_CLIENT_ID:-}" \
    "${PLANNER_API_OBJECT_ID:-${PLANNER_API_APP_OBJECT_ID:-}}" \
    "planner API app")"
fi
bot_json="$(ensure_app \
  "$APP_NAME_PREFIX-bot" \
  false \
  "${BOT_APP_ID:-}" \
  "${BOT_APP_OBJECT_ID:-}" \
  "wrapper/bot app")"

planner_object_id="$(python - <<'PY' "$planner_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
planner_app_id="$(python - <<'PY' "$planner_json"
import json, sys
print(json.loads(sys.argv[1])["appId"])
PY
)"
bot_object_id="$(python - <<'PY' "$bot_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
bot_app_id="$(python - <<'PY' "$bot_json"
import json, sys
print(json.loads(sys.argv[1])["appId"])
PY
)"

ensure_service_principal "$planner_app_id"
ensure_service_principal "$bot_app_id"

scope_id="$(az ad app show --id "$planner_object_id" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
if [[ -z "$scope_id" ]]; then
  scope_id="$(python - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
  patch_file="$(mktemp)"
  cat >"$patch_file" <<JSON
{
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {
        "adminConsentDescription": "Access the Daily Account Planner API as the signed-in user.",
        "adminConsentDisplayName": "Access Daily Account Planner API",
        "id": "$scope_id",
        "isEnabled": true,
        "type": "User",
        "userConsentDescription": "Allow this app to access the Daily Account Planner API on your behalf.",
        "userConsentDisplayName": "Access Daily Account Planner API",
        "value": "access_as_user"
      }
    ]
  }
}
JSON
  patch_application "$planner_object_id" "$patch_file"
  rm -f "$patch_file"
fi

bot_scope_id="$(az ad app show --id "$bot_object_id" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
if [[ -z "$bot_scope_id" ]]; then
  bot_scope_id="$(python - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
fi

bot_sso_resource="${BOT_SSO_RESOURCE_PREFIX}${bot_app_id}"
bot_patch_file="$(mktemp)"
cat >"$bot_patch_file" <<JSON
{
  "identifierUris": [
    "$bot_sso_resource"
  ],
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {
        "adminConsentDescription": "Access the Daily Account Planner wrapper as the signed-in user.",
        "adminConsentDisplayName": "Access Daily Account Planner wrapper",
        "id": "$bot_scope_id",
        "isEnabled": true,
        "type": "User",
        "userConsentDescription": "Allow Teams and Copilot to sign you in to Daily Account Planner.",
        "userConsentDisplayName": "Sign in to Daily Account Planner",
        "value": "access_as_user"
      }
    ]
  },
  "web": {
    "redirectUris": [
      "https://token.botframework.com/.auth/web/redirect"
    ]
  }
}
JSON
patch_application "$bot_object_id" "$bot_patch_file"
rm -f "$bot_patch_file"

bot_preauth_file="$(mktemp)"
cat >"$bot_preauth_file" <<JSON
{
  "api": {
    "preAuthorizedApplications": [
      {
        "appId": "$TEAMS_DESKTOP_MOBILE_CLIENT_ID",
        "delegatedPermissionIds": [
          "$bot_scope_id"
        ]
      },
      {
        "appId": "$TEAMS_WEB_CLIENT_ID",
        "delegatedPermissionIds": [
          "$bot_scope_id"
        ]
      }
    ]
  }
}
JSON
patch_application "$bot_object_id" "$bot_preauth_file"
rm -f "$bot_preauth_file"

databricks_sp="$(az ad sp list --filter "appId eq '$DATABRICKS_RESOURCE_APP_ID'" --query "[0]" -o json)"
databricks_scope_id="$(python - <<'PY' "$databricks_sp"
import json, sys
sp = json.loads(sys.argv[1])
for item in sp.get("oauth2PermissionScopes", []):
    if item.get("value") == "user_impersonation":
        print(item["id"])
        break
PY
)"

planner_access_file="$(mktemp)"
cat >"$planner_access_file" <<JSON
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "$DATABRICKS_RESOURCE_APP_ID",
      "resourceAccess": [
        {
          "id": "$databricks_scope_id",
          "type": "Scope"
        }
      ]
    }
  ]
}
JSON
patch_application "$planner_object_id" "$planner_access_file"
rm -f "$planner_access_file"

wrapper_access_file="$(mktemp)"
cat >"$wrapper_access_file" <<JSON
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "$planner_app_id",
      "resourceAccess": [
        {
          "id": "$scope_id",
          "type": "Scope"
        }
      ]
    }
  ]
}
JSON
patch_application "$bot_object_id" "$wrapper_access_file"
rm -f "$wrapper_access_file"

planner_admin_consent_status="$(try_admin_consent "$planner_app_id" "Planner API -> Azure Databricks user_impersonation" || true)"
bot_admin_consent_status="$(try_admin_consent "$bot_app_id" "Wrapper/channel app -> Planner API access_as_user" || true)"

bot_secret_json="$(az ad app credential reset --id "$bot_object_id" --append --display-name "bot-secret" --years 1 -o json)"
bot_secret="$(python - <<'PY' "$bot_secret_json"
import json, sys
print(json.loads(sys.argv[1])["password"])
PY
)"

if [[ -n "$REUSE_PLANNER_API_APP_ID" ]]; then
  planner_secret="${PLANNER_API_CLIENT_SECRET:-}"
else
  planner_secret_json="$(az ad app credential reset --id "$planner_object_id" --append --display-name "planner-api-secret" --years 1 -o json)"
  planner_secret="$(python - <<'PY' "$planner_secret_json"
import json, sys
print(json.loads(sys.argv[1])["password"])
PY
)"
fi

planner_status="created-or-reused"
if [[ -n "$REUSE_PLANNER_API_APP_ID" ]]; then
  planner_status="reused-existing"
fi

upsert_env_value "PLANNER_API_CLIENT_ID" "$planner_app_id"
upsert_env_value "PLANNER_API_OBJECT_ID" "$planner_object_id"
upsert_env_value "PLANNER_API_CLIENT_SECRET" "$planner_secret"
upsert_env_value "PLANNER_API_EXPECTED_AUDIENCE" "api://$planner_app_id"
upsert_env_value "PLANNER_API_SCOPE" "api://$planner_app_id/access_as_user"
upsert_env_value "BOT_APP_ID" "$bot_app_id"
upsert_env_value "BOT_APP_OBJECT_ID" "$bot_object_id"
upsert_env_value "BOT_APP_PASSWORD" "$bot_secret"
upsert_env_value "BOT_SSO_APP_ID" "$bot_app_id"
upsert_env_value "BOT_SSO_RESOURCE" "$bot_sso_resource"
upsert_env_value "AZUREBOTOAUTHCONNECTIONNAME" "SERVICE_CONNECTION"
upsert_env_value "OBOCONNECTIONNAME" "PLANNER_API_CONNECTION"
upsert_env_value "M365_AUTH_HANDLER_ID" "planner_api"
upsert_env_value "WRAPPER_DEBUG_EXPECTED_AUDIENCE" "$bot_sso_resource"

cat <<EOF
Planner API app $planner_status.
M365 wrapper / bot app created or reused.

Updated $ENV_FILE with:
PLANNER_API_CLIENT_ID=$planner_app_id
PLANNER_API_OBJECT_ID=$planner_object_id
PLANNER_API_CLIENT_SECRET=$planner_secret
PLANNER_API_EXPECTED_AUDIENCE=api://$planner_app_id
PLANNER_API_SCOPE=api://$planner_app_id/access_as_user
BOT_APP_ID=$bot_app_id
BOT_APP_OBJECT_ID=$bot_object_id
BOT_APP_PASSWORD=$bot_secret
BOT_SSO_APP_ID=$bot_app_id
BOT_SSO_RESOURCE=$bot_sso_resource
AZUREBOTOAUTHCONNECTIONNAME=SERVICE_CONNECTION
OBOCONNECTIONNAME=PLANNER_API_CONNECTION
M365_AUTH_HANDLER_ID=planner_api

Admin consent status:
- Planner API -> Azure Databricks user_impersonation: $planner_admin_consent_status
- Wrapper/channel app -> Planner API access_as_user: $bot_admin_consent_status

If either value is manual-required, complete:
- az ad app permission admin-consent --id $planner_app_id
- az ad app permission admin-consent --id $bot_app_id
EOF

if [[ "$FAIL_ON_MISSING_ADMIN_CONSENT" == "true" && ( "$planner_admin_consent_status" != "granted" || "$bot_admin_consent_status" != "granted" ) ]]; then
  echo "Required Entra admin consent was not granted for the operator bootstrap. Resolve the consent commands above and rerun." >&2
  exit 1
fi
