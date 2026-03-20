#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${ACCOUNT_PULSE_BENCHMARK_OUTPUT_DIR:-$ROOT_DIR/benchmark-output}"

cd "$ROOT_DIR"

python -m agents.account_pulse_benchmark \
  --output-dir "$OUTPUT_DIR" \
  "$@"
