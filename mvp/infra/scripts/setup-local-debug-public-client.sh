#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

APP_NAME_PREFIX="${APP_NAME_PREFIX:-${INFRA_NAME_PREFIX:-daily-account-planner}}"
LOCAL_DEBUG_PUBLIC_CLIENT_DISPLAY_NAME="${LOCAL_DEBUG_PUBLIC_CLIENT_DISPLAY_NAME:-$APP_NAME_PREFIX-local-debug-public-client}"
LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH_DEFAULT="${LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH:-$HOME/.cache/daily-account-planner/local_debug_token_cache.json}"

if [[ -z "${AZURE_TENANT_ID:-}" ]]; then
  echo "AZURE_TENANT_ID must be set in $ENV_FILE or the environment." >&2
  exit 1
fi
if [[ -z "${BOT_APP_ID:-}" ]]; then
  echo "BOT_APP_ID must be set in $ENV_FILE or the environment. Run setup-custom-engine-app-registrations first." >&2
  exit 1
fi

BOT_SSO_RESOURCE_VALUE="${BOT_SSO_RESOURCE:-api://botid-${BOT_APP_ID}}"
LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE_VALUE="${LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE:-${BOT_SSO_RESOURCE_VALUE}/access_as_user}"

az account show >/dev/null

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
        "Set LOCAL_DEBUG_PUBLIC_CLIENT_ID or LOCAL_DEBUG_PUBLIC_CLIENT_OBJECT_ID in the env file before rerunning.",
        file=sys.stderr,
    )
    for item in matches:
        print(
            f"- displayName={display_name} appId={item.get('appId','')} objectId={item.get('id','')}",
            file=sys.stderr,
        )
    raise SystemExit(2)
print(json.dumps(matches[0]))
PY
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

ensure_service_principal() {
  local app_id="$1"
  local existing
  existing="$(az ad sp list --filter "appId eq '$app_id'" --query "[0].id" -o tsv)"
  if [[ -z "$existing" ]]; then
    az ad sp create --id "$app_id" >/dev/null
  fi
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
rendered = f"{key}={value}"
updated = False
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

bot_json="$(load_existing_app_by_identifiers "${BOT_APP_OBJECT_ID:-}" "${BOT_APP_ID:-}")"
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
bot_scope_id="$(az ad app show --id "$bot_object_id" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
if [[ -z "$bot_scope_id" ]]; then
  echo "Could not find bot access_as_user scope on BOT_APP_ID=$bot_app_id." >&2
  echo "Run setup-custom-engine-app-registrations first so the wrapper API scope exists." >&2
  exit 1
fi

local_client_json=""
lookup_status=0
if local_client_json="$(load_existing_app_by_identifiers "${LOCAL_DEBUG_PUBLIC_CLIENT_OBJECT_ID:-}" "${LOCAL_DEBUG_PUBLIC_CLIENT_ID:-}")"; then
  :
else
  local_client_json="$(find_single_app_by_display_name "$LOCAL_DEBUG_PUBLIC_CLIENT_DISPLAY_NAME" "local debug public client")" || lookup_status=$?
  if [[ "$lookup_status" -eq 1 ]]; then
    local_client_json="$(az ad app create --display-name "$LOCAL_DEBUG_PUBLIC_CLIENT_DISPLAY_NAME" --sign-in-audience AzureADMyOrg -o json)"
  elif [[ "$lookup_status" -gt 1 ]]; then
    exit "$lookup_status"
  fi
fi

local_client_object_id="$(python - <<'PY' "$local_client_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
local_client_app_id="$(python - <<'PY' "$local_client_json"
import json, sys
print(json.loads(sys.argv[1])["appId"])
PY
)"

ensure_service_principal "$local_client_app_id"

local_client_patch_file="$(mktemp)"
python - <<'PY' "$local_client_json" "$bot_app_id" "$bot_scope_id" >"$local_client_patch_file"
import json
import sys

app = json.loads(sys.argv[1])
bot_app_id = sys.argv[2]
bot_scope_id = sys.argv[3]

required = list(app.get("requiredResourceAccess") or [])
updated = False
for entry in required:
    if entry.get("resourceAppId") != bot_app_id:
        continue
    resource_access = list(entry.get("resourceAccess") or [])
    if not any(item.get("id") == bot_scope_id and item.get("type") == "Scope" for item in resource_access):
        resource_access.append({"id": bot_scope_id, "type": "Scope"})
    entry["resourceAccess"] = resource_access
    updated = True
    break

if not updated:
    required.append(
        {
            "resourceAppId": bot_app_id,
            "resourceAccess": [
                {"id": bot_scope_id, "type": "Scope"},
            ],
        }
    )

print(json.dumps({"isFallbackPublicClient": True, "requiredResourceAccess": required}))
PY
patch_application "$local_client_object_id" "$local_client_patch_file"
rm -f "$local_client_patch_file"

bot_preauth_json="$(az ad app show --id "$bot_object_id" -o json)"
bot_preauth_patch_file="$(mktemp)"
python - <<'PY' "$bot_preauth_json" "$local_client_app_id" "$bot_scope_id" >"$bot_preauth_patch_file"
import json
import sys

bot = json.loads(sys.argv[1])
client_app_id = sys.argv[2]
scope_id = sys.argv[3]

entries = list((bot.get("api") or {}).get("preAuthorizedApplications") or [])
found = False
for entry in entries:
    if entry.get("appId") != client_app_id:
        continue
    delegated = list(entry.get("delegatedPermissionIds") or [])
    if scope_id not in delegated:
        delegated.append(scope_id)
    entry["delegatedPermissionIds"] = delegated
    found = True
    break

if not found:
    entries.append({"appId": client_app_id, "delegatedPermissionIds": [scope_id]})

print(json.dumps({"api": {"preAuthorizedApplications": entries}}))
PY
patch_application "$bot_object_id" "$bot_preauth_patch_file"
rm -f "$bot_preauth_patch_file"

admin_consent_status="$(try_admin_consent "$local_client_app_id" "Local debug public client -> Wrapper/bot access_as_user" || true)"

upsert_env_value "LOCAL_DEBUG_PUBLIC_CLIENT_ID" "$local_client_app_id"
upsert_env_value "LOCAL_DEBUG_PUBLIC_CLIENT_OBJECT_ID" "$local_client_object_id"
upsert_env_value "LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE" "$LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE_VALUE"
upsert_env_value "LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH" "$LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH_DEFAULT"
upsert_env_value "LOCAL_DEBUG_PUBLIC_CLIENT_ADMIN_CONSENT_STATUS" "$admin_consent_status"

cat <<EOF
Local debug public client created or reused.

Updated $ENV_FILE with:
LOCAL_DEBUG_PUBLIC_CLIENT_ID=$local_client_app_id
LOCAL_DEBUG_PUBLIC_CLIENT_OBJECT_ID=$local_client_object_id
LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE=$LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE_VALUE
LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH=$LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH_DEFAULT

Admin consent status:
- Local debug public client -> Wrapper/bot access_as_user: $admin_consent_status

Token helper:
python mvp/scripts/get-local-debug-token.py
EOF
