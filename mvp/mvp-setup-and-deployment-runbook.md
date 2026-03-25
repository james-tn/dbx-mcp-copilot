# MCP-Dev Setup And Deployment Runbook

## Scope

This runbook is for the `mcp-dev` branch only.

Use it when we want:

- fast local debug with planner + MCP + Databricks-app-backed tools
- a side-by-side Azure environment that does not collide with the older deployed stack
- the new MCP-first runtime path

## 1. Safe Repo Initialization

Before the bootstrap rewrites active runtime files, it now creates one-time
backups:

- `.env.pre-mcpdev`
- `.env.secure.pre-mcpdev`
- `.env.inputs.pre-mcpdev`
- `.env.secure.inputs.pre-mcpdev`

The active runtime files remain:

- `.env`
- `.env.secure`

## 2. Local/Open Inner Loop

The recommended inner loop is local compose plus the dev UI.

```bash
cd mvp
docker compose up --build
```

Services:

- planner: `http://localhost:8080`
- MCP: `http://localhost:8001/mcp`
- Databricks app demo: `http://localhost:8002`
- wrapper: `http://localhost:3978`
- dev UI: `http://localhost:8010`

What to verify locally:

1. Open the dev UI.
2. Create a session.
3. Send `hello`.
4. Send `where should I focus?`
5. Send `give me my morning briefing`.
6. Verify SSE events and final reply behavior.

## 3. Open-Mode Azure Bootstrap

Prepare inputs:

```bash
cp mvp/.env.inputs.example mvp/.env.inputs
```

Fill at least:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `SELLER_A_UPN`
- `SELLER_B_UPN`

Then run:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh open
```

The default side-by-side open environment is:

- resource group: `rg-daily-account-planner-mcpdev`
- prefix: `dailyacctplannermcpdev`

After Azure is ready, run:

```bash
bash mvp/infra/scripts/bootstrap-m365-demo.sh open
```

## 4. Secure-Mode Azure Bootstrap

Prepare inputs:

```bash
cp mvp/.env.secure.inputs.example mvp/.env.secure.inputs
```

Fill at least:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `SELLER_A_UPN`
- `SELLER_B_UPN`

Then run:

```bash
bash mvp/infra/scripts/bootstrap-azure-demo.sh secure
```

The default side-by-side secure environment is:

- resource group: `rg-daily-account-planner-mcpdev-secure`
- prefix: `dailyacctplannermcpdevsec`

After Azure is ready, run:

```bash
bash mvp/infra/scripts/bootstrap-m365-demo.sh secure
```

## 5. Databricks App Deployment Modes

### Recommended fast path

If a Databricks App URL already exists, set:

- `TOP_OPPORTUNITIES_APP_BASE_URL`
- `TOP_OPPORTUNITIES_APP_DEPLOY_MODE=external_url`

Then the bootstrap reuses that URL instead of requiring local Databricks CLI
deployment.

### Direct CLI path

If `databricks` CLI is installed and configured, the bootstrap can call:

- `mvp/infra/scripts/deploy-top-opportunities-app.sh`

That script stages:

- `top_opportunities_app/`
- `shared/`
- a rendered `.env`

and then deploys the Databricks App from that self-contained staging folder.

## 6. Manual Low-Level Entry Points

When debugging a partial failure, use the lower-level scripts directly:

```bash
bash mvp/infra/scripts/deploy-top-opportunities-app.sh
bash mvp/infra/scripts/deploy-mcp-server.sh
bash mvp/infra/scripts/deploy-planner-api.sh
bash mvp/infra/scripts/deploy-m365-wrapper.sh
bash mvp/infra/scripts/validate-top-opportunities-app.sh
bash mvp/infra/scripts/validate-mcp-service-e2e.sh
```

## 7. Auth Expectations

### Planner

Required steady-state hosted vars:

- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `MCP_BASE_URL`

`PLANNER_API_CLIENT_SECRET` is now optional and transitional.

### MCP

Required hosted vars:

- `MCP_CLIENT_ID`
- `MCP_EXPECTED_AUDIENCE`
- `TOP_OPPORTUNITIES_APP_BASE_URL`
- `DATABRICKS_HOST`

Preferred hosted OBO path:

- `MCP_MANAGED_IDENTITY_CLIENT_ID`

Transitional fallback:

- `MCP_CLIENT_SECRET`

### Wrapper

Recommended hosted mode:

- `BOT_AUTH_TYPE=user_managed_identity`
- `BOT_MANAGED_IDENTITY_CLIENT_ID`
- `BOT_MANAGED_IDENTITY_RESOURCE_ID`

Transitional hosted mode:

- `BOT_AUTH_TYPE=client_secret`
- `BOT_APP_PASSWORD`

## 8. Known Transitional Behavior

- Wrapper runtime and ACA deploy path now prefer explicit managed-identity
  settings, but bot OAuth connection automation still skips managed-identity
  mode because the current CLI command requires a client secret.
- Planner deployment is MCP-first at runtime, but the secure seed/bootstrap
  path still lives in the planner deployment script.
- `dev_ui` is intentionally local-only and is not part of Azure bootstrap.

## 9. Smoke Checklist

After deploy, validate:

1. `hello`
2. `where should I focus?`
3. `give me my morning briefing`
4. a same-session follow-up
5. a long-running turn that crosses the delayed-ack threshold
6. wrapper recovery from a transient send failure without poisoning the session
