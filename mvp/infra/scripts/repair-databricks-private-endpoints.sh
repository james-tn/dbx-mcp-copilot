#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-secure}"
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

if [[ "$SECURE_MODE" != "true" ]]; then
  echo "This repair script only applies to secure deployments." >&2
  exit 1
fi

NAME_PREFIX="${INFRA_NAME_PREFIX:-dailyacctplannersec}"
DATABRICKS_WORKSPACE_NAME="${DATABRICKS_WORKSPACE_NAME:-${NAME_PREFIX}-dbx}"
VNET_NAME="${SECURE_VNET_NAME:-${NAME_PREFIX}-vnet}"
PRIVATE_ENDPOINT_SUBNET_NAME="${PRIVATE_ENDPOINT_SUBNET_NAME:-private-endpoints}"
PRIVATE_DNS_ZONE_NAME="privatelink.azuredatabricks.net"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

workspace_id="$(az databricks workspace show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$DATABRICKS_WORKSPACE_NAME" \
  --query id \
  -o tsv)"

private_endpoint_subnet_id="$(az network vnet subnet show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --vnet-name "$VNET_NAME" \
  --name "$PRIVATE_ENDPOINT_SUBNET_NAME" \
  --query id \
  -o tsv)"

dns_zone_id="$(az network private-dns zone show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$PRIVATE_DNS_ZONE_NAME" \
  --query id \
  -o tsv)"

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

ensure_databricks_private_dns_record() {
  local pe_name="$1"

  local pe_json
  pe_json="$(az network private-endpoint show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    -o json)"

  python - <<'PY' "$pe_json" "$AZURE_RESOURCE_GROUP" "$PRIVATE_DNS_ZONE_NAME"
import json
import subprocess
import sys

payload = json.loads(sys.argv[1])
resource_group = sys.argv[2]
zone_name = sys.argv[3]

for config in payload.get("customDnsConfigs", []) or []:
    fqdn = str(config.get("fqdn") or "").strip().rstrip(".")
    ip_addresses = [str(ip).strip() for ip in (config.get("ipAddresses") or []) if str(ip).strip()]
    if not fqdn or not ip_addresses or not fqdn.endswith(".azuredatabricks.net"):
        continue

    record_name = fqdn[: -len(".azuredatabricks.net")].rstrip(".")
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
            "show",
            "--resource-group",
            resource_group,
            "--zone-name",
            zone_name,
            "--name",
            record_name,
            "-o",
            "json",
        ],
        text=True,
    )
    existing_payload = json.loads(existing)
    current_ips = {
        str(record.get("ipv4Address") or "").strip()
        for record in existing_payload.get("aRecords", []) or []
        if str(record.get("ipv4Address") or "").strip()
    }

    for ip_address in ip_addresses:
        if ip_address in current_ips:
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
                ip_address,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
PY
}

ensure_private_endpoint() {
  local pe_name="$1"
  local group_id="$2"

  delete_private_endpoint_if_unhealthy "$pe_name"

  if ! az network private-endpoint show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$pe_name" \
    >/dev/null 2>&1; then
    az network private-endpoint create \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$pe_name" \
      --location "$AZURE_LOCATION" \
      --subnet "$private_endpoint_subnet_id" \
      --private-connection-resource-id "$workspace_id" \
      --group-id "$group_id" \
      --connection-name "${pe_name}-connection" \
      >/dev/null
  fi

  if ! az network private-endpoint dns-zone-group show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --endpoint-name "$pe_name" \
    --name default \
    >/dev/null 2>&1; then
    az network private-endpoint dns-zone-group create \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --endpoint-name "$pe_name" \
      --name default \
      --private-dns-zone "$dns_zone_id" \
      --zone-name "$PRIVATE_DNS_ZONE_NAME" \
      >/dev/null
  fi

  ensure_databricks_private_dns_record "$pe_name"
}

private_link_resources_json="$(
  az databricks workspace private-link-resource list \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --workspace-name "$DATABRICKS_WORKSPACE_NAME" \
    -o json
)"

mapfile -t group_ids < <(
  python - <<'PY' "$private_link_resources_json"
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
)

if [[ "${#group_ids[@]}" -eq 0 ]]; then
  echo "No Databricks private link resource group IDs were returned." >&2
  exit 1
fi

for group_id in "${group_ids[@]}"; do
  pe_name="${DATABRICKS_WORKSPACE_NAME}-${group_id//_/}-pe"
  ensure_private_endpoint "$pe_name" "$group_id"
done

az network private-endpoint list \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --query "[?contains(name, '${DATABRICKS_WORKSPACE_NAME}')].{name:name,state:provisioningState}" \
  -o table

az network private-dns record-set a list \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --zone-name "$PRIVATE_DNS_ZONE_NAME" \
  --query "[].{name:name,ips:join(',', aRecords[].ipv4Address)}" \
  -o table
