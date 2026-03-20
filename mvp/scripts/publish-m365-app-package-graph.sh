#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PACKAGE_PATH="${PACKAGE_PATH:-$ROOT_DIR/appPackage/build/daily-account-planner-m365.zip}"
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

if [[ ! -f "$PACKAGE_PATH" ]]; then
  echo "App package not found at $PACKAGE_PATH. Run build-m365-app-package.sh first." >&2
  exit 1
fi

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

response_file="$(mktemp)"
upload_url="https://graph.microsoft.com/v1.0/appCatalogs/teamsApps"
if [[ "$REQUIRES_REVIEW" == "true" ]]; then
  upload_url="${upload_url}?requiresReview=true"
fi

status_code="$(curl -sS -o "$response_file" -w "%{http_code}" \
  -X POST "$upload_url" \
  -H "Authorization: Bearer $GRAPH_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary "@$PACKAGE_PATH")"

mkdir -p "$(dirname "$OUTPUT_PATH")"
cp "$response_file" "$OUTPUT_PATH"
python - <<'PY' "$response_file"
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
echo
echo "HTTP_STATUS=$status_code"
echo "UPLOAD_RESPONSE_PATH=$OUTPUT_PATH"

if [[ "$status_code" -lt 200 || "$status_code" -ge 300 ]]; then
  exit 1
fi
