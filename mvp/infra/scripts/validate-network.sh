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

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  WRAPPER_ACA_APP_NAME
  PLANNER_ACA_APP_NAME
  DATABRICKS_WORKSPACE_NAME
  AZURE_OPENAI_ACCOUNT_NAME
  AZURE_AI_FOUNDRY_ACCOUNT_NAME
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

python - <<'PY' \
  "$(az containerapp show -g "$AZURE_RESOURCE_GROUP" -n "$WRAPPER_ACA_APP_NAME" -o json)" \
  "$(az containerapp show -g "$AZURE_RESOURCE_GROUP" -n "$PLANNER_ACA_APP_NAME" -o json)" \
  "$(az databricks workspace show -g "$AZURE_RESOURCE_GROUP" -n "$DATABRICKS_WORKSPACE_NAME" -o json)" \
  "$(az resource show -g "$AZURE_RESOURCE_GROUP" -n "$AZURE_OPENAI_ACCOUNT_NAME" --resource-type Microsoft.CognitiveServices/accounts -o json)" \
  "$(az resource show -g "$AZURE_RESOURCE_GROUP" -n "$AZURE_AI_FOUNDRY_ACCOUNT_NAME" --resource-type Microsoft.CognitiveServices/accounts -o json)"
import json
import sys

wrapper = json.loads(sys.argv[1])
planner = json.loads(sys.argv[2])
databricks = json.loads(sys.argv[3])
openai = json.loads(sys.argv[4])
foundry = json.loads(sys.argv[5])

summary = {
    "wrapper_external": wrapper.get("properties", {}).get("configuration", {}).get("ingress", {}).get("external"),
    "wrapper_fqdn": wrapper.get("properties", {}).get("configuration", {}).get("ingress", {}).get("fqdn"),
    "planner_external": planner.get("properties", {}).get("configuration", {}).get("ingress", {}).get("external"),
    "planner_fqdn": planner.get("properties", {}).get("configuration", {}).get("ingress", {}).get("fqdn"),
    "databricks_public_network_access": databricks.get("publicNetworkAccess"),
    "databricks_no_public_ip": databricks.get("parameters", {}).get("enableNoPublicIp", {}).get("value"),
    "openai_public_network_access": openai.get("properties", {}).get("publicNetworkAccess"),
    "foundry_public_network_access": foundry.get("properties", {}).get("publicNetworkAccess"),
}

print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
