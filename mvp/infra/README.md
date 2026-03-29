# Daily Account Planner Infra

`mvp/infra` contains the foundation templates, deployment scripts, optional
mock-seed assets, CI/CD helpers, and validation entrypoints for this repo.

This file is the infra index. It is intentionally shorter than the operator
runbook so there is one clear place for step-by-step deployment guidance.

## Canonical Docs

- Operator deployment and troubleshooting: [`../mvp-setup-and-deployment-runbook.md`](../mvp-setup-and-deployment-runbook.md)
- Runtime architecture: [`../daily-account-planner-architecture.md`](../daily-account-planner-architecture.md)
- GitHub Actions CI/CD overview: [`cicd-overview.md`](cicd-overview.md)
- GitHub Actions setup and migration: [`cicd-setup-guide.md`](cicd-setup-guide.md)
- GitHub Actions validation and operations: [`cicd-validation-and-operations.md`](cicd-validation-and-operations.md)
- CI/CD doc index: [`github-actions-cicd-design.md`](github-actions-cicd-design.md)

## Layout

- `bicep/`: shared Azure foundation templates
- `databricks/`: optional mock Databricks seed SQL assets
- `scripts/`: bootstrap, deploy, validate, and local-dev entrypoints
- `outputs/`: local bootstrap status and deployment snapshots

## Supported Environment Model

Tracked templates:

- `mvp/.env.inputs.example`
- `mvp/.env.secure.inputs.example`
- `mvp/.env.example`
- `mvp/.env.secure.example`

Local/generated files:

- `mvp/.env.inputs`
- `mvp/.env.secure.inputs`
- `mvp/.env`
- `mvp/.env.secure`

Prefer canonical hosted-runtime names in new docs and environment wiring:

- `DATABRICKS_*`
- `TOP_OPPORTUNITIES_*`
- `CONTACTS_*`
- `SCOPE_ACCOUNTS_*`
- `SALES_TEAM_MAPPING_*`

Legacy `CUSTOMER_*` aliases still exist in some scripts for migration, but they
are not the preferred contract.

## Main Operator Paths

Recommended secure bootstrap:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

Recommended open bootstrap:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh open
bash mvp/infra/scripts/bootstrap-m365-demo.sh open
```

Existing customer Databricks, secure hosted stack:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-customer-stack.sh
```

Routine planner-only update:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-planner-only.sh
```

Routine wrapper-only update:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/build-and-deploy-wrapper-only.sh
```

Optional mock Databricks bootstrap:

```bash
ENABLE_MOCK_DATABRICKS_ENVIRONMENT=true bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
```

That mock path seeds only the foundation workspace and is never intended to
mutate an existing customer workspace.

## Local Development Paths

Sync local Python dependencies:

```bash
uv sync --project mvp --group dev
```

Start the planner API only:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/run-local-planner-api.sh
```

Start the local chat UI only:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/run-local-planner-chat.sh
```

Start the combined local stack:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/run-local-dev-stack.sh
```

Set up the local debug public client once per tenant:

```bash
ENV_FILE=mvp/.env bash mvp/infra/scripts/setup-local-debug-public-client.sh
```

The local chat UI now handles browser sign-in directly. Manual bearer-token
paste is no longer the normal local workflow.

## Validation And Diagnostics

Validate the customer vPower query path against the active secure env:

```bash
ENV_FILE=mvp/.env.secure VALIDATE_USER_UPN=<seller-upn> \
  bash mvp/infra/scripts/validate-customer-vpower-query.sh
```

- use a real seller UPN/email that should resolve in the target Databricks
  workspace
- this is a direct validation helper, not a required step for normal runtime
  deployment

Run local simulated seller-scope scenarios:

```bash
bash mvp/infra/scripts/run-local-simulated-customer-scenarios.sh
```

If you need long-running Container Apps timeout tuning, secure logging, or
split-role recovery steps, use the runbook rather than this index:

- [`../mvp-setup-and-deployment-runbook.md`](../mvp-setup-and-deployment-runbook.md)

## Script Families

Bootstrap:

- `scripts/bootstrap-azure-demo.sh`
- `scripts/bootstrap-m365-demo.sh`
- `scripts/show-bootstrap-status.sh`
- `scripts/complete-entra-admin-consent.sh`
- `scripts/complete-m365-catalog-publish.sh`

Deploy:

- `scripts/deploy-customer-stack.sh`
- `scripts/build-and-deploy-planner-only.sh`
- `scripts/build-and-deploy-wrapper-only.sh`
- `scripts/deploy-planner-api.sh`
- `scripts/deploy-m365-wrapper.sh`

Validate:

- `scripts/validate-customer-vpower-query.sh`
- `scripts/validate-planner-service-e2e.sh`
- `scripts/validate-network.sh`
- `scripts/validate-seller-access.sh`

Local development:

- `scripts/run-local-planner-api.sh`
- `scripts/run-local-planner-chat.sh`
- `scripts/run-local-dev-stack.sh`
- `scripts/setup-local-debug-public-client.sh`

CI/CD helpers:

- `scripts/ci-render-runtime-env.sh`
- `scripts/ci-deploy-stack.sh`
- `scripts/ci-validate-integration.sh`

## Operating Notes

- The default secure hosted mode assumes an existing Databricks workspace and
  existing customer data sources.
- Customer scope and territory resolution now default to built-in
  `sf_vpower_bronze` queries in planner code.
- Static scope JSON and static sales-team mapping files are no longer the normal
  hosted runtime path.
- Routine planner/wrapper delivery should not use the privileged bootstrap path.
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
