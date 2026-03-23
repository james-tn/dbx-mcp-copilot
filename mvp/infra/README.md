# Daily Account Planner Infra

`mvp/infra` is the canonical home for infrastructure, deploy, seed, and
environment validation assets.

## Layout

- `bicep/`: shared foundation templates used by both open and secure stacks
- `databricks/`: Databricks seed SQL and security model assets
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

Databricks seed:

```bash
bash mvp/infra/scripts/seed-databricks-ri.sh
```

For secure Databricks seeding, the same entrypoint starts a private ACA Job
from inside the secure Container Apps environment. Set
`DATABRICKS_BOOTSTRAP_AUTH_MODE`, `DATABRICKS_SEED_JOB_NAME`,
`DATABRICKS_SEED_TIMEOUT_SECONDS`, `DATABRICKS_SEED_POLL_SECONDS`,
`DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID`, and
`DATABRICKS_AZURE_RESOURCE_ID` in `.env.secure`.

The canonical secure path is now:

1. fill [`mvp/.env.secure.inputs`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.inputs)
2. run `bootstrap-azure-demo.sh secure`
3. run `bootstrap-m365-demo.sh secure`

Secure seeding details:

- the secure seed job uses a non-human Azure managed identity, not a Databricks
  PAT
- secure mode defaults `DATABRICKS_SKIP_CATALOG_CREATE=true` and reuses the
  Databricks workspace catalog instead of trying to create a new managed catalog
  such as `veeam_demo`
- that default avoids secure-workspace failures where the metastore has no
  catalog storage root configured for `CREATE CATALOG`
- workspace principals are bootstrapped through Databricks SCIM/admin APIs
  before SQL grants are applied
- the bootstrap service principal is also ensured to have
  `workspace-access` and `databricks-sql-access`
- SQL `CREATE USER` is not used for the secure bootstrap path
- the secure seed path no longer relies on temporarily enabling Databricks
  public network access
- warehouse permission bootstrap is best-effort when the workspace does not
  expose a mutable warehouse-permissions endpoint; SQL execution remains the
  hard gate
- secure bootstrap validation checks seeded base tables, entitlements, and
  secure-view existence; seller-scoped secure views are validated later through
  delegated seller tests, not through the bootstrap identity

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
- secure mode uses the same warehouse bootstrap behavior from inside the private
  ACA seed job
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
