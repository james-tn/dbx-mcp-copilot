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

Everything else is derived or backfilled into the generated runtime env,
including app IDs, secrets, image refs, container app URLs, Databricks
workspace values, bot metadata, and derived internal resource names.

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

## What The M365 Script Creates

`bootstrap-m365-demo.sh` uses the generated runtime env from the Azure step and:

1. builds the Teams/Copilot package
2. publishes the app package to the Teams app catalog
3. self-installs the app for the signed-in operator
4. writes the published Teams catalog app ID back into the runtime env when
   Graph returns it

## Required Azure Permissions

The Azure bootstrap assumes the signed-in operator can:

- select the target subscription
- create and update resource-group-scoped Azure resources
- create and update managed identities
- create and update Azure Container Apps and ACA Jobs
- create and update Azure Container Registry resources
- create and update Databricks, networking, private endpoints, and private DNS
- create and update Azure OpenAI, AI Foundry, and bot resources

For Entra ID, the operator must be able to create app registrations. A practical
operator role set is one of:

- `Application Administrator`
- `Cloud Application Administrator`
- `Global Administrator`

Admin consent may also require:

- `Application Administrator`
- `Cloud Application Administrator`
- `Privileged Role Administrator`
- `Global Administrator`

The Azure bootstrap now attempts admin consent automatically for:

- Planner API -> Azure Databricks `user_impersonation`
- Wrapper/channel app -> Planner API `access_as_user`

If the operator can create app registrations but cannot grant admin consent, the
Azure bootstrap can still finish, but Databricks delegated access and Teams
sign-in will remain blocked until an admin completes consent for the generated
applications.

## Required M365 / Graph Permissions

The M365 bootstrap needs delegated Microsoft Graph permission to:

- publish to the Teams app catalog:
  - `AppCatalog.Submit`, or
  - `AppCatalog.ReadWrite.All`, or
  - `Directory.ReadWrite.All`
- read the catalog entry for the app:
  - `AppCatalog.Read.All`
- install the app for the signed-in operator:
  - `TeamsAppInstallation.ReadWriteForUser`
  - `User.Read`

If `az account get-access-token --resource-type ms-graph` does not give a token
with those scopes, set `M365_GRAPH_PUBLISHER_CLIENT_ID` in the input env so the
bootstrap can use delegated device-code authentication against your publisher
app.

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

Missing Azure CLI extensions:

- Install the required extensions before rerunning:
  - `containerapp`
  - `databricks`

Unable to create app registrations:

- Grant an Entra role that can create app registrations, then rerun the Azure
  bootstrap.

Admin consent still pending:

- The Azure bootstrap may complete with consent still pending.
  Finish consent, then rerun the M365 bootstrap.

Graph token missing Teams publish/install scopes:

- Set `M365_GRAPH_PUBLISHER_CLIENT_ID` in the input env, rerun the M365
  bootstrap, and complete device-code sign-in.

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
- For a full clean rebuild, destroy the old environment, wait for Azure resource
  group deletion to finish, then rerun the Azure bootstrap from the same
  `*.inputs` file.

## Advanced / Manual Recovery

The new bootstraps are the recommended operator path. The lower-level scripts
remain available for recovery and debugging:

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
