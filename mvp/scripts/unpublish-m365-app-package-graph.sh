#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi
GRAPH_TOKEN_HELPER="${GRAPH_TOKEN_HELPER:-$ROOT_DIR/scripts/get-graph-delegated-token.py}"
GRAPH_SCOPES="${GRAPH_SCOPES:-https://graph.microsoft.com/AppCatalog.ReadWrite.All https://graph.microsoft.com/TeamsAppInstallation.ReadWriteForUser https://graph.microsoft.com/AppCatalog.Read.All https://graph.microsoft.com/User.Read}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

M365_APP_PACKAGE_ID="${M365_APP_PACKAGE_ID:-}"
if [[ -z "$M365_APP_PACKAGE_ID" ]]; then
  echo "M365_APP_PACKAGE_ID is required in $ENV_FILE or the environment." >&2
  exit 1
fi

if [[ -n "${GRAPH_ACCESS_TOKEN:-}" ]]; then
  GRAPH_TOKEN="$GRAPH_ACCESS_TOKEN"
elif [[ -n "${M365_GRAPH_PUBLISHER_CLIENT_ID:-}" ]]; then
  GRAPH_TOKEN="$(python "$GRAPH_TOKEN_HELPER" --tenant-id "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}" --client-id "$M365_GRAPH_PUBLISHER_CLIENT_ID" --scopes "$GRAPH_SCOPES")"
else
  GRAPH_TOKEN="$(az account get-access-token --resource-type ms-graph --query accessToken -o tsv)"
fi

catalog_response_file="$(mktemp)"
catalog_status="$(curl -sS -o "$catalog_response_file" -w "%{http_code}" \
  --get "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps" \
  -H "Authorization: Bearer $GRAPH_TOKEN" \
  --data-urlencode "\$filter=externalId eq '$M365_APP_PACKAGE_ID'")"

if [[ "$catalog_status" -lt 200 || "$catalog_status" -ge 300 ]]; then
  cat "$catalog_response_file"
  echo
  echo "HTTP_STATUS=$catalog_status"
  exit 1
fi

teams_app_id="$(python - <<'PY' "$catalog_response_file"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = payload.get("value", [])
if values:
    print(values[0].get("id", ""))
PY
)"

if [[ -z "$teams_app_id" ]]; then
  echo "No Teams app catalog entry found for externalId=$M365_APP_PACKAGE_ID."
  exit 0
fi

installed_file="$(mktemp)"
installed_status="$(curl -sS -o "$installed_file" -w "%{http_code}" \
  "https://graph.microsoft.com/v1.0/me/teamwork/installedApps?\$expand=teamsAppDefinition" \
  -H "Authorization: Bearer $GRAPH_TOKEN")"

if [[ "$installed_status" -lt 200 || "$installed_status" -ge 300 ]]; then
  cat "$installed_file"
  echo
  echo "HTTP_STATUS=$installed_status"
  exit 1
fi

mapfile -t installed_app_ids < <(python - <<'PY' "$installed_file" "$teams_app_id" "$M365_APP_PACKAGE_ID"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
teams_app_id = sys.argv[2]
external_id = sys.argv[3]

for item in payload.get("value", []):
    definition = item.get("teamsAppDefinition") or {}
    if definition.get("teamsAppId") == teams_app_id or definition.get("externalId") == external_id:
        print(item.get("id", ""))
PY
)

for installed_app_id in "${installed_app_ids[@]}"; do
  [[ -n "$installed_app_id" ]] || continue
  delete_status="$(curl -sS -o /dev/null -w "%{http_code}" \
    -X DELETE "https://graph.microsoft.com/v1.0/me/teamwork/installedApps/$installed_app_id" \
    -H "Authorization: Bearer $GRAPH_TOKEN")"
  if [[ "$delete_status" != "204" && "$delete_status" != "404" ]]; then
    echo "Failed to uninstall installed Teams app $installed_app_id (HTTP $delete_status)." >&2
    exit 1
  fi
done

delete_file="$(mktemp)"
delete_status="$(curl -sS -o "$delete_file" -w "%{http_code}" \
  -X DELETE "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/$teams_app_id" \
  -H "Authorization: Bearer $GRAPH_TOKEN")"

if [[ "$delete_status" != "204" && "$delete_status" != "404" ]]; then
  cat "$delete_file"
  echo
  echo "HTTP_STATUS=$delete_status"
  exit 1
fi

echo "Deleted Teams app catalog entry $teams_app_id for externalId=$M365_APP_PACKAGE_ID."
