#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-}"
DEPLOY_TARGET="${DEPLOY_TARGET:-}"

if [[ -z "$ENV_FILE" ]]; then
  echo "ENV_FILE is required." >&2
  exit 1
fi

if [[ -z "$DEPLOY_TARGET" ]]; then
  echo "DEPLOY_TARGET is required." >&2
  exit 1
fi

ENV_FILE="$ENV_FILE" DEPLOY_TARGET="$DEPLOY_TARGET" \
  bash "$ROOT_DIR/infra/scripts/ci-deploy-planner.sh"

ENV_FILE="$ENV_FILE" DEPLOY_TARGET="$DEPLOY_TARGET" \
  bash "$ROOT_DIR/infra/scripts/ci-deploy-wrapper.sh"
