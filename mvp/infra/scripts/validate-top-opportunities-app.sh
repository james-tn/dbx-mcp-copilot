#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "${TOP_OPPORTUNITIES_APP_BASE_URL:-}" ]]; then
  echo "TOP_OPPORTUNITIES_APP_BASE_URL is required in $ENV_FILE or the environment." >&2
  exit 1
fi

curl -fsS "${TOP_OPPORTUNITIES_APP_BASE_URL}/healthz" >/dev/null
echo "Top Opportunities app health check passed: ${TOP_OPPORTUNITIES_APP_BASE_URL}/healthz"
