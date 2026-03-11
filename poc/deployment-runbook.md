# Deployment Runbook (POC)

## 1. Prerequisites

- Azure CLI authenticated to target tenant/subscription.
- Databricks workspace and SQL Warehouse already provisioned.
- Azure OpenAI resource/deployment already provisioned.
- PowerShell 7+.

## 2. Configure environment

Copy `.env.example` to `.env` and populate all values.

## 3. Provision Entra app registrations

Run:

```powershell
pwsh ./scripts/setup-app-registrations.ps1 -ConfigFile ./.env
```

## 4. Provision Azure infrastructure

Run:

```powershell
pwsh ./scripts/deploy-poc.ps1 -ConfigFile ./.env
```

This deploys:

- Auth Broker Container App
- Revenue MCP Container App
- Container Apps environment + monitoring
- Premium Databricks workspace (UC-ready baseline)
- Databricks Access Connector (system-assigned identity)

### 4.1 Unity Catalog enablement after infra deploy

The Azure deployment creates a UC-ready Databricks foundation, but Unity Catalog requires Databricks account-level configuration:

1. Create or identify a Unity Catalog metastore in the Databricks account console.
2. Assign the metastore to the new workspace.
3. Create/start a UC-enabled SQL warehouse in that workspace.
4. Update `.env` values:
	- `DATABRICKS_SERVER_HOSTNAME` = workspace URL without protocol
	- `DATABRICKS_HTTP_PATH` = UC-enabled SQL warehouse path
	- `MCP_ALLOWED_SCHEMA` = your UC schema (example `ri_poc.revenue`)

Validate UC readiness:

```powershell
pwsh ./scripts/verify-uc-readiness.ps1 -ConfigFile ./.env
```

### 4.2 Automated UC workspace + warehouse + seed flow

You can run the consolidated script below to:

- create Premium Databricks workspace
- create Access Connector
- create SQL warehouse
- verify UC function support
- seed UC dataset when UC is ready

```powershell
pwsh ./scripts/provision-uc-databricks.ps1 -ConfigFile ./.env
```

If the script exits with UC not enabled, complete metastore assignment in Databricks account console, then rerun.

## 5. Build and deploy service images

Option A (recommended for quick start):

```powershell
pwsh ./scripts/build-and-push-images.ps1 -AcrName <your-acr-name> -Tag v1
```

Then set `BROKER_IMAGE` and `MCP_IMAGE` in `.env`.

Option B: use your preferred container registry flow to build and push images for:

- `services/auth-broker`
- `services/revenue-mcp`

Then update Container Apps image settings via Azure CLI.

## 6. Seed Databricks data

Execute SQL in `scripts/seed-databricks-revenue.sql` using Databricks SQL editor or CLI.

## 7. Execute end-to-end tests

Run:

```powershell
pwsh ./scripts/run-e2e-tests.ps1 -ConfigFile ./.env
```

## 8. Validate acceptance criteria

- Regional row-level filtering works for NA and EMEA users.
- Broker OBO issuance succeeds, tokens are reused in cache, and downstream refresh/retry is transparent.
- Revenue metrics match expected values in test assertions.

## 9. VS Code MCP interoperability notes

Use these checks if MCP tools list but invocation fails, or if OAuth/discovery behavior looks inconsistent.

### 9.1 Canonical MCP and discovery endpoints

- MCP endpoint: `https://<revenue-mcp-app>/mcp`
- Protected resource metadata endpoint: `https://<revenue-mcp-app>/.well-known/oauth-protected-resource`

The discovery URL should be rooted on the service host. Do not append `/mcp` to the discovery URL itself.

### 9.2 Common symptoms and fixes

- `500 Internal Server Error` on MCP requests:
	- Ensure the parent FastAPI app is created with the FastMCP lifespan (`lifespan=mcp_http_app.lifespan`).
- `400 Missing session ID` during streamable HTTP calls:
	- Ensure MCP HTTP app is configured for stateless operation (`stateless_http=True`).
- `POST` followed by redirect and `405 Method Not Allowed`:
	- Ensure `/mcp` is treated as canonical and avoid redirect chains that change method semantics.

### 9.3 Broker audience compatibility

`BROKER_EXPECTED_AUDIENCE` supports comma-separated audience values. The broker also accepts equivalent forms for app ID values (for example raw app ID and `api://<app-id>`). This avoids false `401` rejections when different clients emit different audience formats.

Example:

```env
BROKER_EXPECTED_AUDIENCE=api://<broker-app-id>,<broker-app-id>
```

### 9.4 Quick endpoint probes

```powershell
curl https://<revenue-mcp-app>/.well-known/oauth-protected-resource
curl -X POST https://<revenue-mcp-app>/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expected behavior:

- Without a bearer token, MCP returns `401` with a `WWW-Authenticate` challenge.
- With an expired or near-expiry bearer token, MCP returns `401` with a `WWW-Authenticate` challenge so the client can silently reacquire and retry.
- With a valid token, MCP `tools/list` and `tools/call` return `200`.
