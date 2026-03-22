#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi
DELETE_PLANNER_API_APP="${DELETE_PLANNER_API_APP:-true}"
DELETE_BOT_APP="${DELETE_BOT_APP:-true}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

az account show >/dev/null

delete_app_registration() {
  local app_id="$1"
  local label="$2"

  if [[ -z "$app_id" ]]; then
    echo "Skipping $label deletion because no app ID was provided."
    return 0
  fi

  if az ad sp show --id "$app_id" >/dev/null 2>&1; then
    az ad sp delete --id "$app_id" >/dev/null || true
  fi

  if az ad app show --id "$app_id" >/dev/null 2>&1; then
    az ad app delete --id "$app_id" >/dev/null
    echo "Deleted $label app registration: $app_id"
  else
    echo "$label app registration already absent: $app_id"
  fi
}

if [[ "${DELETE_BOT_APP,,}" == "true" ]]; then
  delete_app_registration "${BOT_APP_ID:-}" "bot"
fi

if [[ "${DELETE_PLANNER_API_APP,,}" == "true" ]]; then
  delete_app_registration "${PLANNER_API_CLIENT_ID:-}" "planner API"
fi
