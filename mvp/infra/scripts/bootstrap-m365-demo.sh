#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER_SCRIPT="$ROOT_DIR/infra/bootstrap_helpers.py"
MODE="${1:-secure}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "Usage: bash mvp/infra/scripts/bootstrap-m365-demo.sh <open|secure>" >&2
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

GRAPH_UPLOAD_RESPONSE_PATH="$ROOT_DIR/appPackage/build/graph-upload-response-${MODE}.json"

source_env() {
  if [[ -f "$RUNTIME_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "$RUNTIME_FILE")
    set +a
  fi
}

upsert_env_value() {
  local key="$1"
  local value="$2"

  "$PYTHON_BIN" - <<'PY' "$RUNTIME_FILE" "$key" "$value"
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
updated = False
rendered = f"{key}={value}"
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = rendered
        updated = True
        break

if not updated:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(rendered)

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

log_step() {
  echo "[bootstrap-m365-demo] STEP: $*"
}

log_success() {
  echo "[bootstrap-m365-demo] OK: $*"
}

run_bootstrap_step() {
  local step_name="$1"
  shift

  log_step "$step_name"
  if "$@"; then
    log_success "$step_name"
    return 0
  fi

  echo "[bootstrap-m365-demo] ERROR: $step_name failed." >&2
  return 1
}

ensure_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
}

ensure_input_file() {
  if [[ -f "$INPUT_FILE" ]]; then
    return 0
  fi
  cp "$INPUT_EXAMPLE" "$INPUT_FILE"
  echo "Created $INPUT_FILE from $INPUT_EXAMPLE." >&2
  echo "Fill the required blank values, then rerun the bootstrap." >&2
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

graph_access_token() {
  if [[ -n "${GRAPH_ACCESS_TOKEN:-}" ]]; then
    printf '%s\n' "$GRAPH_ACCESS_TOKEN"
    return 0
  fi

  if [[ -n "${M365_GRAPH_PUBLISHER_CLIENT_ID:-}" ]]; then
    "$PYTHON_BIN" "$ROOT_DIR/scripts/get-graph-delegated-token.py" \
      --tenant-id "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}" \
      --client-id "$M365_GRAPH_PUBLISHER_CLIENT_ID" \
      --scopes "https://graph.microsoft.com/AppCatalog.ReadWrite.All https://graph.microsoft.com/TeamsAppInstallation.ReadWriteForUser https://graph.microsoft.com/AppCatalog.Read.All https://graph.microsoft.com/User.Read"
    return 0
  fi

  az account get-access-token --resource-type ms-graph --query accessToken -o tsv
}

graph_token_scopes() {
  "$PYTHON_BIN" - <<'PY' "$1"
import base64
import json
import sys

token = sys.argv[1]
parts = token.split(".")
payload = parts[1] + "=" * (-len(parts[1]) % 4)
data = json.loads(base64.urlsafe_b64decode(payload))
print(data.get("scp", ""))
PY
}

preflight_runtime() {
  local required_vars=(
    AZURE_TENANT_ID
    BOT_APP_ID
    BOT_SSO_APP_ID
    BOT_SSO_RESOURCE
    WRAPPER_BASE_URL
    M365_APP_PACKAGE_ID
  )
  local var_name
  for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
      echo "$var_name is required in $RUNTIME_FILE. Run the Azure bootstrap first." >&2
      exit 1
    fi
  done
}

preflight_m365() {
  ensure_command az
  ensure_command "$PYTHON_BIN"
  ensure_command curl

  if ! az account show >/dev/null 2>&1; then
    echo "Azure CLI is not signed in. Run 'az login' first." >&2
    exit 1
  fi

  preflight_runtime

  GRAPH_TOKEN="$(graph_access_token)"
  GRAPH_SCOPES="$(graph_token_scopes "$GRAPH_TOKEN")"

  if [[ "$GRAPH_SCOPES" != *"AppCatalog.Submit"* && "$GRAPH_SCOPES" != *"AppCatalog.ReadWrite.All"* && "$GRAPH_SCOPES" != *"Directory.ReadWrite.All"* ]]; then
    echo "The Microsoft Graph token is missing a Teams app catalog publish scope." >&2
    echo "Scopes on the current token: $GRAPH_SCOPES" >&2
    exit 1
  fi

  if [[ "$GRAPH_SCOPES" != *"TeamsAppInstallation.ReadWriteForUser"* && "$GRAPH_SCOPES" != *"TeamsAppInstallation.ReadWriteSelfForUser"* ]]; then
    echo "The Microsoft Graph token is missing a Teams self-install scope." >&2
    echo "Scopes on the current token: $GRAPH_SCOPES" >&2
    exit 1
  fi
}

persist_graph_ids() {
  if [[ ! -f "$GRAPH_UPLOAD_RESPONSE_PATH" ]]; then
    return 0
  fi

  local graph_ids
  graph_ids="$("$PYTHON_BIN" - <<'PY' "$GRAPH_UPLOAD_RESPONSE_PATH"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload.get("id", ""))
print(payload.get("externalId", ""))
PY
)"
  local teams_app_id
  local external_id
  teams_app_id="$(printf '%s\n' "$graph_ids" | sed -n '1p')"
  external_id="$(printf '%s\n' "$graph_ids" | sed -n '2p')"

  if [[ -n "$teams_app_id" ]]; then
    upsert_env_value "GRAPH_TEAMS_APP_ID" "$teams_app_id"
  fi
  if [[ -n "$external_id" ]]; then
    upsert_env_value "GRAPH_EXTERNAL_APP_ID" "$external_id"
  fi
}

validate_m365_outputs() {
  if [[ -z "${WRAPPER_BASE_URL:-}" ]]; then
    echo "WRAPPER_BASE_URL is missing from $RUNTIME_FILE." >&2
    exit 1
  fi

  if [[ -z "${GRAPH_TEAMS_APP_ID:-}" ]]; then
    echo "GRAPH_TEAMS_APP_ID was not written back to $RUNTIME_FILE after publish." >&2
    exit 1
  fi
}

run_bootstrap_step "Ensure operator input file exists" ensure_input_file
run_bootstrap_step "Render runtime env from operator input" render_runtime_env
run_bootstrap_step "Load runtime env" source_env
run_bootstrap_step "Run M365 preflight checks" preflight_m365

run_bootstrap_step "Build Teams/Copilot app package" \
  env ENV_FILE="$RUNTIME_FILE" \
  bash "$ROOT_DIR/scripts/build-m365-app-package.sh"

run_bootstrap_step "Publish Teams app package to catalog" \
  env GRAPH_ACCESS_TOKEN="$GRAPH_TOKEN" ENV_FILE="$RUNTIME_FILE" OUTPUT_PATH="$GRAPH_UPLOAD_RESPONSE_PATH" \
  bash "$ROOT_DIR/scripts/publish-m365-app-package-graph.sh"

run_bootstrap_step "Persist Graph app identifiers to runtime env" persist_graph_ids
run_bootstrap_step "Reload runtime env after publish" source_env

run_bootstrap_step "Install Teams app for signed-in operator" \
  env GRAPH_ACCESS_TOKEN="$GRAPH_TOKEN" ENV_FILE="$RUNTIME_FILE" \
  bash "$ROOT_DIR/scripts/install-m365-app-for-self-graph.sh"

run_bootstrap_step "Validate M365 bootstrap outputs" validate_m365_outputs

echo
echo "M365 bootstrap completed for mode=$MODE."
echo "Runtime env: $RUNTIME_FILE"
echo "Teams package id: ${M365_APP_PACKAGE_ID:-}"
echo "Published Teams catalog app id: ${GRAPH_TEAMS_APP_ID:-}"
echo "Wrapper endpoint: ${WRAPPER_BASE_URL:-}/api/messages"
echo "If sign-in still prompts unexpectedly, verify tenant admin consent for the generated Entra apps and confirm the bot endpoint resolves from Teams."
