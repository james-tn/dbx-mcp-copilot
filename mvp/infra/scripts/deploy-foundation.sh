#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_DIR="$ROOT_DIR/infra"
OUTPUT_DIR="$INFRA_DIR/outputs"
mkdir -p "$OUTPUT_DIR"

image_registry_name() {
  local image_ref="$1"
  local server="${image_ref%%/*}"
  if [[ -z "$image_ref" || "$server" == "$image_ref" || "$server" != *.azurecr.io ]]; then
    return 0
  fi
  printf '%s\n' "${server%%.azurecr.io}"
}

DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-open}"
if [[ "${1:-}" == "open" || "${1:-}" == "secure" ]]; then
  DEPLOYMENT_MODE="$1"
fi

if [[ "$DEPLOYMENT_MODE" == "secure" && -f "$ROOT_DIR/.env.secure" ]]; then
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.secure}"
else
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
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

delete_private_endpoint_if_unhealthy() {
  local pe_name="$1"

  if ! az network private-endpoint show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    >/dev/null 2>&1; then
    return 0
  fi

  local provisioning_state=""
  provisioning_state="$(az network private-endpoint show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    --query provisioningState \
    -o tsv 2>/dev/null || true)"

  if [[ "$provisioning_state" == "Succeeded" ]]; then
    return 0
  fi

  echo "Recreating unhealthy private endpoint '$pe_name' (state: ${provisioning_state:-unknown})."
  az network private-endpoint delete \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    >/dev/null

  for _ in {1..60}; do
    if ! az network private-endpoint show \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$pe_name" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done

  echo "Timed out waiting for private endpoint '$pe_name' to delete." >&2
  return 1
}

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_LOCATION
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "$var_name is required in $ENV_FILE or the environment." >&2
    exit 1
  fi
done

SECURE_MODE="false"
if [[ "$DEPLOYMENT_MODE" == "secure" ]]; then
  SECURE_MODE="true"
fi

NAME_PREFIX="${INFRA_NAME_PREFIX:-dailyacctplanner$([[ "$SECURE_MODE" == "true" ]] && printf 'sec' || printf 'open')}"
OPENAI_ACCOUNT_NAME="${AZURE_OPENAI_ACCOUNT_NAME:-${NAME_PREFIX}openai}"
FOUNDRY_ACCOUNT_NAME="${AZURE_AI_FOUNDRY_ACCOUNT_NAME:-${NAME_PREFIX}foundry}"
FOUNDRY_PROJECT_NAME="${AZURE_AI_FOUNDRY_PROJECT_NAME:-daily-account-planner}"
DATABRICKS_WORKSPACE_NAME="${DATABRICKS_WORKSPACE_NAME:-${NAME_PREFIX}-dbx}"
DATABRICKS_MANAGED_RESOURCE_GROUP="${DATABRICKS_MANAGED_RESOURCE_GROUP:-${AZURE_RESOURCE_GROUP}-dbx-managed}"
DATABRICKS_REQUIRED_NSG_RULES="${DATABRICKS_REQUIRED_NSG_RULES:-NoAzureDatabricksRules}"
ACA_ENVIRONMENT_NAME="${ACA_ENVIRONMENT_NAME:-${NAME_PREFIX}-aca-env}"
KEYVAULT_NAME="${KEYVAULT_NAME:-$(printf '%s' "${NAME_PREFIX}kv" | tr -d '-')}"
ACR_NAME="${ACR_NAME:-$(printf '%s' "${NAME_PREFIX}acr" | tr -d '-')}"
LOG_ANALYTICS_NAME="${LOG_ANALYTICS_NAME:-${NAME_PREFIX}-logs}"
VNET_NAME="${SECURE_VNET_NAME:-${NAME_PREFIX}-vnet}"
FOUNDATION_OUTPUT_FILE="$OUTPUT_DIR/foundation-${DEPLOYMENT_MODE}.json"
AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-${AZURE_OPENAI_MODEL:-gpt-5.3-chat}}"
AZURE_OPENAI_MODEL_NAME="${AZURE_OPENAI_MODEL_NAME:-$AZURE_OPENAI_DEPLOYMENT}"
AZURE_OPENAI_MODEL_VERSION="${AZURE_OPENAI_MODEL_VERSION:-2026-03-03}"
AZURE_OPENAI_DEPLOYMENT_SKU_NAME="${AZURE_OPENAI_DEPLOYMENT_SKU_NAME:-GlobalStandard}"
AZURE_OPENAI_DEPLOYMENT_CAPACITY="${AZURE_OPENAI_DEPLOYMENT_CAPACITY:-1000}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null

deployment_json="$(az deployment group create \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file "$INFRA_DIR/bicep/foundation.bicep" \
  --parameters \
    location="$AZURE_LOCATION" \
    secureDeployment="$SECURE_MODE" \
    createAcr="${CREATE_SECURE_ACR:-$([[ "$SECURE_MODE" == "true" ]] && printf 'false' || printf 'true')}" \
    namePrefix="$NAME_PREFIX" \
    keyVaultName="$KEYVAULT_NAME" \
    acrName="$ACR_NAME" \
    logAnalyticsName="$LOG_ANALYTICS_NAME" \
    vnetName="$VNET_NAME" \
  -o json)"

printf '%s\n' "$deployment_json" >"$FOUNDATION_OUTPUT_FILE"

read_output() {
  python - <<'PY' "$FOUNDATION_OUTPUT_FILE" "$1"
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
key = sys.argv[2]
value = payload["properties"]["outputs"].get(key, {}).get("value", "")
print(value if value is not None else "")
PY
}

purge_deleted_cognitive_account_if_needed() {
  local account_name="$1"

  python - <<'PY' "$account_name" "$AZURE_RESOURCE_GROUP" "$AZURE_LOCATION"
import json
import subprocess
import sys

account_name = sys.argv[1].strip().lower()
resource_group = sys.argv[2].strip()
resource_group_normalized = resource_group.lower()
location = sys.argv[3].strip().lower()

payload = subprocess.check_output(
    ["az", "cognitiveservices", "account", "list-deleted", "-o", "json"],
    text=True,
)
deleted_accounts = json.loads(payload)
for item in deleted_accounts:
    name = str(item.get("name") or "").strip().lower()
    rg_raw = str(item.get("resourceGroup") or "").strip()
    rg = rg_raw.lower()
    loc = str(item.get("location") or "").strip().lower()
    if name != account_name:
        continue
    if rg and rg != resource_group_normalized:
        continue
    purge_location = loc or location
    purge_resource_group = rg_raw or resource_group
    subprocess.run(
        [
            "az",
            "cognitiveservices",
            "account",
            "purge",
            "--name",
            item["name"],
            "--resource-group",
            purge_resource_group,
            "--location",
            purge_location,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    break
PY

  python - <<'PY' "$account_name" "$AZURE_RESOURCE_GROUP"
import json
import subprocess
import sys
import time

account_name = sys.argv[1].strip().lower()
resource_group = sys.argv[2].strip().lower()

for _ in range(60):
    payload = subprocess.check_output(
        ["az", "cognitiveservices", "account", "list-deleted", "-o", "json"],
        text=True,
    )
    deleted_accounts = json.loads(payload)
    still_present = False
    for item in deleted_accounts:
        name = str(item.get("name") or "").strip().lower()
        rg = str(item.get("resourceGroup") or "").strip().lower()
        if name == account_name and (not rg or rg == resource_group):
            still_present = True
            break
    if not still_present:
        raise SystemExit(0)
    time.sleep(5)

raise SystemExit(f"Soft-deleted Cognitive Services account '{account_name}' is still present after purge wait.")
PY
}

ensure_databricks_private_dns_record() {
  local pe_name="$1"
  local dns_zone_name="$2"

  if [[ "$dns_zone_name" != "privatelink.azuredatabricks.net" ]]; then
    return 0
  fi

  local pe_json
  pe_json="$(az network private-endpoint show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    -o json)"

  python - <<'PY' "$pe_json" "$AZURE_RESOURCE_GROUP" "$dns_zone_name"
import json
import subprocess
import sys

payload = json.loads(sys.argv[1])
resource_group = sys.argv[2]
zone_name = sys.argv[3]

for config in payload.get("customDnsConfigs", []) or []:
    fqdn = str(config.get("fqdn") or "").strip().rstrip(".")
    ip_addresses = list(config.get("ipAddresses") or [])
    if not fqdn or not ip_addresses:
        continue
    suffix = ".azuredatabricks.net"
    if not fqdn.endswith(suffix):
        continue
    record_name = fqdn[: -len(suffix)].rstrip(".")
    if subprocess.run(
        [
            "az",
            "network",
            "private-dns",
            "record-set",
            "a",
            "show",
            "--resource-group",
            resource_group,
            "--zone-name",
            zone_name,
            "--name",
            record_name,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode != 0:
        subprocess.run(
            [
                "az",
                "network",
                "private-dns",
                "record-set",
                "a",
                "create",
                "--resource-group",
                resource_group,
                "--zone-name",
                zone_name,
                "--name",
                record_name,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    existing = subprocess.check_output(
        [
            "az",
            "network",
            "private-dns",
            "record-set",
            "a",
            "list",
            "--resource-group",
            resource_group,
            "--zone-name",
            zone_name,
            "-o",
            "json",
        ],
        text=True,
    )
    existing_payload = json.loads(existing)
    current_ips = set()
    for item in existing_payload:
        if str(item.get("name") or "") != record_name:
            continue
        for record in item.get("aRecords", []) or []:
            ip = str(record.get("ipv4Address") or "").strip()
            if ip:
                current_ips.add(ip)
    for ip in ip_addresses:
        if ip in current_ips:
            continue
        subprocess.run(
            [
                "az",
                "network",
                "private-dns",
                "record-set",
                "a",
                "add-record",
                "--resource-group",
                resource_group,
                "--zone-name",
                zone_name,
                "--record-set-name",
                record_name,
                "--ipv4-address",
                ip,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
PY
}

ensure_private_endpoint() {
  local pe_name="$1"
  local resource_id="$2"
  local group_id="$3"
  local dns_zone_name="$4"

  delete_private_endpoint_if_unhealthy "$pe_name"

  if ! az network private-endpoint show -g "$AZURE_RESOURCE_GROUP" -n "$pe_name" >/dev/null 2>&1; then
    az network private-endpoint create \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$pe_name" \
      --location "$AZURE_LOCATION" \
      --subnet "$SECURE_PRIVATE_ENDPOINT_SUBNET_ID" \
      --private-connection-resource-id "$resource_id" \
      --group-id "$group_id" \
      --connection-name "${pe_name}-connection" \
      >/dev/null
  fi

  local dns_zone_id
  dns_zone_id="$(az network private-dns zone show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$dns_zone_name" \
    --query id \
    -o tsv)"

  local existing_group_json=""
  if existing_group_json="$(az network private-endpoint dns-zone-group show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --endpoint-name "$pe_name" \
    --name default \
    -o json 2>/dev/null)"; then
    local desired_zone_present
    desired_zone_present="$(python - <<'PY' "$existing_group_json" "$dns_zone_id"
import json
import sys

payload = json.loads(sys.argv[1])
target = sys.argv[2]
configs = payload.get("privateDnsZoneConfigs", [])
print("true" if any(str(item.get("privateDnsZoneId", "")).strip() == target for item in configs) else "false")
PY
)"
    if [[ "$desired_zone_present" != "true" ]]; then
      az network private-endpoint dns-zone-group delete \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --endpoint-name "$pe_name" \
        --name default \
        >/dev/null
    else
      ensure_databricks_private_dns_record "$pe_name" "$dns_zone_name"
      return 0
    fi
  fi

  az network private-endpoint dns-zone-group create \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --endpoint-name "$pe_name" \
    --name default \
    --private-dns-zone "$dns_zone_id" \
    --zone-name "$dns_zone_name" \
    >/dev/null

  ensure_databricks_private_dns_record "$pe_name" "$dns_zone_name"
}

ensure_databricks_private_endpoints() {
  local workspace_name="$1"
  local databricks_resource_id="$2"

  local resources_json
  resources_json="$(az databricks workspace private-link-resource list \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --workspace-name "$workspace_name" \
    -o json)"

python - <<'PY' "$resources_json"
import json
import sys

resources = json.loads(sys.argv[1])
for item in resources:
    properties = item.get("properties") or {}
    group_id = str(
        item.get("groupId")
        or properties.get("groupId")
        or item.get("name")
        or ""
    ).strip()
    if group_id:
        print(group_id)
PY
}

databricks_workspace_state() {
  az databricks workspace show \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$DATABRICKS_WORKSPACE_NAME" \
    --query provisioningState \
    -o tsv 2>/dev/null || true
}

wait_for_databricks_workspace() {
  local state=""
  for _ in {1..60}; do
    state="$(databricks_workspace_state)"
    if [[ "$state" == "Succeeded" ]]; then
      return 0
    fi
    sleep 10
  done
  echo "Databricks workspace $DATABRICKS_WORKSPACE_NAME did not reach Succeeded state in time." >&2
  return 1
}

SECURE_ACA_SUBNET_ID="$(read_output acaSubnetId)"
DATABRICKS_VNET_PUBLIC_SUBNET_ID="$(read_output databricksPublicSubnetId)"
DATABRICKS_VNET_PRIVATE_SUBNET_ID="$(read_output databricksPrivateSubnetId)"
SECURE_PRIVATE_ENDPOINT_SUBNET_ID="$(read_output privateEndpointSubnetId)"
KEYVAULT_NAME="${KEYVAULT_NAME:-$(read_output keyVaultName)}"
ACR_NAME="${ACR_NAME:-$(read_output acrName)}"
if [[ -z "$ACR_NAME" ]]; then
  ACR_NAME="$(image_registry_name "${PLANNER_API_IMAGE:-}")"
fi
if [[ -z "$ACR_NAME" ]]; then
  ACR_NAME="$(image_registry_name "${WRAPPER_IMAGE:-}")"
fi
LOG_ANALYTICS_NAME="${LOG_ANALYTICS_NAME:-$(read_output logAnalyticsWorkspaceName)}"
VNET_NAME="${VNET_NAME:-${SECURE_VNET_NAME:-}}"
if [[ -z "$VNET_NAME" && "$SECURE_MODE" == "true" ]]; then
  VNET_NAME="${NAME_PREFIX}-vnet"
fi

if ! resource_exists "$AZURE_RESOURCE_GROUP" "$ACA_ENVIRONMENT_NAME" "Microsoft.App/managedEnvironments"; then
  if [[ "$SECURE_MODE" == "true" ]]; then
    az containerapp env create \
      --name "$ACA_ENVIRONMENT_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --infrastructure-subnet-resource-id "$SECURE_ACA_SUBNET_ID" \
      >/dev/null
  else
    az containerapp env create \
      --name "$ACA_ENVIRONMENT_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      >/dev/null
  fi
fi

if ! az cognitiveservices account show -g "$AZURE_RESOURCE_GROUP" -n "$OPENAI_ACCOUNT_NAME" >/dev/null 2>&1; then
  purge_deleted_cognitive_account_if_needed "$OPENAI_ACCOUNT_NAME"
  az cognitiveservices account create \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$OPENAI_ACCOUNT_NAME" \
    -l "$AZURE_LOCATION" \
    --kind OpenAI \
    --sku S0 \
    --custom-domain "$OPENAI_ACCOUNT_NAME" \
    --yes \
    >/dev/null
fi

if ! az cognitiveservices account deployment show \
  -g "$AZURE_RESOURCE_GROUP" \
  -n "$OPENAI_ACCOUNT_NAME" \
  --deployment-name "$AZURE_OPENAI_DEPLOYMENT" \
  >/dev/null 2>&1; then
  az cognitiveservices account deployment create \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$OPENAI_ACCOUNT_NAME" \
    --deployment-name "$AZURE_OPENAI_DEPLOYMENT" \
    --model-format OpenAI \
    --model-name "$AZURE_OPENAI_MODEL_NAME" \
    --model-version "$AZURE_OPENAI_MODEL_VERSION" \
    --sku-name "$AZURE_OPENAI_DEPLOYMENT_SKU_NAME" \
    --sku-capacity "$AZURE_OPENAI_DEPLOYMENT_CAPACITY" \
    >/dev/null
fi

if [[ "$SECURE_MODE" == "true" ]]; then
  az resource update \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$OPENAI_ACCOUNT_NAME" \
    --resource-type "Microsoft.CognitiveServices/accounts" \
    --set properties.publicNetworkAccess=Disabled \
    >/dev/null
fi

if ! az cognitiveservices account show -g "$AZURE_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT_NAME" >/dev/null 2>&1; then
  purge_deleted_cognitive_account_if_needed "$FOUNDRY_ACCOUNT_NAME"
  az cognitiveservices account create \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$FOUNDRY_ACCOUNT_NAME" \
    -l "$AZURE_LOCATION" \
    --kind AIServices \
    --sku S0 \
    --custom-domain "$FOUNDRY_ACCOUNT_NAME" \
    --allow-project-management true \
    --yes \
    >/dev/null
fi

if [[ "$SECURE_MODE" == "true" ]]; then
  az resource update \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT_NAME" \
    --resource-type "Microsoft.CognitiveServices/accounts" \
    --set properties.publicNetworkAccess=Disabled \
    >/dev/null
fi

if [[ "$SECURE_MODE" == "true" ]]; then
  openai_resource_id="$(az cognitiveservices account show -g "$AZURE_RESOURCE_GROUP" -n "$OPENAI_ACCOUNT_NAME" --query id -o tsv)"
  foundry_resource_id="$(az cognitiveservices account show -g "$AZURE_RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT_NAME" --query id -o tsv)"
  keyvault_resource_id="$(az keyvault show -g "$AZURE_RESOURCE_GROUP" -n "$KEYVAULT_NAME" --query id -o tsv)"

  ensure_private_endpoint "${OPENAI_ACCOUNT_NAME}-pe" "$openai_resource_id" account "privatelink.openai.azure.com"
  ensure_private_endpoint "${FOUNDRY_ACCOUNT_NAME}-pe" "$foundry_resource_id" account "privatelink.cognitiveservices.azure.com"
  ensure_private_endpoint "${KEYVAULT_NAME}-pe" "$keyvault_resource_id" vault "privatelink.vaultcore.azure.net"
fi

if ! az cognitiveservices account project show \
  -g "$AZURE_RESOURCE_GROUP" \
  -n "$FOUNDRY_ACCOUNT_NAME" \
  --project-name "$FOUNDRY_PROJECT_NAME" \
  >/dev/null 2>&1; then
  az cognitiveservices account project create \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$FOUNDRY_ACCOUNT_NAME" \
    -l "$AZURE_LOCATION" \
    --project-name "$FOUNDRY_PROJECT_NAME" \
    --display-name "Daily Account Planner" \
    --description "Foundry management project for the Daily Account Planner ${DEPLOYMENT_MODE} environment." \
    >/dev/null
fi

workspace_state="$(databricks_workspace_state)"
if [[ "$workspace_state" == "Failed" ]]; then
  echo "Deleting failed Databricks workspace $DATABRICKS_WORKSPACE_NAME before recreate..."
  az databricks workspace delete \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$DATABRICKS_WORKSPACE_NAME" \
    --yes \
    >/dev/null
  while az databricks workspace show -g "$AZURE_RESOURCE_GROUP" -n "$DATABRICKS_WORKSPACE_NAME" >/dev/null 2>&1; do
    sleep 10
  done
  workspace_state=""
fi

if [[ -z "$workspace_state" ]]; then
  if [[ "$SECURE_MODE" == "true" ]]; then
    az databricks workspace create \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$DATABRICKS_WORKSPACE_NAME" \
      --location "$AZURE_LOCATION" \
      --sku premium \
      --managed-resource-group "$DATABRICKS_MANAGED_RESOURCE_GROUP" \
      --vnet "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.Network/virtualNetworks/$VNET_NAME" \
      --public-subnet "databricks-public" \
      --private-subnet "databricks-private" \
      --enable-no-public-ip true \
      --required-nsg-rules "$DATABRICKS_REQUIRED_NSG_RULES" \
      --public-network-access Disabled \
      >/dev/null
  else
    az databricks workspace create \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$DATABRICKS_WORKSPACE_NAME" \
      --location "$AZURE_LOCATION" \
      --sku premium \
      --managed-resource-group "$DATABRICKS_MANAGED_RESOURCE_GROUP" \
      --public-network-access Enabled \
      >/dev/null
  fi
fi

if [[ "$SECURE_MODE" == "true" ]]; then
  wait_for_databricks_workspace
  databricks_resource_id="$(az databricks workspace show \
    -g "$AZURE_RESOURCE_GROUP" \
    -n "$DATABRICKS_WORKSPACE_NAME" \
    --query id \
    -o tsv)"
  while IFS= read -r group_id; do
    [[ -z "$group_id" ]] && continue
    ensure_private_endpoint \
      "${DATABRICKS_WORKSPACE_NAME}-${group_id//_/}-pe" \
      "$databricks_resource_id" \
      "$group_id" \
      "privatelink.azuredatabricks.net"
  done < <(ensure_databricks_private_endpoints "$DATABRICKS_WORKSPACE_NAME" "$databricks_resource_id")
fi

resolved_workspace_url="$(az databricks workspace show \
  -g "$AZURE_RESOURCE_GROUP" \
  -n "$DATABRICKS_WORKSPACE_NAME" \
  --query workspaceUrl \
  -o tsv 2>/dev/null || true)"
if [[ -n "$resolved_workspace_url" ]]; then
  upsert_env_value "DATABRICKS_HOST" "https://${resolved_workspace_url}"
fi

cat <<EOF
Foundation deployed for DEPLOYMENT_MODE=$DEPLOYMENT_MODE.

Outputs file:
$FOUNDATION_OUTPUT_FILE

Export or persist these values for later deploy steps:
SECURE_DEPLOYMENT=$SECURE_MODE
ACA_ENVIRONMENT_NAME=$ACA_ENVIRONMENT_NAME
SECURE_ACA_SUBNET_ID=$SECURE_ACA_SUBNET_ID
SECURE_PRIVATE_ENDPOINT_SUBNET_ID=$SECURE_PRIVATE_ENDPOINT_SUBNET_ID
DATABRICKS_VNET_PUBLIC_SUBNET_ID=$DATABRICKS_VNET_PUBLIC_SUBNET_ID
DATABRICKS_VNET_PRIVATE_SUBNET_ID=$DATABRICKS_VNET_PRIVATE_SUBNET_ID
AZURE_OPENAI_ACCOUNT_NAME=$OPENAI_ACCOUNT_NAME
AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT
AZURE_AI_FOUNDRY_ACCOUNT_NAME=$FOUNDRY_ACCOUNT_NAME
AZURE_AI_FOUNDRY_PROJECT_NAME=$FOUNDRY_PROJECT_NAME
DATABRICKS_WORKSPACE_NAME=$DATABRICKS_WORKSPACE_NAME
KEYVAULT_NAME=$KEYVAULT_NAME
ACR_NAME=$ACR_NAME
LOG_ANALYTICS_NAME=$LOG_ANALYTICS_NAME
EOF
