# Daily Account Planner Infra

`mvp/infra` contains the deployment and validation surface for the `mcp-dev`
architecture.

## What This Branch Deploys

Hosted runtime components:

- planner API
- MCP server
- M365 wrapper
- Databricks App for `get_top_opportunities`

Local-only component:

- `dev_ui`

## Main Bootstrap Entry Points

Azure:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh open
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
```

M365 publish/install:

```bash
bash mvp/infra/scripts/bootstrap-m365-demo.sh open
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

## New Low-Level Scripts

### App and service deploy

- `deploy-top-opportunities-app.sh`
- `deploy-mcp-server.sh`
- `deploy-planner-api.sh`
- `deploy-m365-wrapper.sh`

### Validation

- `validate-top-opportunities-app.sh`
- `validate-mcp-service-e2e.sh`
- `validate-databricks-direct-query.sh`
- `validate-planner-service-e2e.sh`

### Shared/bootstrap

- `setup-custom-engine-app-registrations.sh`
- `create-azure-bot-resource.sh`
- `setup-bot-oauth-connection.sh`
- `deploy-foundation.sh`
- `deploy-stack.sh`

## Naming Defaults

Open mode defaults:

- resource group: `rg-daily-account-planner-mcpdev`
- prefix: `dailyacctplannermcpdev`

Secure mode defaults:

- resource group: `rg-daily-account-planner-mcpdev-secure`
- prefix: `dailyacctplannermcpdevsec`

## Important Branch Rules

- Local `.env` files are still the active runtime files.
- Bootstrap now protects pre-branch values by creating `*.pre-mcpdev` backups.
- Planner runtime is MCP-first and no longer depends on planner-owned Databricks auth.
- MCP is the enterprise trust boundary.
- Wrapper managed-identity runtime support is implemented, but bot OAuth
  connection automation still has a managed-identity gap.

## Recommended Operator Flow

1. Prepare `mvp/.env.inputs` or `mvp/.env.secure.inputs`.
2. Run `bootstrap-azure-demo.sh`.
3. Run `bootstrap-m365-demo.sh`.
4. Validate:
   - Top Opportunities app
   - MCP
   - planner
   - wrapper / bot path
