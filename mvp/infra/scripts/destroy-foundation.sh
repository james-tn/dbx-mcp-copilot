#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-${1:-open}}"
if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi
WAIT_FOR_DELETE="${WAIT_FOR_DELETE:-true}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

delete_databricks_workspace_if_present() {
  local workspace_name="${DATABRICKS_WORKSPACE_NAME:-}"
  [[ -n "$workspace_name" ]] || return 0

  if ! az databricks workspace show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$workspace_name" \
    >/dev/null 2>&1; then
    echo "Databricks workspace already absent: $workspace_name"
    return 0
  fi

  az databricks workspace delete \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$workspace_name" \
    --yes \
    --force-deletion true \
    --no-wait \
    >/dev/null
  echo "Started deletion for Databricks workspace: $workspace_name"
}

wait_for_databricks_workspace_delete() {
  local workspace_name="${DATABRICKS_WORKSPACE_NAME:-}"
  [[ -n "$workspace_name" ]] || return 0

  for _ in {1..180}; do
    if ! az databricks workspace show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$workspace_name" \
      >/dev/null 2>&1; then
      echo "Databricks workspace deleted: $workspace_name"
      return 0
    fi
    sleep 10
  done

  echo "Timed out waiting for Databricks workspace deletion: $workspace_name" >&2
  return 1
}

delete_group_if_present() {
  local group_name="$1"
  [[ -n "$group_name" ]] || return 0

  if ! az group exists --name "$group_name" -o tsv | grep -qi '^true$'; then
    echo "Resource group already absent: $group_name"
    return 0
  fi

  az group delete --name "$group_name" --yes --no-wait >/dev/null
  echo "Started deletion for resource group: $group_name"
}

wait_for_group_delete() {
  local group_name="$1"
  [[ -n "$group_name" ]] || return 0

  for _ in {1..180}; do
    if ! az group exists --name "$group_name" -o tsv | grep -qi '^true$'; then
      echo "Resource group deleted: $group_name"
      return 0
    fi
    sleep 10
  done

  echo "Timed out waiting for resource group deletion: $group_name" >&2
  return 1
}

delete_databricks_workspace_if_present
delete_group_if_present "$AZURE_RESOURCE_GROUP"

if [[ "${WAIT_FOR_DELETE,,}" == "true" ]]; then
  wait_for_databricks_workspace_delete
  wait_for_group_delete "$AZURE_RESOURCE_GROUP"
  wait_for_group_delete "${DATABRICKS_MANAGED_RESOURCE_GROUP:-}"
fi
