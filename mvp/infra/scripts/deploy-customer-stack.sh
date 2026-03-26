#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

env_file_declares_key() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] && rg -q "^${key}=" "$ENV_FILE"
}

# Keep customer-target deployments deterministic from the runtime env file.
# Without this, an operator shell can leak an unrelated OpenAI API key into the
# planner deploy, which makes the runtime prefer key auth over managed identity.
if ! env_file_declares_key "AZURE_OPENAI_API_KEY"; then
  unset AZURE_OPENAI_API_KEY || true
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
  AZURE_LOCATION
  ACA_ENVIRONMENT_NAME
  PLANNER_API_IMAGE
  WRAPPER_IMAGE
  AZURE_TENANT_ID
  PLANNER_API_CLIENT_ID
  PLANNER_API_CLIENT_SECRET
  PLANNER_API_EXPECTED_AUDIENCE
  BOT_APP_ID
  BOT_APP_PASSWORD
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

echo "[deploy-customer-stack] Deploying planner for secure customer Databricks profile..."
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}" \
  bash "$ROOT_DIR/infra/scripts/deploy-planner-api.sh"

echo "[deploy-customer-stack] Deploying wrapper..."
ENV_FILE="$ENV_FILE" DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${SECURE_DEPLOYMENT:-false}}" \
  bash "$ROOT_DIR/infra/scripts/deploy-m365-wrapper.sh"

echo
echo "Customer-target planner + wrapper deployment completed."
echo "Runtime env: $ENV_FILE"
