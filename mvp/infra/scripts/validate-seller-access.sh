#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PROMPT="${PROMPT:-tell me my accounts}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "${WRAPPER_BASE_URL:-}" ]]; then
  echo "WRAPPER_BASE_URL is required." >&2
  exit 1
fi

if [[ -z "${SELLER_A_TOKEN:-}" || -z "${SELLER_B_TOKEN:-}" ]]; then
  echo "SELLER_A_TOKEN and SELLER_B_TOKEN are required." >&2
  exit 1
fi

SELLER_A_UPN="${SELLER_A_UPN:-ri-test-na@m365cpi89838450.onmicrosoft.com}"
SELLER_B_UPN="${SELLER_B_UPN:-DaichiM@M365CPI89838450.OnMicrosoft.com}"

call_debug_chat() {
  local bearer_token="$1"
  local session_id="$2"
  curl -fsS \
    -X POST "$WRAPPER_BASE_URL/api/debug/chat" \
    -H "Authorization: Bearer $bearer_token" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"${PROMPT//\"/\\\"}\",\"session_id\":\"$session_id\"}"
}

seller_a_response="$(call_debug_chat "$SELLER_A_TOKEN" "debug-proof-a")"
seller_b_response="$(call_debug_chat "$SELLER_B_TOKEN" "debug-proof-b")"

python - <<'PY' "$SELLER_A_UPN" "$seller_a_response" "$SELLER_B_UPN" "$seller_b_response"
import json
import sys

seller_a_upn = sys.argv[1]
seller_a = json.loads(sys.argv[2])
seller_b_upn = sys.argv[3]
seller_b = json.loads(sys.argv[4])

print(json.dumps(
    {
        "seller_a": {
            "upn": seller_a_upn,
            "reply": seller_a.get("reply"),
            "session_id": seller_a.get("session_id"),
        },
        "seller_b": {
            "upn": seller_b_upn,
            "reply": seller_b.get("reply"),
            "session_id": seller_b.get("session_id"),
        },
    },
    ensure_ascii=False,
    indent=2,
))
PY
