# Daily Account Planner MVP Runbook

## Purpose

This runbook is the recommended operator path for bringing up the full demo
environment in Azure and Microsoft 365 with seed data and the two-user access
control demo.

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
  [`mvp/infra/outputs/bootstrap-status-secure.json`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/outputs/bootstrap-status-secure.json)
  or
  [`mvp/infra/outputs/bootstrap-status-open.json`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/outputs/bootstrap-status-open.json)
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
- `SELLER_A_UPN`
- `SELLER_B_UPN`

Open mode:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`
- `SELLER_A_UPN`
- `SELLER_B_UPN`

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

- [`mvp/.env.secure`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure)
- [`mvp/.env`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env)

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
[`mvp/.env.secure.inputs.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.inputs.example):

- `AZURE_RESOURCE_GROUP=rg-daily-account-planner-secure`
- `AZURE_LOCATION=eastus2`
- `SECURE_DEPLOYMENT=true`
- `DEPLOYMENT_MODE=secure`
- `INFRA_NAME_PREFIX=dailyacctplannersec`

Open defaults are already prefilled in
[`mvp/.env.inputs.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.inputs.example):

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
3. planner deployment
4. Databricks seed
5. wrapper deployment
6. Azure Bot resource
7. bot OAuth connection

Expected Azure-side outputs include:

- resource group and shared foundation resources
- Azure Container Apps environment
- planner container app
- wrapper container app
- Azure OpenAI and AI Foundry resources
- Databricks workspace
- ACR image refs written into the runtime env
- planner API app registration
- bot / wrapper app registration
- secure seed job in secure mode
- Azure Bot resource and OAuth connection

Secure ACR exception:

- in secure mode, the operator path intentionally leaves Azure Container
  Registry public-network-access enabled so `az acr build` can run from
  Microsoft-managed build agents
- the secure networking model still applies to Databricks, private endpoints,
  private DNS, and the in-network seed path

Secure Databricks catalog note:

- the secure bootstrap defaults `DATABRICKS_SKIP_CATALOG_CREATE=true`
- the Azure script reuses the Databricks workspace catalog for secure seed data
  instead of trying to create a fresh managed catalog like `veeam_demo`
- this avoids metastore failures on tenants where Databricks catalog creation
  requires an explicit storage root

Open Databricks catalog note:

- open mode now auto-detects the workspace catalog available in the fresh
  Databricks SQL warehouse and reuses it when the legacy `veeam_demo` catalog
  path is not available

## What The M365 Script Creates

`bootstrap-m365-demo.sh` uses the generated runtime env from the Azure step and:

1. builds the Teams/Copilot package
2. publishes the app package to the Teams app catalog
3. self-installs the app for the signed-in operator
4. writes the published Teams catalog app ID back into the runtime env when
   Graph returns it

## Split-Role Scripts

Use these only when the main bootstrap pauses at a privilege boundary:

- [`mvp/infra/scripts/complete-entra-admin-consent.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/complete-entra-admin-consent.sh)
  completes Entra app registration and admin consent without rotating already
  persisted app secrets
- [`mvp/infra/scripts/complete-m365-catalog-publish.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/complete-m365-catalog-publish.sh)
  builds and publishes the Teams app package to the catalog, then hands control
  back to the deployment operator for self-install
- [`mvp/infra/scripts/show-bootstrap-status.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/show-bootstrap-status.sh)
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

- Databricks workspace-user bootstrap
- security entitlements in
  [`mvp/infra/databricks/seed-databricks-ri.sql`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/databricks/seed-databricks-ri.sql)
- grants on the secure catalog objects
- wrapper debug allow-list defaults
- seller validation scripts

The seed assigns different territories to the two sellers so the demo can show
distinct results for seller A versus seller B.

## Common Failure Cases And Recovery

Missing input values:

- If a bootstrap says the input env is missing variables, fill the named values
  in [`mvp/.env.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.inputs)
  or [`mvp/.env.secure.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.inputs),
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
  [`mvp/.env.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.inputs)
  or [`mvp/.env.secure.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.inputs)
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
cd /mnt/c/testing/veeam/revenue_intelligence/mvp
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

## Advanced / Manual Recovery

The new bootstraps are the recommended operator path. The lower-level scripts
remain available for recovery and debugging:

- [`mvp/infra/scripts/show-bootstrap-status.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/show-bootstrap-status.sh)
- [`mvp/infra/scripts/complete-entra-admin-consent.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/complete-entra-admin-consent.sh)
- [`mvp/infra/scripts/complete-m365-catalog-publish.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/complete-m365-catalog-publish.sh)
- [`mvp/infra/scripts/deploy-foundation.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/deploy-foundation.sh)
- [`mvp/infra/scripts/setup-custom-engine-app-registrations.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-custom-engine-app-registrations.sh)
- [`mvp/infra/scripts/deploy-planner-api.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/deploy-planner-api.sh)
- [`mvp/infra/scripts/seed-databricks-ri.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/seed-databricks-ri.sh)
- [`mvp/infra/scripts/deploy-m365-wrapper.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/deploy-m365-wrapper.sh)
- [`mvp/infra/scripts/create-azure-bot-resource.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/create-azure-bot-resource.sh)
- [`mvp/infra/scripts/setup-bot-oauth-connection.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-bot-oauth-connection.sh)
- [`mvp/scripts/build-m365-app-package.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/scripts/build-m365-app-package.sh)
- [`mvp/scripts/publish-m365-app-package-graph.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/scripts/publish-m365-app-package-graph.sh)
- [`mvp/scripts/install-m365-app-for-self-graph.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/scripts/install-m365-app-for-self-graph.sh)
