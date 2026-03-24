#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
GRAPH_TOKEN_HELPER="${GRAPH_TOKEN_HELPER:-$ROOT_DIR/scripts/get-graph-delegated-token.py}"
GRAPH_SCOPES="${GRAPH_SCOPES:-https://graph.microsoft.com/TeamsAppInstallation.ReadWriteForUser https://graph.microsoft.com/User.Read https://graph.microsoft.com/AppCatalog.Read.All}"
MANIFEST_PATH="${MANIFEST_PATH:-$ROOT_DIR/appPackage/build/manifest.json}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

log_step() {
  echo "[install-m365-app-for-self-graph] STEP: $*" >&2
}

log_success() {
  echo "[install-m365-app-for-self-graph] OK: $*" >&2
}

if [[ -z "${M365_APP_PACKAGE_ID:-}" ]]; then
  if [[ -f "$MANIFEST_PATH" ]]; then
    M365_APP_PACKAGE_ID="$(python - <<'PY' "$MANIFEST_PATH"
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["id"])
PY
)"
  else
    echo "M365_APP_PACKAGE_ID is required when $MANIFEST_PATH does not exist." >&2
    exit 1
  fi
fi

if [[ -n "${GRAPH_ACCESS_TOKEN:-}" ]]; then
  GRAPH_TOKEN="$GRAPH_ACCESS_TOKEN"
elif [[ -n "${M365_GRAPH_PUBLISHER_CLIENT_ID:-}" ]]; then
  GRAPH_TOKEN="$(python "$GRAPH_TOKEN_HELPER" --tenant-id "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}" --client-id "$M365_GRAPH_PUBLISHER_CLIENT_ID" --scopes "$GRAPH_SCOPES")"
else
  GRAPH_TOKEN="$(az account get-access-token --resource-type ms-graph --query accessToken -o tsv)"
fi
log_success "Resolved Graph token for self-install flow"

response_file="$(mktemp)"
log_step "Looking up Teams catalog app by externalId=$M365_APP_PACKAGE_ID"
status_code="$(curl -sS -o "$response_file" -w "%{http_code}" \
  --get "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps" \
  -H "Authorization: Bearer $GRAPH_TOKEN" \
  --data-urlencode "\$filter=externalId eq '$M365_APP_PACKAGE_ID'")"

if [[ "$status_code" -lt 200 || "$status_code" -ge 300 ]]; then
  cat "$response_file"
  echo
  echo "HTTP_STATUS=$status_code"
  exit 1
fi
log_success "Resolved Teams catalog app metadata"

teams_app_id="$(python - <<'PY' "$response_file"
import json
import sys
from pathlib import Path

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = body.get("value", [])
if values:
    print(values[0].get("id", ""))
PY
)"

if [[ -z "$teams_app_id" ]]; then
  echo "No uploaded Teams app was found for externalId=$M365_APP_PACKAGE_ID." >&2
  exit 2
fi

me_file="$(mktemp)"
log_step "Resolving signed-in Microsoft Graph user"
me_status="$(curl -sS -o "$me_file" -w "%{http_code}" \
  "https://graph.microsoft.com/v1.0/me" \
  -H "Authorization: Bearer $GRAPH_TOKEN")"

if [[ "$me_status" -lt 200 || "$me_status" -ge 300 ]]; then
  cat "$me_file"
  echo
  echo "HTTP_STATUS=$me_status"
  exit 1
fi
log_success "Resolved signed-in Microsoft Graph user"

user_id="$(python - <<'PY' "$me_file"
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["id"])
PY
)"

install_body_file="$(mktemp)"
cat >"$install_body_file" <<JSON
{
  "teamsApp@odata.bind": "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/$teams_app_id"
}
JSON

install_response="$(mktemp)"
log_step "Installing Teams app '$teams_app_id' for signed-in user '$user_id'"
install_status="$(curl -sS -o "$install_response" -w "%{http_code}" \
  -X POST "https://graph.microsoft.com/v1.0/users/$user_id/teamwork/installedApps" \
  -H "Authorization: Bearer $GRAPH_TOKEN" \
  -H "Content-Type: application/json" \
  --data "@$install_body_file")"

cat "$install_response"
echo
echo "HTTP_STATUS=$install_status"

if [[ "$install_status" == "409" ]]; then
  echo "The app is already installed for the signed-in user."
  exit 0
fi

if [[ "$install_status" -lt 200 || "$install_status" -ge 300 ]]; then
  exit 1
fi
log_success "Installed Teams app for signed-in user"
