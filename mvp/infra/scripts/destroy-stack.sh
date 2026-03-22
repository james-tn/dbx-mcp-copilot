#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_SCRIPTS_DIR="$ROOT_DIR/infra/scripts"
APP_SCRIPTS_DIR="$ROOT_DIR/scripts"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

SKIP_M365_UNPUBLISH="${SKIP_M365_UNPUBLISH:-false}"
SKIP_APP_REGISTRATION_DELETE="${SKIP_APP_REGISTRATION_DELETE:-false}"

if [[ "${SKIP_M365_UNPUBLISH,,}" != "true" ]]; then
  ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$APP_SCRIPTS_DIR/unpublish-m365-app-package-graph.sh"
fi

if [[ "${SKIP_APP_REGISTRATION_DELETE,,}" != "true" ]]; then
  ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/delete-app-registrations.sh"
fi

ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/destroy-foundation.sh"
