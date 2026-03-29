#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER_SCRIPT="$ROOT_DIR/infra/bootstrap_helpers.py"
MODE="${1:-secure}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "Usage: bash mvp/infra/scripts/complete-m365-catalog-publish.sh <open|secure>" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if [[ "$MODE" == "secure" ]]; then
  INPUT_FILE="${INPUT_FILE:-$ROOT_DIR/.env.secure.inputs}"
  INPUT_EXAMPLE="${INPUT_EXAMPLE:-$ROOT_DIR/.env.secure.inputs.example}"
  RUNTIME_FILE="${RUNTIME_FILE:-$ROOT_DIR/.env.secure}"
  RUNTIME_EXAMPLE="${RUNTIME_EXAMPLE:-$ROOT_DIR/.env.secure.example}"
else
  INPUT_FILE="${INPUT_FILE:-$ROOT_DIR/.env.inputs}"
  INPUT_EXAMPLE="${INPUT_EXAMPLE:-$ROOT_DIR/.env.inputs.example}"
  RUNTIME_FILE="${RUNTIME_FILE:-$ROOT_DIR/.env}"
  RUNTIME_EXAMPLE="${RUNTIME_EXAMPLE:-$ROOT_DIR/.env.example}"
fi

GRAPH_UPLOAD_RESPONSE_PATH="$ROOT_DIR/appPackage/build/graph-upload-response-${MODE}.json"

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

bootstrap_status_init "$STATUS_FILE" "$MODE" "m365_catalog_admin" "$INPUT_FILE" "$RUNTIME_FILE" "true"
ensure_input_file
render_runtime_env
source_env
bootstrap_status_note_step "$STATUS_FILE" "load runtime env"

if ! az account show >/dev/null 2>&1; then
  bootstrap_status_fail "$STATUS_FILE" "Azure CLI is not signed in."
  echo "Azure CLI is not signed in. Run 'az login' first." >&2
  exit 1
fi

ENV_FILE="$RUNTIME_FILE" \
  bash "$ROOT_DIR/scripts/build-m365-app-package.sh"
bootstrap_status_note_step "$STATUS_FILE" "build Teams app package"

ENV_FILE="$RUNTIME_FILE" OUTPUT_PATH="$GRAPH_UPLOAD_RESPONSE_PATH" \
  bash "$ROOT_DIR/scripts/publish-m365-app-package-graph.sh"
bootstrap_status_note_step "$STATUS_FILE" "publish Teams app package"

persist_graph_ids
source_env
bootstrap_status_note_step "$STATUS_FILE" "persist Graph app identifiers"

if [[ -z "${GRAPH_TEAMS_APP_ID:-}" ]]; then
  bootstrap_status_fail "$STATUS_FILE" "GRAPH_TEAMS_APP_ID was not written after catalog publish."
  echo "GRAPH_TEAMS_APP_ID was not written after catalog publish." >&2
  exit 1
fi

bootstrap_status_pause \
  "$STATUS_FILE" \
  "deployment_operator" \
  "bash mvp/infra/scripts/bootstrap-m365-demo.sh $MODE" \
  "Teams catalog publish is complete. Have the deployment operator rerun the M365 bootstrap to self-install the app."

echo "Teams catalog publish completed."
echo "Next step:"
echo "  bash mvp/infra/scripts/bootstrap-m365-demo.sh $MODE"
