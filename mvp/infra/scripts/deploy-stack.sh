#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_SCRIPTS_DIR="$ROOT_DIR/infra/scripts"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"

"$INFRA_SCRIPTS_DIR/deploy-foundation.sh" "$DEPLOYMENT_MODE"
"$INFRA_SCRIPTS_DIR/setup-custom-engine-app-registrations.sh"
"$INFRA_SCRIPTS_DIR/deploy-planner-api.sh"
"$INFRA_SCRIPTS_DIR/seed-databricks-ri.sh"
"$INFRA_SCRIPTS_DIR/validate-databricks-direct-query.sh"
"$INFRA_SCRIPTS_DIR/deploy-m365-wrapper.sh"
"$INFRA_SCRIPTS_DIR/create-azure-bot-resource.sh"
"$INFRA_SCRIPTS_DIR/setup-bot-oauth-connection.sh"
