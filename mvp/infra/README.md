# Daily Account Planner Infra

`mvp/infra` is the canonical home for infrastructure, deploy, optional mock
seed, and environment validation assets.

## Layout

- `bicep/`: shared foundation templates used by both open and secure stacks
- `databricks/`: optional Databricks mock-seed SQL assets
- `scripts/`: app registration, foundation deploy, service deploy, seed, and
  validation entrypoints
- `outputs/`: deployment output snapshots written by the bash wrappers

## Main entrypoints

Recommended operator flow:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

Open mode uses the same two-step flow:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh open
bash mvp/infra/scripts/bootstrap-m365-demo.sh open
```

The new bootstrap scripts:

- read the small operator-owned `*.inputs` env files
- generate and maintain `.env` / `.env.secure`
- fail early when the operator cannot grant required Entra admin consent
- build and publish the planner and wrapper images automatically
- reuse an existing foundation on reruns instead of replaying the secure
  foundation deployment across a live Databricks workspace
- drive the existing lower-level scripts in the supported order
- keep the lower-level scripts compatible for manual recovery
- preserve generated runtime values only when the same bootstrap input
  signature still matches the current tenant / subscription / prefix / demo-user
  set
- default both open and secure mode to a smaller starter Azure OpenAI
  deployment footprint (`gpt-5.2-chat`, `GlobalStandard`, capacity `500`) so a
  customer demo tenant can more easily host both environments at once
- create the Container Apps environment against the named Log Analytics
  workspace instead of letting Azure generate a random fallback workspace on the
  first run

Customer-target path:

- for an existing secured Databricks environment, use the secure runtime env
  and deploy scripts in place:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-customer-stack.sh
```

- for routine planner-only updates that leave the already-deployed wrapper
  untouched, use:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-planner-only.sh
```

- for routine wrapper-only updates that leave the planner untouched, use:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-wrapper-only.sh
```

- this is the default hosted-secure operator model: Azure hosts the planner and
  wrapper on top of an existing Databricks workspace and existing customer data
  sources
- customer scoped accounts and territory resolution now default to built-in
  `sf_vpower_bronze` queries in the planner runtime
- no Databricks provisioning or data seeding is part of the default secure
  customer runbook
- the secure bootstrap does not mutate the existing customer workspace
- existing Databricks permissions, users, and grants must already be in place
  outside this runbook

Optional mock Databricks path:

- use this only when you intentionally want a mock environment for testing
- enable it explicitly during Azure bootstrap:

```bash
ENABLE_MOCK_DATABRICKS_ENVIRONMENT=true bash mvp/infra/scripts/bootstrap-azure-demo.sh open
ENABLE_MOCK_DATABRICKS_ENVIRONMENT=true bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
```

- that path uses [`seed-databricks-aiq-dev.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/seed-databricks-aiq-dev.sh)
  to create the AIQ-shaped mock tables plus `sf_vpower_bronze` mock tables used
  by the built-in customer scope queries
- the mock seed path targets only the bootstrap/foundation `DATABRICKS_*`
  workspace values
- it does not fall back to `CUSTOMER_DATABRICKS_*`, so it cannot accidentally
  seed an existing customer workspace
- when `MOCK_DATABRICKS_ENVIRONMENT=true`, the planner can use that mock
  workspace instead of an existing customer workspace because the bootstrap
  rewires the planner's active `CUSTOMER_*` Databricks settings to the seeded
  foundation workspace before planner deployment

Local planner chat:

- for local planner-only testing without Microsoft 365, run:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/run-local-planner-chat.sh
```

- the local chat app uses the same planner HTTP API and env settings, but it is
  intended for open/local access only

Local simulated customer-scope scenarios:

```bash
bash mvp/infra/scripts/run-local-simulated-customer-scenarios.sh
```

- this runs local pytest-backed scenarios with a simulated signed-in seller identity
- it covers the Account Pulse empty-scope message, dynamic Next Move scope prompt,
  signed-in-scope top-opps defaulting, and comma-separated territory overrides

Customer vPower query validation:

```bash
ENV_FILE=mvp/.env.secure VALIDATE_USER_UPN=<seller-upn> \
  bash mvp/infra/scripts/validate-customer-vpower-query.sh
```

- this validates email -> territories, scoped-account rows, and top-opps source
  eligibility directly against the configured customer Databricks workspace

Optional operator overrides:

- if your tenant uses a different Azure OpenAI quota profile, set
  `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_MODEL`,
  `AZURE_OPENAI_MODEL_NAME`, `AZURE_OPENAI_MODEL_VERSION`, and
  `AZURE_OPENAI_DEPLOYMENT_CAPACITY` in the operator-owned `*.inputs` file
  before you run the Azure bootstrap
- open mode now also derives an environment-specific Entra app-registration
  prefix from `INFRA_NAME_PREFIX`, which avoids collisions with older
  `daily-account-planner-*` apps in the same tenant during reruns

Secure-mode ACR note:

- the secure operator path intentionally leaves Azure Container Registry public
  network access enabled so `az acr build` can run from Microsoft-managed build
  agents
- Databricks, Container Apps, private endpoints, and DNS remain on the secure
  network path; the public ACR exception exists only for the image-build step

Legacy entrypoints remain available for debugging:

```bash
bash mvp/infra/scripts/deploy-foundation.sh open
bash mvp/infra/scripts/deploy-foundation.sh secure
bash mvp/infra/scripts/deploy-stack.sh open
bash mvp/infra/scripts/deploy-stack.sh secure
```

The canonical secure path is now:

1. fill [`mvp/.env.secure.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.inputs)
2. run `bootstrap-azure-demo.sh secure`
3. run `bootstrap-m365-demo.sh secure`

Secure app registration details:

- `setup-custom-engine-app-registrations.sh` respects `DEPLOYMENT_MODE=secure`
  and defaults to `.env.secure` when `ENV_FILE` is not overridden
- secure mode uses the `daily-account-planner-secure` app name prefix unless an
  explicit `APP_NAME_PREFIX` is provided
- the operator bootstrap now treats missing admin consent as a blocking failure
- generated planner and bot IDs, secrets, scopes, and expected audiences are
  written back into `ENV_FILE`; do not commit `.env.secure`
- generated app IDs / object IDs are persisted in the runtime env and preferred
  on reruns so the bootstrap does not accidentally bind to a different same-name
  app in a customer tenant

Databricks warehouse bootstrap details:

- open mode and local seed flows can auto-create a starter SQL warehouse when
  the workspace does not already have one
- open mode also auto-detects the workspace catalog exposed by the Databricks
  SQL warehouse and reuses it when the fresh workspace does not support the old
  `veeam_demo` catalog bootstrap path
- secure mode leaves existing customer workspaces untouched by default and only
  runs mock seeding plus Databricks access bootstrap when
  `ENABLE_MOCK_DATABRICKS_ENVIRONMENT` is explicitly enabled
- set `DATABRICKS_WAREHOUSE_ID` to pin a specific warehouse, or
  `DATABRICKS_AUTO_CREATE_WAREHOUSE=false` to force the operator to provide one

Canonical secure repeatability flow:

```bash
bash mvp/infra/scripts/destroy-stack.sh secure
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

Wait for the target resource group to be fully deleted before rerunning the
secure bootstrap.
