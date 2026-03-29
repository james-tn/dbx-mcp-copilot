#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

export PYTHONPATH="$ROOT_DIR/agents:$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT_DIR"
uv run --project "$ROOT_DIR" python -m uvicorn agents.api:app --host "$HOST" --port "$PORT"
