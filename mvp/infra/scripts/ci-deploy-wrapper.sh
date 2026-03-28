#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-}"
DEPLOY_TARGET="${DEPLOY_TARGET:-}"

if [[ -z "$ENV_FILE" ]]; then
  echo "ENV_FILE is required." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ENV_FILE does not exist: $ENV_FILE" >&2
  exit 1
fi

if [[ -n "$DEPLOY_TARGET" && "$DEPLOY_TARGET" != "integration" && "$DEPLOY_TARGET" != "production" ]]; then
  echo "DEPLOY_TARGET must be 'integration' or 'production' when provided." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE")
set +a

ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}" \
  bash "$ROOT_DIR/infra/scripts/deploy-m365-wrapper.sh"
