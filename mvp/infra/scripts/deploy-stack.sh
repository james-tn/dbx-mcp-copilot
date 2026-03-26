#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_SCRIPTS_DIR="$ROOT_DIR/infra/scripts"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi

ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/deploy-foundation.sh" "$DEPLOYMENT_MODE"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/setup-custom-engine-app-registrations.sh"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/deploy-planner-api.sh"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/validate-databricks-direct-query.sh"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/deploy-m365-wrapper.sh"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/create-azure-bot-resource.sh"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" "$INFRA_SCRIPTS_DIR/setup-bot-oauth-connection.sh"
