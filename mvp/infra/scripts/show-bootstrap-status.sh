#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-secure}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "Usage: bash mvp/infra/scripts/show-bootstrap-status.sh <open|secure>" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/infra/scripts/bootstrap-status-lib.sh"

STATUS_FILE="$(bootstrap_status_file_for_mode "$ROOT_DIR" "$MODE")"
if [[ ! -f "$STATUS_FILE" ]]; then
  echo "No bootstrap status file found for mode=$MODE at $STATUS_FILE" >&2
  exit 1
fi

cat "$STATUS_FILE"
