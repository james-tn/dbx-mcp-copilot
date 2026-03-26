#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ -z "$DEPLOYMENT_MODE" ]]; then
  DEPLOYMENT_MODE="${SECURE_DEPLOYMENT:-false}"
fi

upsert_env_value() {
  local key="$1"
  local value="$2"

  python - <<'PY' "$ENV_FILE" "$key" "$value"
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

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  ACA_ENVIRONMENT_NAME
  ACR_NAME
  AZURE_TENANT_ID
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_DEPLOYMENT
  PLANNER_API_CLIENT_ID
  PLANNER_API_CLIENT_SECRET
  PLANNER_API_EXPECTED_AUDIENCE
  CUSTOMER_DATABRICKS_HOST
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID" >/dev/null

git_short_sha="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
git_short_sha="${git_short_sha:-manual}"
mode_prefix="open"
if [[ "${DEPLOYMENT_MODE,,}" == "secure" || "${DEPLOYMENT_MODE,,}" == "true" ]]; then
  mode_prefix="secure"
fi
build_suffix="$(date -u +%Y%m%d%H%M%S)-${git_short_sha}"
planner_repository="daily-account-planner/planner"
planner_image_ref="${ACR_NAME}.azurecr.io/${planner_repository}:${mode_prefix}-${build_suffix}"

echo "[build-and-deploy-planner-only] Building planner image ${planner_image_ref}"
az acr build \
  --registry "$ACR_NAME" \
  --image "${planner_repository}:${mode_prefix}-${build_suffix}" \
  --file "$ROOT_DIR/agents/Dockerfile" \
  "$ROOT_DIR" >/dev/null

upsert_env_value "PLANNER_API_IMAGE" "$planner_image_ref"
export PLANNER_API_IMAGE="$planner_image_ref"

echo "[build-and-deploy-planner-only] Deploying planner image ${planner_image_ref}"
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" \
  bash "$ROOT_DIR/infra/scripts/deploy-planner-api.sh"

echo
echo "Planner build + deploy completed."
echo "Runtime env: $ENV_FILE"
echo "Planner image: $planner_image_ref"
