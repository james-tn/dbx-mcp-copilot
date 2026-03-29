#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER_SCRIPT="$ROOT_DIR/infra/bootstrap_helpers.py"
MODE="${1:-secure}"
REQUIRE_ADMIN_CONSENT="${REQUIRE_ADMIN_CONSENT:-true}"
SPLIT_RESPONSIBILITY_MODE="${SPLIT_RESPONSIBILITY_MODE:-false}"
ENABLE_MOCK_DATABRICKS_ENVIRONMENT="${ENABLE_MOCK_DATABRICKS_ENVIRONMENT:-false}"

if [[ "$MODE" != "open" && "$MODE" != "secure" ]]; then
  echo "Usage: bash mvp/infra/scripts/bootstrap-azure-demo.sh <open|secure>" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if [[ "$MODE" == "secure" ]]; then
  INPUT_FILE="$ROOT_DIR/.env.secure.inputs"
  INPUT_EXAMPLE="$ROOT_DIR/.env.secure.inputs.example"
  RUNTIME_FILE="$ROOT_DIR/.env.secure"
  RUNTIME_EXAMPLE="$ROOT_DIR/.env.secure.example"
else
  INPUT_FILE="$ROOT_DIR/.env.inputs"
  INPUT_EXAMPLE="$ROOT_DIR/.env.inputs.example"
  RUNTIME_FILE="$ROOT_DIR/.env"
  RUNTIME_EXAMPLE="$ROOT_DIR/.env.example"
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/infra/scripts/bootstrap-status-lib.sh"
STATUS_FILE="$(bootstrap_status_file_for_mode "$ROOT_DIR" "$MODE")"
ENTRA_ADMIN_CONSENT_PENDING="false"
MOCK_DATABRICKS_PUBLIC_ACCESS_TEMP_OPENED="false"

source_env() {
  if [[ -f "$RUNTIME_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "$RUNTIME_FILE")
    set +a
  fi
}

upsert_env_value() {
  local key="$1"
  local value="$2"

  "$PYTHON_BIN" - <<'PY' "$RUNTIME_FILE" "$key" "$value"
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

log_step() {
  echo "[bootstrap-azure-demo] STEP: $*"
}

log_success() {
  echo "[bootstrap-azure-demo] OK: $*"
}

set_databricks_public_network_access() {
  local desired_state="$1"
  local normalized_desired="$desired_state"
  if [[ "$normalized_desired" != "Enabled" && "$normalized_desired" != "Disabled" ]]; then
    echo "Databricks public network access state must be Enabled or Disabled." >&2
    return 1
  fi

  az databricks workspace update \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    --public-network-access "$normalized_desired" \
    >/dev/null

  for _ in {1..60}; do
    current_state="$(az databricks workspace show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$DATABRICKS_WORKSPACE_NAME" \
      --query publicNetworkAccess \
      -o tsv 2>/dev/null || true)"
    if [[ "$current_state" == "$normalized_desired" ]]; then
      return 0
    fi
    sleep 5
  done

  echo "Timed out waiting for Databricks public network access to become $normalized_desired." >&2
  return 1
}

enable_mock_databricks_public_access() {
  set_databricks_public_network_access "Enabled"
  MOCK_DATABRICKS_PUBLIC_ACCESS_TEMP_OPENED="true"
}

disable_mock_databricks_public_access() {
  set_databricks_public_network_access "Disabled"
  MOCK_DATABRICKS_PUBLIC_ACCESS_TEMP_OPENED="false"
}

cleanup_mock_databricks_public_access() {
  if [[ "$MOCK_DATABRICKS_PUBLIC_ACCESS_TEMP_OPENED" != "true" ]]; then
    return 0
  fi

  echo "[bootstrap-azure-demo] WARN: closing temporary Databricks public access during cleanup."
  set +e
  set_databricks_public_network_access "Disabled"
  set -e
  MOCK_DATABRICKS_PUBLIC_ACCESS_TEMP_OPENED="false"
}

trap cleanup_mock_databricks_public_access EXIT

handoff_and_exit() {
  local role_name="$1"
  local next_step_script="$2"
  local message="$3"

  bootstrap_status_pause "$STATUS_FILE" "$role_name" "$next_step_script" "$message"
  echo
  echo "Bootstrap paused at a privilege boundary."
  echo "Reason: $message"
  echo "Next step:"
  echo "  $next_step_script"
  exit 2
}

run_bootstrap_step() {
  local step_name="$1"
  shift

  log_step "$step_name"
  if "$@"; then
    log_success "$step_name"
    bootstrap_status_note_step "$STATUS_FILE" "$step_name"
    return 0
  fi

  bootstrap_status_fail "$STATUS_FILE" "$step_name failed."
  echo "[bootstrap-azure-demo] ERROR: $step_name failed." >&2
  return 1
}

ensure_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
}

resource_exists() {
  local resource_group="$1"
  local resource_name="$2"
  local resource_type="$3"

  az resource show \
    --resource-group "$resource_group" \
    --name "$resource_name" \
    --resource-type "$resource_type" \
    >/dev/null 2>&1
}

ensure_input_file() {
  if [[ -f "$INPUT_FILE" ]]; then
    return 0
  fi
  cp "$INPUT_EXAMPLE" "$INPUT_FILE"
  echo "Created $INPUT_FILE from $INPUT_EXAMPLE." >&2
  echo "Fill the required blank values, then rerun the bootstrap." >&2
  exit 1
}

upsert_if_value() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    upsert_env_value "$key" "$value"
  fi
}

render_runtime_env() {
  if ! "$PYTHON_BIN" "$HELPER_SCRIPT" render-runtime-env \
    --mode "$MODE" \
    --input-file "$INPUT_FILE" \
    --runtime-example "$RUNTIME_EXAMPLE" \
    --runtime-file "$RUNTIME_FILE"; then
    echo "The input env is missing required values. Fill the listed variables in $INPUT_FILE and rerun." >&2
    exit 1
  fi
}

foundation_exists() {
  if ! az group exists --name "$AZURE_RESOURCE_GROUP" -o tsv | grep -qi '^true$'; then
    return 1
  fi

  if ! az acr show --name "$ACR_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    return 1
  fi

  if ! resource_exists "$AZURE_RESOURCE_GROUP" "$ACA_ENVIRONMENT_NAME" "Microsoft.App/managedEnvironments"; then
    return 1
  fi

  if ! az databricks workspace show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    >/dev/null 2>&1; then
    return 1
  fi

  return 0
}

refresh_runtime_from_existing_foundation() {
  local workspace_url=""
  local managed_rg_id=""
  local openai_endpoint=""

  upsert_env_value "SECURE_DEPLOYMENT" "$([[ "$MODE" == "secure" ]] && printf 'true' || printf 'false')"
  upsert_env_value "ACA_ENVIRONMENT_NAME" "${ACA_ENVIRONMENT_NAME:-}"
  upsert_env_value "AZURE_OPENAI_ACCOUNT_NAME" "${AZURE_OPENAI_ACCOUNT_NAME:-}"
  upsert_env_value "AZURE_AI_FOUNDRY_ACCOUNT_NAME" "${AZURE_AI_FOUNDRY_ACCOUNT_NAME:-}"
  upsert_env_value "AZURE_AI_FOUNDRY_PROJECT_NAME" "${AZURE_AI_FOUNDRY_PROJECT_NAME:-}"
  upsert_env_value "DATABRICKS_WORKSPACE_NAME" "${DATABRICKS_WORKSPACE_NAME:-}"
  upsert_env_value "DATABRICKS_RESOURCE_GROUP" "$AZURE_RESOURCE_GROUP"
  upsert_env_value "SECURE_VNET_NAME" "${SECURE_VNET_NAME:-}"
  upsert_env_value "KEYVAULT_NAME" "${KEYVAULT_NAME:-}"
  upsert_env_value "ACR_NAME" "${ACR_NAME:-}"
  upsert_env_value "LOG_ANALYTICS_NAME" "${LOG_ANALYTICS_NAME:-}"

  if [[ "$MODE" == "secure" ]]; then
    upsert_if_value "SECURE_ACA_SUBNET_ID" "$(az network vnet subnet show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --vnet-name "$SECURE_VNET_NAME" \
      --name "aca-infra" \
      --query id \
      -o tsv 2>/dev/null || true)"
    upsert_if_value "SECURE_PRIVATE_ENDPOINT_SUBNET_ID" "$(az network vnet subnet show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --vnet-name "$SECURE_VNET_NAME" \
      --name "private-endpoints" \
      --query id \
      -o tsv 2>/dev/null || true)"
    upsert_if_value "DATABRICKS_VNET_PUBLIC_SUBNET_ID" "$(az network vnet subnet show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --vnet-name "$SECURE_VNET_NAME" \
      --name "databricks-public" \
      --query id \
      -o tsv 2>/dev/null || true)"
    upsert_if_value "DATABRICKS_VNET_PRIVATE_SUBNET_ID" "$(az network vnet subnet show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --vnet-name "$SECURE_VNET_NAME" \
      --name "databricks-private" \
      --query id \
      -o tsv 2>/dev/null || true)"
  fi

  workspace_url="$(az databricks workspace show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    --query workspaceUrl \
    -o tsv 2>/dev/null || true)"
  if [[ -n "$workspace_url" ]]; then
    upsert_env_value "DATABRICKS_HOST" "https://${workspace_url}"
  fi

  managed_rg_id="$(az databricks workspace show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    --query managedResourceGroupId \
    -o tsv 2>/dev/null || true)"
  if [[ -n "$managed_rg_id" ]]; then
    upsert_env_value "DATABRICKS_MANAGED_RESOURCE_GROUP" "${managed_rg_id##*/}"
  fi

  openai_endpoint="$(az cognitiveservices account show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_OPENAI_ACCOUNT_NAME" \
    --query properties.endpoint \
    -o tsv 2>/dev/null || true)"
  if [[ -n "$openai_endpoint" ]]; then
    upsert_env_value "AZURE_OPENAI_ENDPOINT" "$openai_endpoint"
  fi
}

configure_customer_databricks_runtime() {
  if [[ "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT,,}" == "true" || "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT}" == "1" ]]; then
    local aiq_dev_catalog="${AIQ_DEV_CATALOG:-}"
    local workspace_id=""
    if [[ -z "$aiq_dev_catalog" && "$MODE" == "secure" ]]; then
      workspace_id="$(az databricks workspace show \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --name "$DATABRICKS_WORKSPACE_NAME" \
        --query workspaceId \
        -o tsv 2>/dev/null || true)"
      if [[ -n "$workspace_id" ]]; then
        aiq_dev_catalog="$(
          python3 - <<'PY' "$DATABRICKS_WORKSPACE_NAME" "$workspace_id"
import re
import sys

workspace_name = sys.argv[1].strip()
workspace_id = sys.argv[2].strip()
normalized_name = re.sub(r"[^0-9A-Za-z_]", "_", workspace_name)
print(f"{normalized_name}_{workspace_id}" if normalized_name and workspace_id else "")
PY
        )"
      fi
    fi
    aiq_dev_catalog="${aiq_dev_catalog:-dev_catalog}"
    upsert_env_value "MOCK_DATABRICKS_ENVIRONMENT" "true"
    upsert_env_value "AIQ_DEV_CATALOG" "$aiq_dev_catalog"
    upsert_env_value "AIQ_DEV_SKIP_CATALOG_CREATE" "$([[ "$MODE" == "secure" ]] && printf 'true' || printf 'false')"
    upsert_env_value "DATABRICKS_HOST" "${DATABRICKS_HOST:-}"
    upsert_env_value "DATABRICKS_AZURE_RESOURCE_ID" "${DATABRICKS_AZURE_RESOURCE_ID:-}"
    upsert_env_value "DATABRICKS_OBO_SCOPE" "${DATABRICKS_OBO_SCOPE:-2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default}"
    upsert_env_value "DATABRICKS_WAREHOUSE_ID" "${DATABRICKS_WAREHOUSE_ID:-}"
    upsert_env_value "TOP_OPPORTUNITIES_SOURCE" "${aiq_dev_catalog}.data_science_account_iq_gold.account_iq_scores"
    upsert_env_value "CONTACTS_SOURCE" "${aiq_dev_catalog}.account_iq_gold.aiq_contact"
    upsert_env_value "SCOPE_ACCOUNTS_CATALOG" "${aiq_dev_catalog}"
    upsert_env_value "SALES_TEAM_MAPPING_CATALOG" "${aiq_dev_catalog}"
  else
    upsert_env_value "MOCK_DATABRICKS_ENVIRONMENT" "false"
  fi
}

preflight_extensions() {
  local extension_name
  for extension_name in containerapp databricks; do
    if ! az extension show --name "$extension_name" >/dev/null 2>&1; then
      echo "Azure CLI extension '$extension_name' is required before bootstrap." >&2
      exit 1
    fi
  done

  if ! az bot -h >/dev/null 2>&1; then
    echo "Azure CLI bot commands are required before bootstrap." >&2
    exit 1
  fi
}

preflight_entra_permissions() {
  local permission_json
  if ! permission_json="$("$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import json
import subprocess
import sys


def run_az(*args: str) -> str:
    return subprocess.check_output(["az", *args], text=True, stderr=subprocess.DEVNULL).strip()


def role_names() -> list[str]:
    try:
        payload = run_az(
            "rest",
            "--method",
            "GET",
            "--url",
            "https://graph.microsoft.com/v1.0/me/memberOf?$select=displayName",
        )
    except subprocess.CalledProcessError:
        return []
    body = json.loads(payload)
    return [
        str(item.get("displayName") or "").strip()
        for item in body.get("value", [])
        if str(item.get("displayName") or "").strip()
    ]


def app_creation_policy() -> tuple[bool, bool]:
    try:
        payload = run_az(
            "rest",
            "--method",
            "GET",
            "--url",
            "https://graph.microsoft.com/v1.0/policies/authorizationPolicy/authorizationPolicy?$select=defaultUserRolePermissions",
        )
    except subprocess.CalledProcessError:
        return False, False
    body = json.loads(payload)
    permissions = body.get("defaultUserRolePermissions") or {}
    return True, bool(permissions.get("allowedToCreateApps"))


roles = role_names()
role_set = {role.lower() for role in roles}
app_admin_roles = {
    "application administrator",
    "cloud application administrator",
    "global administrator",
}
consent_roles = app_admin_roles | {"privileged role administrator"}
policy_readable, policy_allows_app_creation = app_creation_policy()

payload = {
    "roles": roles,
    "can_create_apps": policy_allows_app_creation or bool(role_set & app_admin_roles),
    "policy_readable": policy_readable,
    "can_grant_admin_consent": bool(role_set & consent_roles),
}
print(json.dumps(payload))
PY
)"; then
    echo "Unable to evaluate Entra app registration prerequisites. Confirm your operator account can create app registrations." >&2
    exit 1
  fi

  CAN_CREATE_APPS="$("$PYTHON_BIN" - <<'PY' "$permission_json"
import json
import sys
print("true" if json.loads(sys.argv[1]).get("can_create_apps") else "false")
PY
)"
  CAN_GRANT_ADMIN_CONSENT="$("$PYTHON_BIN" - <<'PY' "$permission_json"
import json
import sys
print("true" if json.loads(sys.argv[1]).get("can_grant_admin_consent") else "false")
PY
)"
  POLICY_READABLE="$("$PYTHON_BIN" - <<'PY' "$permission_json"
import json
import sys
print("true" if json.loads(sys.argv[1]).get("policy_readable") else "false")
PY
)"
  OPERATOR_DIRECTORY_ROLES="$("$PYTHON_BIN" - <<'PY' "$permission_json"
import json
import sys
print(", ".join(json.loads(sys.argv[1]).get("roles") or []))
PY
)"

  if [[ "$CAN_CREATE_APPS" != "true" ]]; then
    if [[ "$POLICY_READABLE" == "true" ]]; then
      if [[ "$SPLIT_RESPONSIBILITY_MODE" == "true" ]]; then
        echo "Warning: the signed-in operator does not appear able to create app registrations in this tenant." >&2
        echo "A separate Entra admin may need to run the Entra completion step or rerun the Azure bootstrap." >&2
      else
        echo "The signed-in operator does not appear able to create app registrations in this tenant." >&2
        echo "Grant Application Administrator, Cloud Application Administrator, or Global Administrator, or enable user app registration in Entra ID." >&2
        exit 1
      fi
    fi
    echo "Warning: the bootstrap could not conclusively verify app-registration create rights." >&2
    echo "If setup-custom-engine-app-registrations.sh fails, grant Application Administrator, Cloud Application Administrator, or Global Administrator and rerun." >&2
  fi

  if [[ "$REQUIRE_ADMIN_CONSENT" == "true" && "$CAN_GRANT_ADMIN_CONSENT" != "true" ]]; then
    if [[ "$SPLIT_RESPONSIBILITY_MODE" == "true" ]]; then
      echo "Warning: the signed-in operator does not appear able to grant tenant-wide admin consent for the generated Entra applications." >&2
      echo "The bootstrap will continue in split-responsibility mode and pause for an Entra admin later." >&2
      ENTRA_ADMIN_CONSENT_PENDING="true"
    else
      echo "The signed-in operator does not appear able to grant tenant-wide admin consent for the generated Entra applications." >&2
      echo "Grant Application Administrator, Cloud Application Administrator, Global Administrator, or Privileged Role Administrator before running the operator bootstrap." >&2
      if [[ -n "$OPERATOR_DIRECTORY_ROLES" ]]; then
        echo "Signed-in directory roles: $OPERATOR_DIRECTORY_ROLES" >&2
      fi
      exit 1
    fi
  fi
}

preflight_azure() {
  ensure_command az
  ensure_command "$PYTHON_BIN"
  ensure_command curl

  if ! az account show >/dev/null 2>&1; then
    echo "Azure CLI is not signed in. Run 'az login' first." >&2
    exit 1
  fi

  az account set --subscription "$AZURE_SUBSCRIPTION_ID" >/dev/null
  preflight_extensions
  preflight_entra_permissions
}

build_and_publish_images() {
  local planner_repository="daily-account-planner/planner"
  local wrapper_repository="daily-account-planner/wrapper"
  local git_short_sha
  local build_suffix
  git_short_sha="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
  git_short_sha="${git_short_sha:-manual}"
  build_suffix="$(date -u +%Y%m%d%H%M%S)-${git_short_sha}"
  local planner_tag="${MODE}-${build_suffix}"
  local wrapper_tag="${MODE}-${build_suffix}"
  local planner_image_ref="${ACR_NAME}.azurecr.io/${planner_repository}:${planner_tag}"
  local wrapper_image_ref="${ACR_NAME}.azurecr.io/${wrapper_repository}:${wrapper_tag}"

  if [[ -z "${ACR_NAME:-}" ]]; then
    echo "ACR_NAME was not populated by the foundation deploy." >&2
    exit 1
  fi

  if ! az acr show --name "$ACR_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    echo "Azure Container Registry '$ACR_NAME' was not found in $AZURE_RESOURCE_GROUP." >&2
    exit 1
  fi

  log_step "Building planner image ${planner_image_ref}"
  az acr build \
    --registry "$ACR_NAME" \
    --image "${planner_repository}:${planner_tag}" \
    --file "$ROOT_DIR/agents/Dockerfile" \
    "$ROOT_DIR" >/dev/null
  log_success "Built planner image ${planner_image_ref}"

  log_step "Building wrapper image ${wrapper_image_ref}"
  az acr build \
    --registry "$ACR_NAME" \
    --image "${wrapper_repository}:${wrapper_tag}" \
    --file "$ROOT_DIR/m365_wrapper/Dockerfile" \
    "$ROOT_DIR" >/dev/null
  log_success "Built wrapper image ${wrapper_image_ref}"

  upsert_env_value "PLANNER_API_IMAGE" "$planner_image_ref"
  upsert_env_value "WRAPPER_IMAGE" "$wrapper_image_ref"
  source_env
}

validate_azure_outputs() {
  local resource_type

  for resource_type in \
    "Microsoft.App/containerApps:$PLANNER_ACA_APP_NAME" \
    "Microsoft.App/containerApps:$WRAPPER_ACA_APP_NAME" \
    "Microsoft.BotService/botServices:$BOT_RESOURCE_NAME"; do
    local type_name="${resource_type%%:*}"
    local resource_name="${resource_type##*:}"
    if ! az resource show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$resource_name" \
      --resource-type "$type_name" >/dev/null 2>&1; then
      echo "Expected resource '$resource_name' of type '$type_name' was not found." >&2
      exit 1
    fi
  done

  if [[ -z "${PLANNER_API_BASE_URL:-}" || -z "${WRAPPER_BASE_URL:-}" ]]; then
    echo "Planner or wrapper base URLs were not written back to $RUNTIME_FILE." >&2
    exit 1
  fi

}

run_entra_app_registration_step() {
  local fail_on_missing_admin_consent="$REQUIRE_ADMIN_CONSENT"
  if [[ "$SPLIT_RESPONSIBILITY_MODE" == "true" ]]; then
    fail_on_missing_admin_consent="false"
  fi

  ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" FAIL_ON_MISSING_ADMIN_CONSENT="$fail_on_missing_admin_consent" \
    bash "$ROOT_DIR/infra/scripts/setup-custom-engine-app-registrations.sh"
}

entra_admin_consent_is_pending() {
  [[ "${PLANNER_API_ADMIN_CONSENT_STATUS:-}" != "granted" || "${WRAPPER_API_ADMIN_CONSENT_STATUS:-}" != "granted" ]]
}

bootstrap_status_init "$STATUS_FILE" "$MODE" "azure" "$INPUT_FILE" "$RUNTIME_FILE" "$SPLIT_RESPONSIBILITY_MODE"
run_bootstrap_step "Ensure operator input file exists" ensure_input_file
run_bootstrap_step "Render runtime env from operator input" render_runtime_env
run_bootstrap_step "Load runtime env" source_env
run_bootstrap_step "Run Azure preflight checks" preflight_azure

upsert_env_value "CREATE_SECURE_ACR" "true"
source_env

if foundation_exists; then
  echo "Existing foundation detected for $MODE mode. Reusing it instead of redeploying the foundation."
  run_bootstrap_step "Refresh runtime env from existing foundation" refresh_runtime_from_existing_foundation
else
  run_bootstrap_step "Deploy Azure foundation for $MODE mode" \
    env ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" CREATE_SECURE_ACR=true \
    bash "$ROOT_DIR/infra/scripts/deploy-foundation.sh" "$MODE"
fi
run_bootstrap_step "Reload runtime env after foundation" source_env

run_bootstrap_step "Build and publish planner and wrapper images" build_and_publish_images

if ! run_bootstrap_step "Create or update Entra app registrations" run_entra_app_registration_step; then
  if [[ "$SPLIT_RESPONSIBILITY_MODE" == "true" ]]; then
    source_env
    if [[ -n "${PLANNER_API_CLIENT_ID:-}" && -n "${BOT_APP_ID:-}" ]]; then
      handoff_and_exit \
        "entra_admin" \
        "bash mvp/infra/scripts/complete-entra-admin-consent.sh $MODE" \
        "The deployment operator could not finish Entra application setup. An Entra admin should complete the app registration and consent step."
    fi
    handoff_and_exit \
      "entra_admin" \
      "SPLIT_RESPONSIBILITY_MODE=true bash mvp/infra/scripts/bootstrap-azure-demo.sh $MODE" \
      "The deployment operator could not create the required Entra applications. An Entra admin should rerun the Azure bootstrap or complete the app-registration phase."
  fi
  exit 1
fi
run_bootstrap_step "Reload runtime env after Entra app registration setup" source_env
run_bootstrap_step "Configure Databricks runtime selection" configure_customer_databricks_runtime
run_bootstrap_step "Reload runtime env after Databricks runtime selection" source_env

if entra_admin_consent_is_pending; then
  ENTRA_ADMIN_CONSENT_PENDING="true"
fi

run_bootstrap_step "Deploy planner API" \
  env ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" \
  bash "$ROOT_DIR/infra/scripts/deploy-planner-api.sh"
run_bootstrap_step "Reload runtime env after planner deployment" source_env

if [[ "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT,,}" == "true" || "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT}" == "1" ]]; then
  if [[ "$MODE" == "secure" ]]; then
    run_bootstrap_step "Enable temporary Databricks public access for mock seeding" enable_mock_databricks_public_access
  fi
  run_bootstrap_step "Run AIQ mock Databricks seed" \
    env ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" \
    bash "$ROOT_DIR/infra/scripts/seed-databricks-aiq-dev.sh"
  run_bootstrap_step "Reload runtime env after AIQ mock Databricks seed" source_env
  run_bootstrap_step "Bootstrap Databricks access on mock foundation workspace" \
    env ENV_FILE="$RUNTIME_FILE" \
    bash "$ROOT_DIR/infra/scripts/bootstrap-databricks-access.sh" foundation
  run_bootstrap_step "Validate AIQ mock Databricks tables" \
    env ENV_FILE="$RUNTIME_FILE" \
    bash "$ROOT_DIR/infra/scripts/validate-databricks-aiq-dev.sh"
  if [[ "$MODE" == "secure" ]]; then
    run_bootstrap_step "Disable Databricks public access after mock seeding" disable_mock_databricks_public_access
    run_bootstrap_step "Validate secure network posture after mock seeding" \
      env ENV_FILE="$RUNTIME_FILE" \
      bash "$ROOT_DIR/infra/scripts/validate-network.sh"
  fi
else
  echo "Skipping Databricks provisioning, seeding, and permission changes. Existing-workspace mode is read-only from the planner bootstrap."
fi

if [[ "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT,,}" == "true" || "${ENABLE_MOCK_DATABRICKS_ENVIRONMENT}" == "1" ]]; then
  echo "AIQ mock Databricks validation completed through the AIQ-shaped table validator."
else
  echo "Skipping local direct Databricks validation because the active runbook path does not rely on a locally reachable seeded workspace."
fi

run_bootstrap_step "Deploy M365 wrapper" \
  env ENV_FILE="$RUNTIME_FILE" DEPLOYMENT_MODE="$MODE" \
  bash "$ROOT_DIR/infra/scripts/deploy-m365-wrapper.sh"
run_bootstrap_step "Reload runtime env after wrapper deployment" source_env

run_bootstrap_step "Create or update Azure Bot resource" \
  env ENV_FILE="$RUNTIME_FILE" \
  bash "$ROOT_DIR/infra/scripts/create-azure-bot-resource.sh"
run_bootstrap_step "Reload runtime env after bot resource setup" source_env

run_bootstrap_step "Create or update Azure Bot OAuth connection" \
  env ENV_FILE="$RUNTIME_FILE" FAIL_ON_MISSING_ADMIN_CONSENT="$REQUIRE_ADMIN_CONSENT" \
  bash "$ROOT_DIR/infra/scripts/setup-bot-oauth-connection.sh"
run_bootstrap_step "Reload runtime env after bot OAuth setup" source_env

run_bootstrap_step "Validate Azure bootstrap outputs" validate_azure_outputs

if [[ "$SPLIT_RESPONSIBILITY_MODE" == "true" && "$ENTRA_ADMIN_CONSENT_PENDING" == "true" ]]; then
  handoff_and_exit \
    "entra_admin" \
    "bash mvp/infra/scripts/complete-entra-admin-consent.sh $MODE" \
    "Azure resources are deployed, but Entra admin consent is still pending for the generated applications."
fi

bootstrap_status_complete "$STATUS_FILE" "Azure bootstrap completed successfully."

echo
echo "Azure bootstrap completed for mode=$MODE."
echo "Input env: $INPUT_FILE"
echo "Runtime env: $RUNTIME_FILE"
echo "Planner URL: ${PLANNER_API_BASE_URL:-}"
echo "Wrapper URL: ${WRAPPER_BASE_URL:-}"
echo "Bot resource: ${BOT_RESOURCE_NAME:-}"
if [[ "$CAN_GRANT_ADMIN_CONSENT" != "true" ]]; then
  echo "Admin consent is still required for the generated Entra applications."
  if [[ -n "${OPERATOR_DIRECTORY_ROLES:-}" ]]; then
    echo "Signed-in directory roles: $OPERATOR_DIRECTORY_ROLES"
  fi
fi
