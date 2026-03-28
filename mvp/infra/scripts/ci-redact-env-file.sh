#!/usr/bin/env bash
set -euo pipefail

INPUT_ENV_FILE="${INPUT_ENV_FILE:-${1:-}}"
OUTPUT_ENV_FILE="${OUTPUT_ENV_FILE:-${2:-}}"

if [[ -z "$INPUT_ENV_FILE" ]]; then
  echo "INPUT_ENV_FILE is required." >&2
  exit 1
fi

if [[ -z "$OUTPUT_ENV_FILE" ]]; then
  echo "OUTPUT_ENV_FILE is required." >&2
  exit 1
fi

if [[ ! -f "$INPUT_ENV_FILE" ]]; then
  echo "INPUT_ENV_FILE does not exist: $INPUT_ENV_FILE" >&2
  exit 1
fi

python - <<'PY' "$INPUT_ENV_FILE" "$OUTPUT_ENV_FILE"
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
secret_markers = ("SECRET", "PASSWORD", "TOKEN", "KEY")

redacted_lines = []
for line in source_path.read_text(encoding="utf-8").splitlines():
    if "=" not in line:
        redacted_lines.append(line)
        continue
    key, value = line.split("=", 1)
    if any(marker in key for marker in secret_markers):
        redacted_lines.append(f"{key}=<redacted>")
    else:
        redacted_lines.append(f"{key}={value}")

target_path.parent.mkdir(parents=True, exist_ok=True)
target_path.write_text("\n".join(redacted_lines) + "\n", encoding="utf-8")
PY
