#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-}"
VALIDATE_USER_UPN="${VALIDATE_USER_UPN:-}"
ENABLE_WRAPPER_HEALTHCHECK="${ENABLE_WRAPPER_HEALTHCHECK:-true}"
REQUIRE_AUTHENTICATED_E2E="${REQUIRE_AUTHENTICATED_E2E:-true}"

if [[ -z "$ENV_FILE" ]]; then
  echo "ENV_FILE is required." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ENV_FILE does not exist: $ENV_FILE" >&2
  exit 1
fi

if [[ -z "$VALIDATE_USER_UPN" ]]; then
  echo "VALIDATE_USER_UPN is required." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source <(sed 's/\r$//' "$ENV_FILE")
set +a

if [[ -z "${PLANNER_API_BEARER_TOKEN:-}" && "${REQUIRE_AUTHENTICATED_E2E,,}" == "true" ]]; then
  echo "PLANNER_API_BEARER_TOKEN is required when REQUIRE_AUTHENTICATED_E2E=true." >&2
  exit 1
fi

if [[ "${ENABLE_WRAPPER_HEALTHCHECK,,}" == "true" ]]; then
  if [[ -z "${WRAPPER_BASE_URL:-}" ]]; then
    echo "WRAPPER_BASE_URL is required when ENABLE_WRAPPER_HEALTHCHECK=true." >&2
    exit 1
  fi
  echo "Checking wrapper health endpoint..."
  curl -fsS "${WRAPPER_BASE_URL%/}/healthz"
  echo
fi

echo "Checking planner service health and authenticated chat..."
ENV_FILE="$ENV_FILE" bash "$ROOT_DIR/infra/scripts/validate-planner-service-e2e.sh"

echo
echo "Checking customer vPower query path for $VALIDATE_USER_UPN..."
ENV_FILE="$ENV_FILE" VALIDATE_USER_UPN="$VALIDATE_USER_UPN" \
  bash "$ROOT_DIR/infra/scripts/validate-customer-vpower-query.sh"
