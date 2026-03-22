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

echo "Checking planner health endpoint..."
curl -fsS "$PLANNER_API_BASE_URL/healthz"
echo

if [[ -z "${PLANNER_API_BEARER_TOKEN:-}" ]]; then
  echo "PLANNER_API_BEARER_TOKEN is not set. Health check completed, authenticated chat validation skipped."
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
