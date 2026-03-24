#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PACKAGE_PATH="${PACKAGE_PATH:-$ROOT_DIR/appPackage/build/daily-account-planner-m365.zip}"
MANIFEST_PATH="${MANIFEST_PATH:-$ROOT_DIR/appPackage/build/manifest.json}"
GRAPH_TOKEN_HELPER="${GRAPH_TOKEN_HELPER:-$ROOT_DIR/scripts/get-graph-delegated-token.py}"
GRAPH_SCOPES="${GRAPH_SCOPES:-https://graph.microsoft.com/AppCatalog.ReadWrite.All https://graph.microsoft.com/User.Read}"
REQUIRES_REVIEW="${REQUIRES_REVIEW:-false}"
OUTPUT_PATH="${OUTPUT_PATH:-$ROOT_DIR/appPackage/build/graph-upload-response.json}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

log_step() {
  echo "[publish-m365-app-package-graph] STEP: $*" >&2
}

log_success() {
  echo "[publish-m365-app-package-graph] OK: $*" >&2
}

if [[ ! -f "$PACKAGE_PATH" ]]; then
  echo "App package not found at $PACKAGE_PATH. Run build-m365-app-package.sh first." >&2
  exit 1
fi

resolve_app_package_id() {
  if [[ -n "${M365_APP_PACKAGE_ID:-}" ]]; then
    printf '%s\n' "$M365_APP_PACKAGE_ID"
    return 0
  fi
  if [[ ! -f "$MANIFEST_PATH" ]]; then
    echo "M365_APP_PACKAGE_ID is required when $MANIFEST_PATH does not exist." >&2
    exit 1
  fi
  python - <<'PY' "$MANIFEST_PATH"
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["id"])
PY
}

if [[ -n "${GRAPH_ACCESS_TOKEN:-}" ]]; then
  GRAPH_TOKEN="$GRAPH_ACCESS_TOKEN"
elif [[ -n "${M365_GRAPH_PUBLISHER_CLIENT_ID:-}" ]]; then
  GRAPH_TOKEN="$(python "$GRAPH_TOKEN_HELPER" --tenant-id "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}" --client-id "$M365_GRAPH_PUBLISHER_CLIENT_ID" --scopes "$GRAPH_SCOPES")"
else
  GRAPH_TOKEN="$(az account get-access-token --resource-type ms-graph --query accessToken -o tsv)"
fi
SCOPES="$(python - <<'PY' "$GRAPH_TOKEN"
import base64
import json
import sys

token = sys.argv[1]
parts = token.split(".")
payload = parts[1] + "=" * (-len(parts[1]) % 4)
data = json.loads(base64.urlsafe_b64decode(payload))
print(data.get("scp", ""))
PY
)"

if [[ "$SCOPES" != *"AppCatalog.Submit"* && "$SCOPES" != *"AppCatalog.ReadWrite.All"* && "$SCOPES" != *"Directory.ReadWrite.All"* ]]; then
  echo "Current Microsoft Graph token is missing a Teams app catalog publish scope." >&2
  echo "Required delegated scope: AppCatalog.Submit (or AppCatalog.ReadWrite.All / Directory.ReadWrite.All)." >&2
  echo "Current token scopes: $SCOPES" >&2
  exit 2
fi
log_success "Resolved Graph publish token with required catalog scope"

response_file="$(mktemp)"
app_lookup_file="$(mktemp)"
upload_url="https://graph.microsoft.com/v1.0/appCatalogs/teamsApps"
if [[ "$REQUIRES_REVIEW" == "true" ]]; then
  upload_url="${upload_url}?requiresReview=true"
fi

lookup_existing_app() {
  local external_id="$1"
  local lookup_status
  log_step "Looking up existing Teams catalog app for externalId=$external_id"
  lookup_status="$(curl -sS -o "$app_lookup_file" -w "%{http_code}" \
    --get "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps" \
    -H "Authorization: Bearer $GRAPH_TOKEN" \
    --data-urlencode "\$filter=externalId eq '$external_id'")"
  if [[ "$lookup_status" -lt 200 || "$lookup_status" -ge 300 ]]; then
    cat "$app_lookup_file" >&2
    echo >&2
    echo "HTTP_STATUS=$lookup_status" >&2
    exit 1
  fi
  log_success "Looked up existing Teams catalog app for externalId=$external_id"
  python - <<'PY' "$app_lookup_file"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = payload.get("value", [])
if not values:
    raise SystemExit(1)
entry = values[0]
print(entry.get("id", ""))
print(entry.get("externalId", ""))
print(json.dumps(entry))
PY
}

response_contains() {
  local needle="$1"
  local path="$2"
  grep -q "$needle" "$path" 2>/dev/null
}

write_output_summary() {
  local source_path="$1"
  cp "$source_path" "$OUTPUT_PATH"
  python - <<'PY' "$source_path"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
body = path.read_text(encoding="utf-8").strip()
if not body:
    print("{}")
    raise SystemExit(0)
try:
    parsed = json.loads(body)
except json.JSONDecodeError:
    print(body)
    raise SystemExit(0)

print(json.dumps(parsed, indent=2))
app_id = parsed.get("id")
external_id = parsed.get("externalId")
if app_id:
    print(f"GRAPH_TEAMS_APP_ID={app_id}")
if external_id:
    print(f"GRAPH_EXTERNAL_APP_ID={external_id}")
PY
}

status_code="$(curl -sS -o "$response_file" -w "%{http_code}" \
  -X POST "$upload_url" \
  -H "Authorization: Bearer $GRAPH_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary "@$PACKAGE_PATH")"
log_step "Initial Graph upload returned HTTP $status_code"

mkdir -p "$(dirname "$OUTPUT_PATH")"
if [[ "$status_code" == "409" ]]; then
  log_step "Initial Graph upload reported an existing catalog entry; attempting appDefinition update"
  app_package_id="$(resolve_app_package_id)"
  existing_app_info="$(lookup_existing_app "$app_package_id" || true)"
  existing_app_id="$(printf '%s\n' "$existing_app_info" | sed -n '1p')"
  if [[ -z "$existing_app_id" ]]; then
    cat "$response_file" >&2
    echo >&2
    echo "HTTP_STATUS=$status_code" >&2
    exit 1
  fi

  update_url="https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/${existing_app_id}/appDefinitions"
  if [[ "$REQUIRES_REVIEW" == "true" ]]; then
    update_url="${update_url}?requiresReview=true"
  fi

  update_response_file="$(mktemp)"
  update_status="$(curl -sS -o "$update_response_file" -w "%{http_code}" \
    -X POST "$update_url" \
    -H "Authorization: Bearer $GRAPH_TOKEN" \
    -H "Content-Type: application/zip" \
    --data-binary "@$PACKAGE_PATH")"
  if [[ "$update_status" -lt 200 || "$update_status" -ge 300 ]]; then
    if [[ "$update_status" == "409" ]] && (
      response_contains "TenantAppDefinitionManifestVersionAlreadyExists" "$update_response_file" ||
      response_contains "manifest version exists" "$update_response_file"
    ); then
      status_code="200"
    else
      cat "$update_response_file" >&2
      echo >&2
      echo "HTTP_STATUS=$update_status" >&2
      exit 1
    fi
  else
    status_code="$update_status"
  fi
  log_success "Graph catalog update path completed with HTTP $status_code"

  app_lookup_json="$(printf '%s\n' "$existing_app_info" | sed -n '3p')"
  printf '%s\n' "$app_lookup_json" > "$response_file"
fi

write_output_summary "$response_file"
echo
echo "HTTP_STATUS=$status_code"
echo "UPLOAD_RESPONSE_PATH=$OUTPUT_PATH"

if [[ "$status_code" -lt 200 || "$status_code" -ge 300 ]]; then
  exit 1
fi
log_success "Published Teams app package to Microsoft Graph app catalog"
