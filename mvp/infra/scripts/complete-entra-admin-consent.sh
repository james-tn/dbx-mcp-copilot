#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER_SCRIPT="$ROOT_DIR/infra/bootstrap_helpers.py"
MODE="${1:-secure}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "Usage: bash mvp/infra/scripts/complete-entra-admin-consent.sh <open|secure>" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if [[ "$MODE" == "secure" ]]; then
  INPUT_FILE="$ROOT_DIR/.env.secure.inputs"
  INPUT_EXAMPLE="$ROOT_DIR/.env.secure.inputs.example"
  RUNTIME_FILE="$ROOT_DIR/.env.secure"
  RUNTIME_EXAMPLE="$ROOT_DIR/.env.secure.example"
else
  INPUT_FILE="$ROOT_DIR/.env.inputs"
  INPUT_EXAMPLE="$ROOT_DIR/.env.inputs.example"
  RUNTIME_FILE="$ROOT_DIR/.env"
  RUNTIME_EXAMPLE="$ROOT_DIR/.env.example"
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/infra/scripts/bootstrap-status-lib.sh"
STATUS_FILE="$(bootstrap_status_file_for_mode "$ROOT_DIR" "$MODE")"

source_env() {
  if [[ -f "$RUNTIME_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "$RUNTIME_FILE")
    set +a
  fi
}

ensure_input_file() {
  if [[ -f "$INPUT_FILE" ]]; then
    return 0
  fi
  cp "$INPUT_EXAMPLE" "$INPUT_FILE"
  echo "Created $INPUT_FILE from $INPUT_EXAMPLE." >&2
  echo "Fill the required blank values, then rerun the script." >&2
  exit 1
}

render_runtime_env() {
  if ! "$PYTHON_BIN" "$HELPER_SCRIPT" render-runtime-env \
    --mode "$MODE" \
    --input-file "$INPUT_FILE" \
    --runtime-example "$RUNTIME_EXAMPLE" \
    --runtime-file "$RUNTIME_FILE"; then
    echo "The input env is missing required values. Fill the listed variables in $INPUT_FILE and rerun." >&2
    exit 1
  fi
}

bootstrap_status_init "$STATUS_FILE" "$MODE" "entra_admin" "$INPUT_FILE" "$RUNTIME_FILE" "true"
ensure_input_file
render_runtime_env
source_env
bootstrap_status_note_step "$STATUS_FILE" "load runtime env"

if ! az account show >/dev/null 2>&1; then
  bootstrap_status_fail "$STATUS_FILE" "Azure CLI is not signed in."
  echo "Azure CLI is not signed in. Run 'az login' first." >&2
  exit 1
fi

ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" FAIL_ON_MISSING_ADMIN_CONSENT=true PRESERVE_EXISTING_CREDENTIALS=true \
  bash "$ROOT_DIR/infra/scripts/setup-custom-engine-app-registrations.sh"
source_env
bootstrap_status_note_step "$STATUS_FILE" "grant Entra admin consent"

if [[ "${PLANNER_API_ADMIN_CONSENT_STATUS:-}" != "granted" || "${WRAPPER_API_ADMIN_CONSENT_STATUS:-}" != "granted" ]]; then
  bootstrap_status_fail "$STATUS_FILE" "Entra admin consent is still not granted for all required applications."
  echo "Entra admin consent is still not granted for all required applications." >&2
  exit 1
fi

if [[ -n "${PLANNER_API_BASE_URL:-}" && -n "${WRAPPER_BASE_URL:-}" ]]; then
  bootstrap_status_pause \
    "$STATUS_FILE" \
    "deployment_operator" \
    "bash mvp/infra/scripts/bootstrap-m365-demo.sh $MODE" \
    "Entra admin consent is complete. The Azure bootstrap was already provisioned; continue with the M365 bootstrap."
  echo "Entra admin consent completed." 
  echo "Next step:"
  echo "  bash mvp/infra/scripts/bootstrap-m365-demo.sh $MODE"
else
  bootstrap_status_pause \
    "$STATUS_FILE" \
    "deployment_operator" \
    "bash mvp/infra/scripts/bootstrap-azure-demo.sh $MODE" \
    "Entra app registration and consent are ready. Resume the Azure bootstrap to finish infrastructure deployment."
  echo "Entra admin consent completed."
  echo "Next step:"
  echo "  bash mvp/infra/scripts/bootstrap-azure-demo.sh $MODE"
fi
