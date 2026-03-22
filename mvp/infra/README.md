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
- SQL `CREATE USER` is not used for the secure bootstrap path
- the secure seed path no longer relies on temporarily enabling Databricks
  public network access
- secure bootstrap validation checks seeded base tables, entitlements, and
  secure-view existence; seller-scoped secure views are validated later through
  delegated seller tests, not through the bootstrap identity

Canonical secure repeatability flow:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/destroy-stack.sh secure
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-stack.sh secure
ENV_FILE=mvp/.env.secure bash mvp/scripts/publish-m365-app-package-graph.sh
ENV_FILE=mvp/.env.secure bash mvp/scripts/install-m365-app-for-self-graph.sh
```
