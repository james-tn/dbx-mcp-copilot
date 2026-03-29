#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  setup-github-oidc.sh \
    --github-org <org> \
    --github-repo <repo> \
    --subscription-id <subscription-id> \
    --tenant-id <tenant-id> \
    --integration-scope <scope> \
    --production-scope <scope> \
    [--integration-acr-scope <scope>] \
    [--integration-keyvault-scope <scope>] \
    [--production-keyvault-scope <scope>] \
    [--bootstrap-scope <scope>] \
    [--integration-app-name <name>] \
    [--production-app-name <name>] \
    [--bootstrap-app-name <name>] \
    [--include-pull-request-subjects]

Example:
  bash mvp/infra/scripts/setup-github-oidc.sh \
    --github-org james-tn \
    --github-repo dbx-mcp-copilot \
    --subscription-id 00000000-0000-0000-0000-000000000000 \
    --tenant-id 11111111-1111-1111-1111-111111111111 \
    --integration-scope /subscriptions/<sub>/resourceGroups/rg-dap-int \
    --integration-acr-scope /subscriptions/<sub>/resourceGroups/rg-dap-int/providers/Microsoft.ContainerRegistry/registries/acrdapint \
    --integration-keyvault-scope /subscriptions/<sub>/resourceGroups/rg-dap-int/providers/Microsoft.KeyVault/vaults/kv-dap-int \
    --production-scope /subscriptions/<sub>/resourceGroups/rg-dap-prod \
    --production-keyvault-scope /subscriptions/<sub>/resourceGroups/rg-dap-prod/providers/Microsoft.KeyVault/vaults/kv-dap-prod \
    --bootstrap-scope /subscriptions/<sub>/resourceGroups/rg-dap-bootstrap

What this script does:
- creates one Entra application + service principal for integration
- creates one Entra application + service principal for production
- optionally creates one Entra application + service principal for bootstrap
- adds GitHub federated credentials bound to repo environments
- assigns the RBAC this repo expects:
  - integration: Contributor + AcrPush + optional Key Vault Secrets User
  - production: Contributor + optional Key Vault Secrets User
  - bootstrap-foundation: Contributor

This script intentionally does not grant User Access Administrator or Role
Based Access Control Administrator. Routine CI/CD in this repo is expected to
run with pre-provisioned RBAC and with AZURE_OPENAI_AUTO_ROLE_ASSIGN=false.
EOF
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
}

GITHUB_ORG=""
GITHUB_REPO=""
SUBSCRIPTION_ID=""
TENANT_ID=""
INTEGRATION_SCOPE=""
PRODUCTION_SCOPE=""
BOOTSTRAP_SCOPE=""
INTEGRATION_ACR_SCOPE=""
INTEGRATION_KEYVAULT_SCOPE=""
PRODUCTION_KEYVAULT_SCOPE=""
INTEGRATION_APP_NAME="gh-dbx-mcp-copilot-integration"
PRODUCTION_APP_NAME="gh-dbx-mcp-copilot-production"
BOOTSTRAP_APP_NAME="gh-dbx-mcp-copilot-bootstrap-foundation"
INCLUDE_PULL_REQUEST_SUBJECTS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --github-org)
      GITHUB_ORG="${2:-}"
      shift 2
      ;;
    --github-repo)
      GITHUB_REPO="${2:-}"
      shift 2
      ;;
    --subscription-id)
      SUBSCRIPTION_ID="${2:-}"
      shift 2
      ;;
    --tenant-id)
      TENANT_ID="${2:-}"
      shift 2
      ;;
    --integration-scope)
      INTEGRATION_SCOPE="${2:-}"
      shift 2
      ;;
    --production-scope)
      PRODUCTION_SCOPE="${2:-}"
      shift 2
      ;;
    --bootstrap-scope)
      BOOTSTRAP_SCOPE="${2:-}"
      shift 2
      ;;
    --integration-acr-scope)
      INTEGRATION_ACR_SCOPE="${2:-}"
      shift 2
      ;;
    --integration-keyvault-scope)
      INTEGRATION_KEYVAULT_SCOPE="${2:-}"
      shift 2
      ;;
    --production-keyvault-scope)
      PRODUCTION_KEYVAULT_SCOPE="${2:-}"
      shift 2
      ;;
    --integration-app-name)
      INTEGRATION_APP_NAME="${2:-}"
      shift 2
      ;;
    --production-app-name)
      PRODUCTION_APP_NAME="${2:-}"
      shift 2
      ;;
    --bootstrap-app-name)
      BOOTSTRAP_APP_NAME="${2:-}"
      shift 2
      ;;
    --include-pull-request-subjects)
      INCLUDE_PULL_REQUEST_SUBJECTS="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_command az
require_command python3

required_args=(
  GITHUB_ORG
  GITHUB_REPO
  SUBSCRIPTION_ID
  TENANT_ID
  INTEGRATION_SCOPE
  PRODUCTION_SCOPE
)

for name in "${required_args[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required argument: $name" >&2
    usage >&2
    exit 1
  fi
done

if [[ -z "$INTEGRATION_ACR_SCOPE" ]]; then
  INTEGRATION_ACR_SCOPE="$INTEGRATION_SCOPE"
fi

az account set --subscription "$SUBSCRIPTION_ID" >/dev/null

environment_subject() {
  local environment_name="$1"
  printf 'repo:%s/%s:environment:%s\n' "$GITHUB_ORG" "$GITHUB_REPO" "$environment_name"
}

pull_request_subject() {
  printf 'repo:%s/%s:pull_request\n' "$GITHUB_ORG" "$GITHUB_REPO"
}

ensure_app_registration() {
  local display_name="$1"
  local existing_app_id=""

  existing_app_id="$(
    az ad app list \
      --display-name "$display_name" \
      --query '[0].appId' \
      -o tsv 2>/dev/null || true
  )"

  if [[ -n "$existing_app_id" ]]; then
    printf '%s\n' "$existing_app_id"
    return 0
  fi

  az ad app create \
    --display-name "$display_name" \
    --sign-in-audience AzureADMyOrg \
    --query appId \
    -o tsv
}

ensure_service_principal() {
  local app_id="$1"
  if ! az ad sp show --id "$app_id" >/dev/null 2>&1; then
    az ad sp create --id "$app_id" >/dev/null
  fi
}

app_object_id() {
  local app_id="$1"
  az ad app show --id "$app_id" --query id -o tsv
}

ensure_federated_credential() {
  local app_object_id="$1"
  local credential_name="$2"
  local subject="$3"
  local existing=""
  local parameters=""

  existing="$(
    az ad app federated-credential list \
      --id "$app_object_id" \
      --query "[?name=='$credential_name'].name" \
      -o tsv 2>/dev/null || true
  )"

  if [[ -n "$existing" ]]; then
    return 0
  fi

  parameters="$(
    python3 - <<'PY' "$credential_name" "$subject"
import json
import sys

print(json.dumps({
    "name": sys.argv[1],
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": sys.argv[2],
    "audiences": ["api://AzureADTokenExchange"],
}))
PY
  )"

  az ad app federated-credential create \
    --id "$app_object_id" \
    --parameters "$parameters" \
    >/dev/null
}

ensure_role_assignment() {
  local assignee="$1"
  local role_name="$2"
  local scope="$3"
  local existing=""

  existing="$(
    az role assignment list \
      --assignee "$assignee" \
      --role "$role_name" \
      --scope "$scope" \
      --query '[0].id' \
      -o tsv 2>/dev/null || true
  )"

  if [[ -n "$existing" ]]; then
    return 0
  fi

  az role assignment create \
    --assignee "$assignee" \
    --role "$role_name" \
    --scope "$scope" \
    >/dev/null
}

configure_identity() {
  local app_name="$1"
  local environment_name="$2"
  local contributor_scope="$3"
  local acr_scope="$4"
  local keyvault_scope="$5"
  local app_id=""
  local object_id=""

  app_id="$(ensure_app_registration "$app_name")"
  ensure_service_principal "$app_id"
  object_id="$(app_object_id "$app_id")"

  ensure_federated_credential \
    "$object_id" \
    "github-${environment_name}" \
    "$(environment_subject "$environment_name")"

  if [[ "$INCLUDE_PULL_REQUEST_SUBJECTS" == "true" ]]; then
    ensure_federated_credential \
      "$object_id" \
      "github-pull-request" \
      "$(pull_request_subject)"
  fi

  ensure_role_assignment "$app_id" "Contributor" "$contributor_scope"

  if [[ -n "$acr_scope" ]]; then
    ensure_role_assignment "$app_id" "AcrPush" "$acr_scope"
  fi

  if [[ -n "$keyvault_scope" ]]; then
    ensure_role_assignment "$app_id" "Key Vault Secrets User" "$keyvault_scope"
  fi

  printf '%s\t%s\n' "$environment_name" "$app_id"
}

declare -a configured
configured+=("$(configure_identity "$INTEGRATION_APP_NAME" "integration" "$INTEGRATION_SCOPE" "$INTEGRATION_ACR_SCOPE" "$INTEGRATION_KEYVAULT_SCOPE")")
configured+=("$(configure_identity "$PRODUCTION_APP_NAME" "production" "$PRODUCTION_SCOPE" "" "$PRODUCTION_KEYVAULT_SCOPE")")

if [[ -n "$BOOTSTRAP_SCOPE" ]]; then
  configured+=("$(configure_identity "$BOOTSTRAP_APP_NAME" "bootstrap-foundation" "$BOOTSTRAP_SCOPE" "" "")")
fi

echo
echo "GitHub OIDC setup complete."
echo
echo "Put these values into the matching GitHub Environments:"
for entry in "${configured[@]}"; do
  env_name="${entry%%$'\t'*}"
  app_id="${entry#*$'\t'}"
  echo "- $env_name: AZURE_CLIENT_ID=$app_id"
done
echo "- shared: AZURE_TENANT_ID=$TENANT_ID"
echo "- shared: AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
echo
echo "RBAC assigned by this script:"
echo "- integration: Contributor on $INTEGRATION_SCOPE"
echo "- integration: AcrPush on $INTEGRATION_ACR_SCOPE"
if [[ -n "$INTEGRATION_KEYVAULT_SCOPE" ]]; then
  echo "- integration: Key Vault Secrets User on $INTEGRATION_KEYVAULT_SCOPE"
fi
echo "- production: Contributor on $PRODUCTION_SCOPE"
if [[ -n "$PRODUCTION_KEYVAULT_SCOPE" ]]; then
  echo "- production: Key Vault Secrets User on $PRODUCTION_KEYVAULT_SCOPE"
fi
if [[ -n "$BOOTSTRAP_SCOPE" ]]; then
  echo "- bootstrap-foundation: Contributor on $BOOTSTRAP_SCOPE"
fi
echo
echo "No role-assignment-admin roles were granted."

