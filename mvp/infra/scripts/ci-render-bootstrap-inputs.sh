#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${MODE:-secure}"
OUTPUT_INPUT_FILE="${OUTPUT_INPUT_FILE:-}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "MODE must be 'open' or 'secure'." >&2
  exit 1
fi

if [[ -z "$OUTPUT_INPUT_FILE" ]]; then
  echo "OUTPUT_INPUT_FILE is required." >&2
  exit 1
fi

TEMPLATE_PATH="$ROOT_DIR/.env.inputs.example"
if [[ "$MODE" == "secure" ]]; then
  TEMPLATE_PATH="$ROOT_DIR/.env.secure.inputs.example"
fi

python - <<'PY' "$ROOT_DIR" "$TEMPLATE_PATH" "$OUTPUT_INPUT_FILE"
import os
import sys
from collections import OrderedDict
from pathlib import Path

root_dir = Path(sys.argv[1])
template_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])

sys.path.insert(0, str(root_dir))
from infra.bootstrap_helpers import load_env_file, write_env_file  # noqa: E402

values = OrderedDict(load_env_file(template_path))
for key in list(values):
    if key in os.environ:
        values[key] = os.environ[key]

output_path.parent.mkdir(parents=True, exist_ok=True)
write_env_file(output_path, values)
PY
