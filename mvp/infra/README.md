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

Open or secure foundation:

```bash
bash mvp/infra/scripts/deploy-foundation.sh open
bash mvp/infra/scripts/deploy-foundation.sh secure
```

End-to-end stack:

```bash
bash mvp/infra/scripts/deploy-stack.sh open
bash mvp/infra/scripts/deploy-stack.sh secure
```

`deploy-stack.sh` now propagates both `ENV_FILE` and `DEPLOYMENT_MODE` to each
child script. For the secure path, prefer:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-stack.sh secure
```

This keeps the secure deployment bound to `.env.secure` across foundation,
registration, planner, seed, wrapper, and bot steps.

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

The canonical secure path is:

1. deploy the secure foundation
2. create app registrations
3. provision and attach the Databricks bootstrap user-assigned managed identity
4. deploy the planner app and secure ACA seed job
5. run the private seed job
6. validate delegated Databricks access and seller separation
7. deploy the wrapper, bot resource, and bot OAuth connection
8. publish and install the secure M365 package

Secure seeding details:

- the secure seed job uses a non-human Azure managed identity, not a Databricks
  PAT
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
- generated planner and bot IDs, secrets, scopes, and expected audiences are
  written back into `ENV_FILE`; do not commit `.env.secure`

Canonical secure repeatability flow:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/destroy-stack.sh secure
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-stack.sh secure
ENV_FILE=mvp/.env.secure bash mvp/scripts/publish-m365-app-package-graph.sh
ENV_FILE=mvp/.env.secure bash mvp/scripts/install-m365-app-for-self-graph.sh
```

Wait for the `veeam_poc_secured` resource group to be fully deleted before
running the secure redeploy command.
