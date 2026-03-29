# Daily Account Planner MVP Runbook

## Purpose

This runbook is the recommended operator path for Azure and Microsoft 365
deployment of the planner stack.

Default operator model:

- Azure hosts the planner and wrapper on top of an existing Databricks
  workspace and existing customer data sources
- Databricks provisioning and data seeding are not part of the default runbook

Optional mock operator model:

- if you explicitly want a mock Databricks environment for testing or parity
  validation, enable the mock Databricks path during bootstrap
- that path provisions the Azure demo foundation and runs the AIQ-shaped mock
  seed

The recommended flow is now two steps:

1. Azure bootstrap
2. M365 bootstrap

The operator edits only a small input env. The scripts generate and maintain the
runtime env used by the lower-level deployment scripts.

## Operator Models

This repo now supports two operator models:

1. Single-operator path
2. Split-responsibility path

Single-operator path:

- one operator runs the Azure bootstrap and the M365 bootstrap end to end
- that operator needs both Azure deployment rights and Entra / Graph rights

Split-responsibility path:

- the deployment operator runs the main bootstrap
- if the bootstrap hits an Entra or Teams catalog privilege boundary, it pauses
  and prints the next script to run
- the next admin completes only the missing approval/publish step
- the deployment operator then resumes from the printed next step

Status files:

- the scripts write their current state to
  [`mvp/infra/outputs/bootstrap-status-secure.json`](infra/outputs/bootstrap-status-secure.json)
  or
  [`mvp/infra/outputs/bootstrap-status-open.json`](infra/outputs/bootstrap-status-open.json)
- to inspect the current status, run:

```bash
bash mvp/infra/scripts/show-bootstrap-status.sh secure
bash mvp/infra/scripts/show-bootstrap-status.sh open
```

## Quick Start: Azure Bootstrap

1. Copy the right input template:

```bash
cp mvp/.env.secure.inputs.example mvp/.env.secure.inputs
cp mvp/.env.inputs.example mvp/.env.inputs
```

2. Fill the required blanks in the file you plan to use.

Secure mode:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`

Open mode:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`

Optional mock/demo-only identities:

- `SELLER_A_UPN`
- `SELLER_B_UPN`

Use those only when you intentionally want the seeded demo or seller-validation
helpers to exercise two distinct sample users. They are not required for the
normal customer-hosted path against an existing Databricks workspace.

If you are deploying against an existing production Databricks workspace with no
workspace provisioning and no seed step, also fill these in the input env:

- `CUSTOMER_DATABRICKS_HOST`
- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID` when the Azure Databricks workspace
  requires the workspace resource header
- `CUSTOMER_DATABRICKS_WAREHOUSE_ID` if you want to pin a warehouse; otherwise
  leave it blank and let the planner resolve one dynamically
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`
- `CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH`
  default: `fixtures/customer_rep_lookup_static_map.json`
- optional when the customer `sf_vpower_bronze` tables are not on the workspace
  default catalog:
  - `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
  - `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`

In that default existing-workspace mode, the Azure bootstrap does not seed or
mutate Databricks at all. Existing users, grants, warehouse access, and table
permissions must already be present on the customer workspace.

3. Sign in to Azure:

```bash
az login
```

4. Run the Azure bootstrap:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
bash mvp/infra/scripts/bootstrap-azure-demo.sh open
```

The script renders and maintains:

- [`mvp/.env.secure`](.env.secure)
- [`mvp/.env`](.env)

Do not treat those files as operator-owned. They are generated runtime state.

On reruns, the Azure bootstrap reuses an existing foundation instead of
replaying the secure foundation deployment across a live Databricks workspace.

## Quick Start: Split Responsibility

If no single user has all required rights, enable split mode:

```bash
SPLIT_RESPONSIBILITY_MODE=true bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
SPLIT_RESPONSIBILITY_MODE=true bash mvp/infra/scripts/bootstrap-azure-demo.sh open
```

If the Azure bootstrap pauses for Entra admin work, run:

```bash
bash mvp/infra/scripts/complete-entra-admin-consent.sh secure
bash mvp/infra/scripts/complete-entra-admin-consent.sh open
```

If the M365 bootstrap pauses for Teams catalog publish work, run:

```bash
bash mvp/infra/scripts/complete-m365-catalog-publish.sh secure
bash mvp/infra/scripts/complete-m365-catalog-publish.sh open
```

Then resume with the next step printed by the status file or the bootstrap
message, usually:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

## Quick Start: M365 Bootstrap

After the Azure bootstrap finishes successfully, run:

```bash
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
bash mvp/infra/scripts/bootstrap-m365-demo.sh open
```

The M365 bootstrap:

- builds the Teams/Copilot app package
- publishes it to the Teams app catalog
- installs it for the signed-in operator

If the Graph token does not already have the required delegated scopes, set
`M365_GRAPH_PUBLISHER_CLIENT_ID` in the input env and the script will use device
code flow for the Graph publish/install step.

## Required Input Parameters To Fill

Secure defaults are already prefilled in
[`mvp/.env.secure.inputs.example`](.env.secure.inputs.example):

- `AZURE_RESOURCE_GROUP=rg-daily-account-planner-secure`
- `AZURE_LOCATION=eastus2`
- `SECURE_DEPLOYMENT=true`
- `DEPLOYMENT_MODE=secure`
- `INFRA_NAME_PREFIX=dailyacctplannersec`

Open defaults are already prefilled in
[`mvp/.env.inputs.example`](.env.inputs.example):

- `AZURE_RESOURCE_GROUP=rg-daily-account-planner`
- `AZURE_LOCATION=eastus`
- `SECURE_DEPLOYMENT=false`
- `DEPLOYMENT_MODE=open`
- `INFRA_NAME_PREFIX=dailyacctplanneropen`

Optional operator input:

- `M365_GRAPH_PUBLISHER_CLIENT_ID`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_MODEL`
- `AZURE_OPENAI_MODEL_NAME`
- `AZURE_OPENAI_MODEL_VERSION`
- `AZURE_OPENAI_DEPLOYMENT_CAPACITY`

Everything else is derived or backfilled into the generated runtime env,
including app IDs, secrets, image refs, container app URLs, Databricks
workspace values, bot metadata, and derived internal resource names.

Production existing-Databricks note:

- the planner can only query the customer AIQ tables after Databricks grants the
  signed-in user path it uses
- in hosted secure mode, the planner reaches Databricks over private networking,
  but Databricks must still authorize the delegated user/OBO path to use SQL
  warehouses and read:
  - `prod_catalog.data_science_account_iq_gold.account_iq_scores`
  - `prod_catalog.account_iq_gold.aiq_contact`
- if this grant is missing, the planner returns a backend execution/access
  failure even though M365, wrapper routing, and private networking are healthy
- the bootstrap does not attempt to create those permissions on an existing
  customer workspace

Starter Azure OpenAI default:

- both open and secure mode now default to `gpt-5.2-chat` with
  `GlobalStandard` capacity `500`
- that smaller starter footprint makes it easier to host both demo
  environments in the same subscription
- if your tenant uses a different quota profile, set the optional
  `AZURE_OPENAI_*` overrides in the operator input env before bootstrap

Open-mode app registration naming:

- open mode now derives its Entra app-registration display-name prefix from
  `INFRA_NAME_PREFIX`
- that prevents ambiguous reuse when the same tenant already contains older
  generic `daily-account-planner-*` app registrations from previous demos

## What The Azure Script Creates

`bootstrap-azure-demo.sh` renders the runtime env, runs preflight checks,
builds both images with `az acr build`, and then orchestrates the lower-level
scripts in this order:

1. foundation deploy
2. Entra app registrations and secrets
3. Databricks runtime selection for existing-workspace or mock-seeded mode
4. planner deployment
5. optional AIQ mock Databricks seed and foundation-workspace access bootstrap
   when explicitly enabled
6. wrapper deployment
7. Azure Bot resource
8. bot OAuth connection

Expected Azure-side outputs include:

- resource group and shared foundation resources
- Azure Container Apps environment
- planner container app
- wrapper container app
- Azure OpenAI and AI Foundry resources
- optional Databricks workspace when you intentionally use the mock path
- ACR image refs written into the runtime env
- planner API app registration
- bot / wrapper app registration
- Azure Bot resource and OAuth connection

Secure ACR exception:

- in secure mode, the operator path intentionally leaves Azure Container
  Registry public-network-access enabled so `az acr build` can run from
  Microsoft-managed build agents
- the secure networking model still applies to Databricks, private endpoints,
  private DNS, and the in-network seed path

Default existing-Databricks note:

- the default secure customer path expects you to supply the existing Databricks
  connection values in the runtime env
- the planner then connects directly to those existing sources:
  - `CUSTOMER_TOP_OPPORTUNITIES_SOURCE=prod_catalog.data_science_account_iq_gold.account_iq_scores`
  - `CUSTOMER_CONTACTS_SOURCE=prod_catalog.account_iq_gold.aiq_contact`
- Account Pulse and sales-team resolution now default to built-in customer
  Databricks queries against `sf_vpower_bronze`
- if `sf_vpower_bronze` is not on the workspace default catalog, set
  `CUSTOMER_SCOPE_ACCOUNTS_CATALOG` and `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`
- the bootstrap does not create workspace users, warehouse permissions, or Unity
  Catalog grants on that existing customer workspace

Optional mock Databricks note:

- when `ENABLE_MOCK_DATABRICKS_ENVIRONMENT=true`, the bootstrap also runs
  [`seed-databricks-aiq-dev.sh`](infra/scripts/seed-databricks-aiq-dev.sh)
  to create AIQ-shaped mock tables plus `sf_vpower_bronze` territory/account
  tables derived from the customer workbook sample for parity testing
- the mock seed path targets only the foundation `DATABRICKS_*` workspace
  values and never falls back to `CUSTOMER_DATABRICKS_*`
- when mock mode is enabled, the bootstrap rewires the planner's active
  `CUSTOMER_*` Databricks settings to the seeded foundation workspace before
  planner deployment

## What The M365 Script Creates

`bootstrap-m365-demo.sh` uses the generated runtime env from the Azure step and:

1. builds the Teams/Copilot package
2. publishes the app package to the Teams app catalog
3. self-installs the app for the signed-in operator
4. writes the published Teams catalog app ID back into the runtime env when
   Graph returns it

## Split-Role Scripts

Use these only when the main bootstrap pauses at a privilege boundary:

- [`mvp/infra/scripts/complete-entra-admin-consent.sh`](infra/scripts/complete-entra-admin-consent.sh)
  completes Entra app registration and admin consent without rotating already
  persisted app secrets
- [`mvp/infra/scripts/complete-m365-catalog-publish.sh`](infra/scripts/complete-m365-catalog-publish.sh)
  builds and publishes the Teams app package to the catalog, then hands control
  back to the deployment operator for self-install
- [`mvp/infra/scripts/show-bootstrap-status.sh`](infra/scripts/show-bootstrap-status.sh)
  prints the current mode status, the last successful step, and the next role /
  script required to continue

## Required Azure Permissions

Deployment operator:

- select the target subscription
- create and update resource-group-scoped Azure resources
- create and update managed identities
- create and update Azure Container Apps and ACA Jobs
- create and update Azure Container Registry resources
- create and update Databricks, networking, private endpoints, and private DNS
- create and update Azure OpenAI, AI Foundry, and bot resources

Entra admin:

- must be able to create app registrations if the deployment operator cannot
- must be able to grant tenant-wide admin consent for:
  - Planner API -> Azure Databricks `user_impersonation`
  - Wrapper/channel app -> Planner API `access_as_user`

A practical Entra admin role set is one of:

- `Application Administrator`
- `Cloud Application Administrator`
- `Privileged Role Administrator`
- `Global Administrator`

If split mode is enabled and admin consent is missing, the Azure bootstrap now
pauses and points to the Entra admin completion script instead of forcing a
single operator to hold every role.

## Required M365 / Graph Permissions

M365 catalog admin:

- publish to the Teams app catalog:
  - `AppCatalog.Submit`, or
  - `AppCatalog.ReadWrite.All`, or
  - `Directory.ReadWrite.All`
- read the catalog entry for the app:
  - `AppCatalog.Read.All`

Deployment operator for self-install:

- install the app for the signed-in operator:
  - `TeamsAppInstallation.ReadWriteForUser`
  - `User.Read`

If `az account get-access-token --resource-type ms-graph` does not give a token
with those scopes, set `M365_GRAPH_PUBLISHER_CLIENT_ID` in the input env so the
bootstrap can use delegated device-code authentication against your publisher
app. In split mode, the Teams catalog publish step can also be run separately by
the M365 catalog admin.

## Demo Access-Control Model

The data access control demo intentionally uses two users:

- `SELLER_A_UPN`
- `SELLER_B_UPN`

Those are now operator-selected instead of repo-hard-coded.

The bootstrap uses them to drive:

- wrapper debug allow-list defaults
- seller validation scripts

If you intentionally enable the optional mock Databricks path, those users can
still be used to exercise different seller views during testing.

For customer-hosted deployments against an existing Databricks workspace, treat
these as optional demo helpers rather than normal production setup inputs.

## Common Failure Cases And Recovery

Missing input values:

- If a bootstrap says the input env is missing variables, fill the named values
  in [`mvp/.env.inputs`](.env.inputs)
  or [`mvp/.env.secure.inputs`](.env.secure.inputs),
  then rerun the same bootstrap.

Not signed in to Azure:

- Run `az login` and rerun the bootstrap.

Admin consent missing:

- If the live app says it cannot retrieve scoped accounts or delegated access is
  unavailable, check the generated Entra apps and complete admin consent for:
  - Planner API -> Azure Databricks `user_impersonation`
  - Wrapper/channel app -> Planner API `access_as_user`
- In split mode, run:
  - `bash mvp/infra/scripts/complete-entra-admin-consent.sh secure`
  - `bash mvp/infra/scripts/complete-entra-admin-consent.sh open`

Missing Azure CLI extensions:

- Install the required extensions before rerunning:
  - `containerapp`
  - `databricks`

Unable to create app registrations:

- Grant an Entra role that can create app registrations, then rerun the Azure
  bootstrap.
- In split mode, if the deployment operator cannot create apps, have an Entra
  admin rerun the Azure bootstrap or complete the Entra admin step, depending on
  what the status file says.

Admin consent still pending:

- In single-operator mode, the Azure bootstrap treats this as a blocking
  failure.
- In split mode, the Azure bootstrap pauses and writes the next role / next
  script into the bootstrap status file.

Azure OpenAI deployment creation fails with quota or model-capacity errors:

- Set the optional `AZURE_OPENAI_*` override values in
  [`mvp/.env.inputs`](.env.inputs)
  or [`mvp/.env.secure.inputs`](.env.secure.inputs)
  so the bootstrap requests a deployment footprint your tenant can host.

Graph token missing Teams publish/install scopes:

- Set `M365_GRAPH_PUBLISHER_CLIENT_ID` in the input env, rerun the M365
  bootstrap, and complete device-code sign-in.
- In split mode, if the current operator does not have catalog-publish scope,
  run:
  - `bash mvp/infra/scripts/complete-m365-catalog-publish.sh secure`
  - `bash mvp/infra/scripts/complete-m365-catalog-publish.sh open`
- Then rerun:
  - `bash mvp/infra/scripts/bootstrap-m365-demo.sh secure`
  - `bash mvp/infra/scripts/bootstrap-m365-demo.sh open`

Secure Databricks is private from the operator machine:

- In secure mode, the runbook intentionally skips local direct-query validation
  from the operator machine. The secure ACA seed job is the in-network seed path.

ACR build fails with registry firewall or public-access errors:

- rerun the Azure bootstrap after confirming the registry is public-network
  enabled
- the recommended operator path now keeps the secure-mode ACR public on purpose
  so `az acr build` can log in from Microsoft-managed build infrastructure

Repeatability and reruns:

- Re-running the Azure bootstrap is the normal convergence path.
- Re-running the M365 bootstrap is safe when you need to republish or reinstall.
- If split mode pauses a run, check the status file before rerunning:
  - `bash mvp/infra/scripts/show-bootstrap-status.sh secure`
  - `bash mvp/infra/scripts/show-bootstrap-status.sh open`
- For a full clean rebuild, destroy the old environment, wait for Azure resource
  group deletion to finish, then rerun the Azure bootstrap from the same
  `*.inputs` file.

## Debugging And Troubleshooting

Secure ACA apps do not require shell access for first-line debugging:

- the ACA environment sends logs to Azure Log Analytics during foundation deploy
- use Azure Monitor / Log Analytics queries first, even for private secure-mode
  apps that are not directly reachable from the operator machine

Recommended startup steps:

```bash
cd <repo>/mvp
set -a
source .env.secure
set +a

workspace_id=$(az monitor log-analytics workspace show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS_NAME" \
  --query customerId -o tsv)
```

Find the latest ready revision for the app you want to inspect:

```bash
az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$WRAPPER_ACA_APP_NAME" \
  --query properties.latestReadyRevisionName -o tsv
```

Use console logs for application stdout/stderr:

```bash
az monitor log-analytics query -w "$workspace_id" \
  --analytics-query "
    ContainerAppConsoleLogs
    | where TimeGenerated > ago(30m)
    | where ContainerAppName == '$WRAPPER_ACA_APP_NAME'
    | project TimeGenerated, ContainerAppName, RevisionName, ContainerName, Log
    | top 100 by TimeGenerated desc
  " -o table
```

Use system logs for revision, replica, probe, startup, and platform failures:

```bash
az monitor log-analytics query -w "$workspace_id" \
  --analytics-query "
    ContainerAppSystemLogs
    | where TimeGenerated > ago(30m)
    | where ContainerAppName == '$WRAPPER_ACA_APP_NAME'
    | project TimeGenerated, ContainerAppName, RevisionName, ReplicaName, Reason, Log
    | top 100 by TimeGenerated desc
  " -o table
```

Filter to one specific revision when you are debugging a rollout:

```bash
revision_name=$(az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$PLANNER_ACA_APP_NAME" \
  --query properties.latestReadyRevisionName -o tsv)

az monitor log-analytics query -w "$workspace_id" \
  --analytics-query "
    ContainerAppConsoleLogs
    | where TimeGenerated > ago(30m)
    | where ContainerAppName == '$PLANNER_ACA_APP_NAME'
    | where RevisionName == '$revision_name'
    | project TimeGenerated, RevisionName, Log
    | top 100 by TimeGenerated desc
  " -o table
```

Practical guidance:

- `ContainerAppConsoleLogs` is the best first stop for Python tracebacks,
  import failures, auth errors, and request-time exceptions
- `ContainerAppSystemLogs` is the best first stop for image-pull failures,
  bad startup commands, health-probe failures, crash loops, and revision
  provisioning problems
- in this environment the table names are `ContainerAppConsoleLogs` and
  `ContainerAppSystemLogs` without the older `_CL` suffix
- secure mode does not change this workflow; the app can stay private while
  logs remain queryable through Azure Monitor
- if the log query returns nothing, widen the window from `ago(30m)` to
  `ago(24h)` and confirm you are filtering on the right `ContainerAppName`

Optional mock Databricks note:

- the default secure customer path does not run Databricks seed jobs
- if you intentionally enabled `ENABLE_MOCK_DATABRICKS_ENVIRONMENT=true`, use
  [`mvp/infra/scripts/seed-databricks-aiq-dev.sh`](infra/scripts/seed-databricks-aiq-dev.sh)
  to refresh the AIQ-shaped mock tables
- if the mock seed fails, first verify the foundation `DATABRICKS_HOST`,
  `DATABRICKS_WAREHOUSE_ID`, and `AIQ_DEV_CATALOG` values in the runtime env
- the Databricks access bootstrap helper is for the mock/foundation workspace
  path only, not for an existing customer workspace

## Advanced / Manual Recovery

The new bootstraps are the recommended operator path. The lower-level scripts
remain available for recovery and debugging:

- [`mvp/infra/scripts/show-bootstrap-status.sh`](infra/scripts/show-bootstrap-status.sh)
- [`mvp/infra/scripts/complete-entra-admin-consent.sh`](infra/scripts/complete-entra-admin-consent.sh)
- [`mvp/infra/scripts/complete-m365-catalog-publish.sh`](infra/scripts/complete-m365-catalog-publish.sh)
- [`mvp/infra/scripts/deploy-foundation.sh`](infra/scripts/deploy-foundation.sh)
- [`mvp/infra/scripts/setup-custom-engine-app-registrations.sh`](infra/scripts/setup-custom-engine-app-registrations.sh)
- [`mvp/infra/scripts/build-and-deploy-planner-only.sh`](infra/scripts/build-and-deploy-planner-only.sh)
- [`mvp/infra/scripts/build-and-deploy-wrapper-only.sh`](infra/scripts/build-and-deploy-wrapper-only.sh)
- [`mvp/infra/scripts/bootstrap-databricks-access.sh`](infra/scripts/bootstrap-databricks-access.sh)
- [`mvp/infra/scripts/deploy-planner-api.sh`](infra/scripts/deploy-planner-api.sh)
- [`mvp/infra/scripts/seed-databricks-aiq-dev.sh`](infra/scripts/seed-databricks-aiq-dev.sh)
- [`mvp/infra/scripts/deploy-m365-wrapper.sh`](infra/scripts/deploy-m365-wrapper.sh)
- [`mvp/infra/scripts/create-azure-bot-resource.sh`](infra/scripts/create-azure-bot-resource.sh)
- [`mvp/infra/scripts/setup-bot-oauth-connection.sh`](infra/scripts/setup-bot-oauth-connection.sh)
- [`mvp/scripts/build-m365-app-package.sh`](scripts/build-m365-app-package.sh)
- [`mvp/scripts/publish-m365-app-package-graph.sh`](scripts/publish-m365-app-package-graph.sh)
- [`mvp/scripts/install-m365-app-for-self-graph.sh`](scripts/install-m365-app-for-self-graph.sh)

## Secure Customer Planner Updates

For the hosted secure customer path:

- use [`.env.secure.example`](.env.secure.example) as the only hosted env template
- set the existing Databricks values in [`mvp/.env.secure.inputs`](.env.secure.inputs) before bootstrap, or in [`.env.secure`](.env.secure) before planner-only redeploys
- set `CUSTOMER_DATABRICKS_HOST` at deployment time
- set `CUSTOMER_DATABRICKS_WAREHOUSE_ID` only if you want to pin a specific SQL warehouse; blank is allowed
- keep `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID` when the target Azure Databricks workspace requires the workspace resource header, but do not treat it as mandatory
- leave `CUSTOMER_DATABRICKS_OBO_SCOPE` at the default Azure Databricks delegated scope unless the customer explicitly requires a different resource
- Next Move defaults to:
  - `CUSTOMER_TOP_OPPORTUNITIES_SOURCE=prod_catalog.data_science_account_iq_gold.account_iq_scores`
  - `CUSTOMER_CONTACTS_SOURCE=prod_catalog.account_iq_gold.aiq_contact`
- Account Pulse and sales-team resolution default to the built-in `sf_vpower_bronze`
  queries in planner code; no hosted static scope JSON is required
- if the bronze tables require catalog qualification, set:
  - `CUSTOMER_SCOPE_ACCOUNTS_CATALOG=<catalog>`
  - `CUSTOMER_SALES_TEAM_MAPPING_CATALOG=<catalog>`
- customer rep lookup defaults to:
  - `CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH=fixtures/customer_rep_lookup_static_map.json`

Validate the new customer query path directly by user email:

```bash
ENV_FILE=mvp/.env.secure VALIDATE_USER_UPN=<seller-upn> \
  bash mvp/infra/scripts/validate-customer-vpower-query.sh
```

`VALIDATE_USER_UPN` is validation-only. It is not required for planner or
wrapper deployment.

Routine planner-only code updates should leave the deployed wrapper intact:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-planner-only.sh
```

Routine wrapper-only code updates should leave the planner intact:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-wrapper-only.sh
```

Use the full stack deploy only when you intentionally need planner plus wrapper:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-customer-stack.sh
```

For local planner testing without Microsoft 365:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/run-local-planner-chat.sh
```

- the local chat app reuses the planner HTTP API and wrapper debug-auth helpers
- use `.env`, not `.env.secure`, because the secure planner is expected to be
  private from local/operator access

For local simulated customer-scope scenario coverage without Microsoft 365 or live Databricks login:

```bash
bash mvp/infra/scripts/run-local-simulated-customer-scenarios.sh
```

- this uses simulated signed-in identity context and local fakes
- it validates the Account Pulse empty-scope message, dynamic Next Move scope
  prompt, signed-in-scope top-opps defaulting, and comma-separated territory overrides

- if secure hosted Next Move still reports a Databricks execution/access error,
  verify the existing Databricks grants for the planner's delegated user path before
  republishing the M365 app; wrapper-to-planner routing alone is not sufficient
