#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PLANNER_PORT="${PLANNER_PORT:-8080}"
UI_PORT="${UI_PORT:-8787}"
HOST="${HOST:-127.0.0.1}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

export PLANNER_API_BASE_URL="http://${HOST}:${PLANNER_PORT}"
export PLANNER_SERVICE_BASE_URL="$PLANNER_API_BASE_URL"

planner_pid=""

cleanup() {
  if [[ -n "$planner_pid" ]] && kill -0 "$planner_pid" 2>/dev/null; then
    kill "$planner_pid" 2>/dev/null || true
    wait "$planner_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

bash "$ROOT_DIR/infra/scripts/run-local-planner-api.sh" &
planner_pid="$!"

for _ in $(seq 1 30); do
  if ! kill -0 "$planner_pid" 2>/dev/null; then
    echo "Local planner API process exited before becoming healthy." >&2
    exit 1
  fi
  if curl -fsS "$PLANNER_API_BASE_URL/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! kill -0 "$planner_pid" 2>/dev/null; then
  echo "Local planner API process exited before the chat UI started." >&2
  exit 1
fi

if ! curl -fsS "$PLANNER_API_BASE_URL/healthz" >/dev/null 2>&1; then
  echo "Local planner API did not become healthy at $PLANNER_API_BASE_URL/healthz." >&2
  exit 1
fi

PORT="$UI_PORT" bash "$ROOT_DIR/infra/scripts/run-local-planner-chat.sh"
