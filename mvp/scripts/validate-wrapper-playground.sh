#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

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

echo "Checking wrapper health endpoint..."
curl -fsS "$WRAPPER_BASE_URL/healthz"
echo

cat <<EOF
Wrapper is reachable.

Next local channel-validation steps:
1. Open Microsoft 365 Agents Playground.
2. Point the custom engine messaging endpoint to:
   ${WRAPPER_BASE_URL}/api/messages
3. Sign in with a user who has planner API consent and Databricks access.
4. Verify these prompts keep the same conversation session:
   - Give me my morning briefing
   - Where should I focus?
   - Draft me an email for adidas AG
EOF
