#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
FIRST_PROMPT="${FIRST_PROMPT:-Where should I focus?}"
FOLLOW_UP_PROMPT="${FOLLOW_UP_PROMPT:-Give me two more details on the top account.}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "${PLANNER_API_BASE_URL:-}" ]]; then
  echo "PLANNER_API_BASE_URL is required." >&2
  exit 1
fi

planner_host="$(python - <<'PY' "$PLANNER_API_BASE_URL"
from urllib.parse import urlparse
import sys

print((urlparse(sys.argv[1]).hostname or "").strip())
PY
)"

planner_url_is_internal="false"
if [[ "$planner_host" == *".internal."* ]]; then
  planner_url_is_internal="true"
fi

check_internal_planner_via_azure() {
  if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || -z "${AZURE_RESOURCE_GROUP:-}" || -z "${PLANNER_ACA_APP_NAME:-}" ]]; then
    echo "AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and PLANNER_ACA_APP_NAME are required for internal planner validation." >&2
    exit 1
  fi

  local resource_payload=""
  resource_payload="$(az rest --method get --url \
    "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.App/containerApps/${PLANNER_ACA_APP_NAME}?api-version=2024-03-01")"

  python - <<'PY' "$resource_payload"
import json
import sys

payload = json.loads(sys.argv[1])
properties = payload.get("properties", {})
configuration = properties.get("configuration", {})
ingress = configuration.get("ingress", {})
summary = {
    "name": payload.get("name"),
    "running_status": properties.get("runningStatus"),
    "provisioning_state": properties.get("provisioningState"),
    "latest_ready_revision": properties.get("latestReadyRevisionName"),
    "fqdn": ingress.get("fqdn"),
    "external": ingress.get("external"),
}
print(json.dumps(summary, indent=2))

if summary["provisioning_state"] != "Succeeded":
    raise SystemExit("Planner container app provisioning state is not Succeeded.")
if summary["running_status"] != "Running":
    raise SystemExit("Planner container app running status is not Running.")
if not summary["latest_ready_revision"]:
    raise SystemExit("Planner container app does not have a latest ready revision.")
PY
}

if [[ "$planner_url_is_internal" == "true" ]]; then
  echo "Planner URL is internal; validating Container App state via Azure control plane..."
  check_internal_planner_via_azure
else
echo "Checking planner health endpoint..."
curl -fsS "$PLANNER_API_BASE_URL/healthz"
echo
fi

if [[ -z "${PLANNER_API_BEARER_TOKEN:-}" ]]; then
  echo "PLANNER_API_BEARER_TOKEN is not set. Health check completed, authenticated chat validation skipped."
  exit 0
fi

if [[ "$planner_url_is_internal" == "true" ]]; then
  echo "PLANNER_API_BASE_URL points to an internal ingress host, so direct authenticated chat validation is skipped on this runner."
  exit 0
fi

session_response="$(curl -fsS \
  -X POST "$PLANNER_API_BASE_URL/api/chat/sessions" \
  -H "Authorization: Bearer $PLANNER_API_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}')"

session_id="$(python - <<'PY' "$session_response"
import json, sys
print(json.loads(sys.argv[1])["session_id"])
PY
)"

echo "Created session: $session_id"

send_message() {
  local prompt="$1"
  curl -fsS \
    -X POST "$PLANNER_API_BASE_URL/api/chat/sessions/$session_id/messages" \
    -H "Authorization: Bearer $PLANNER_API_BEARER_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"${prompt//\"/\\\"}\"}"
}

echo "Running first turn..."
first_response="$(send_message "$FIRST_PROMPT")"
python - <<'PY' "$first_response"
import json, sys
payload = json.loads(sys.argv[1])
print(payload["reply"])
PY

echo
echo "Running follow-up turn..."
follow_up_response="$(send_message "$FOLLOW_UP_PROMPT")"
python - <<'PY' "$follow_up_response"
import json, sys
payload = json.loads(sys.argv[1])
print(payload["reply"])
print(f"Turn count: {len(payload['turns'])}")
PY
