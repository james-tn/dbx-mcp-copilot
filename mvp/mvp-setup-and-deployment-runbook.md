# Daily Account Planner MVP Setup And Deployment Runbook

## Purpose

This runbook documents the full MVP operator path for the Daily Account Planner:

- local setup and service bring-up
- Databricks seed and validation
- Azure Container Apps deployment
- Microsoft 365 packaging and publish

The MVP runtime is two services:

- **planner service**: stateful planner runtime with MAF handoff, planner API
  auth validation, Databricks OBO, and direct business-query execution
- **M365 wrapper**: thin Custom Engine ingress that forwards authenticated turns

The wrapper is the Copilot-facing endpoint. The planner is the Databricks trust
boundary.

## Recommended execution order

1. fill in [`mvp/.env.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.example) as `mvp/.env`
2. create app registrations
3. seed Databricks and validate direct query access
4. bring up the planner locally and validate direct chat
5. bring up the wrapper locally and validate the forwarder path
6. deploy planner and wrapper to ACA
7. build and publish the Microsoft 365 package

## Environment contract

Use [`mvp/.env.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.example)
as the baseline contract.

For the secure side-by-side environment, start from
[`mvp/.env.secure.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.example)
instead and keep `SECURE_DEPLOYMENT=true`.

High-value required values:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_CLIENT_SECRET`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_APP_PASSWORD`
- `BOT_RESOURCE_NAME`
- `DATABRICKS_HOST`
- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`

Required MVP defaults:

- `SESSION_STORE_MODE=memory`
- `ACA_MIN_REPLICAS=1`
- `ACA_MAX_REPLICAS=1`

## 1. App registrations

Create or reuse the planner API app and wrapper/bot app:

```bash
bash mvp/infra/scripts/setup-custom-engine-app-registrations.sh
```

For the secure side-by-side path, run the same script against `.env.secure`:

```bash
ENV_FILE=mvp/.env.secure DEPLOYMENT_MODE=secure \
bash mvp/infra/scripts/setup-custom-engine-app-registrations.sh
```

The script now writes the generated values back into `ENV_FILE` and prints the
final values for verification, so secure-side app rebinds do not depend on a
manual copy step.

The script sets up:

- planner API identifier URI and `access_as_user` scope
- planner API delegated Databricks access requirement
- wrapper / bot delegated access requirement to the planner API scope
- bot SSO metadata:
  - `BOT_SSO_APP_ID`
  - `BOT_SSO_RESOURCE=api://botid-<bot-app-id>`
  - Teams trusted client preauthorization
  - Bot Framework redirect URI for sign-in

Admin consent still needs to be completed for:

1. planner API delegated access to Azure Databricks `user_impersonation`
2. wrapper/channel delegated access to planner API `access_as_user`
3. bot app delegated access to planner API `access_as_user`

Secure registration notes:

- secure mode defaults to the `daily-account-planner-secure` app name prefix
  unless you override `APP_NAME_PREFIX`
- the secure path does not automatically reuse `PLANNER_API_CLIENT_ID`; set
  `REUSE_PLANNER_API_APP_ID` explicitly only when you intentionally want to
  bind a second app package or bot identity to an existing planner API app
- `.env.secure` will contain live secrets after this step and must not be
  committed

## 2. Databricks prep

This section is the minimum one-time Databricks setup for the MVP. The planner
uses Databricks SQL Statements API against the `veeam_demo.ri_secure.*` views,
and the seed script creates the demo database objects and synthetic data needed
for local and deployed testing.

### 2.1 Local operator prerequisites

Before seeding, make sure the operator running the scripts has:

1. access to the target Databricks workspace at `DATABRICKS_HOST`
2. permission to use the SQL Statements API
3. permission to list SQL warehouses
4. permission to create schemas / tables / views in the demo area used by the
   seed script

Set these values in `.env` first:

- `DATABRICKS_HOST`
- optionally `DATABRICKS_WAREHOUSE_ID`
- optionally `DATABRICKS_PAT`

The scripts support two local auth modes:

1. **Preferred local setup path: Azure CLI token**
   - run `az login`
   - if needed, run `az account set --subscription <subscription-id>`
   - the seed script will request a Databricks bearer with:
     - resource `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`
2. **Fallback local setup path: Databricks PAT**
   - set `DATABRICKS_PAT` in `.env`
   - this is useful if local Entra-based Databricks auth is not yet ready

### 2.2 SQL warehouse setup

Create or reuse a Databricks SQL warehouse for the MVP.

Recommended checks:

1. confirm at least one warehouse exists
2. confirm at least one warehouse is in `RUNNING`, `STARTING`, or `STARTED`
   state
3. set `DATABRICKS_WAREHOUSE_ID` explicitly if you want deterministic behavior

If `DATABRICKS_WAREHOUSE_ID` is not set, the seed and validation scripts will:

1. list SQL warehouses from `GET /api/2.0/sql/warehouses`
2. pick the first running or starting warehouse
3. otherwise fall back to the first warehouse returned

### 2.3 Seed the MVP dataset

Seed the enriched MVP dataset:

```bash
bash mvp/infra/scripts/seed-databricks-ri.sh
```

What the script does:

1. loads `.env`
2. obtains a Databricks bearer from `DATABRICKS_PAT` or Azure CLI
3. resolves a warehouse if `DATABRICKS_WAREHOUSE_ID` is unset
4. executes the seed SQL file statement-by-statement through the SQL Statements
   API
5. waits for each statement to finish before continuing

Optional overrides:

- `ENV_FILE=/path/to/.env`
- `SQL_FILE=/path/to/seed-databricks-ri.sql`
- `DEPLOYMENT_MODE=secure`

In secure mode, the seed script starts a private ACA Job from inside the
secure Container Apps environment. For that path, also set:

- `DATABRICKS_BOOTSTRAP_AUTH_MODE`
- `DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_CLIENT_ID`
- `DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_PRINCIPAL_ID`
- optionally `DATABRICKS_BOOTSTRAP_MANAGED_IDENTITY_RESOURCE_ID`
- optionally `DATABRICKS_BOOTSTRAP_PRINCIPAL_NAME`
- `DATABRICKS_SEED_JOB_NAME`
- `DATABRICKS_SEED_TIMEOUT_SECONDS`
- `DATABRICKS_SEED_POLL_SECONDS`
- `DATABRICKS_AZURE_RESOURCE_ID`
- optionally `DATABRICKS_BOOTSTRAP_WAREHOUSE_ID`
- `DATABRICKS_SKIP_CATALOG_CREATE=true` when the workspace catalog is already
  provisioned by Databricks workspace setup

The secure job uses a bootstrap-only user-assigned managed identity rather than
a Databricks PAT, and the secure path no longer relies on temporarily enabling
Databricks public network access.

Secure bootstrap order:

1. authenticate from the private ACA Job with the bootstrap managed identity
2. verify or create required workspace principals through Databricks SCIM/admin
   APIs
3. ensure the bootstrap service principal exists in the workspace and has
   `workspace-access` plus `databricks-sql-access`
4. attempt SQL warehouse `CAN_USE` permission bootstrap for the managed
   identity identifiers; if the workspace exposes no mutable permissions
   endpoint, continue and let SQL execution decide effective access
5. run the SQL seed for catalog objects, base tables, entitlements, views,
   grants, and bootstrap state
6. validate seeded base tables, entitlements, and secure-view existence

Important secure-path note:

- the bootstrap identity is not a seller principal, so seller-scoped
  `session_user()` secure views are expected to be empty for that identity
- do not use bootstrap execution to assert seller-visible secure-view row
  counts
- validate seller visibility later with delegated seller tests such as
  `validate-databricks-direct-query.sh` and `validate-seller-access.sh`

Seed success criteria:

1. the script exits successfully
2. in secure mode it prints `Secure Databricks seed completed successfully via ACA Job: ...`
3. the base tables contain seeded rows:
   - `<catalog>.ri.accounts`
   - `<catalog>.ri.reps`
   - `<catalog>.ri.opportunities`
   - `<catalog>.ri.contacts`
   - `<catalog>.ri_security.user_territory_entitlements`
4. the secure views exist:
   - `<catalog>.ri_secure.accounts`
   - `<catalog>.ri_secure.reps`
   - `<catalog>.ri_secure.opportunities`
   - `<catalog>.ri_secure.contacts`
5. seller-specific row visibility is validated separately through delegated
   seller tests

### 2.4 Validate direct Databricks connectivity after seed

Validate the planner’s direct-query path before deploying the full planner:

```bash
bash mvp/infra/scripts/validate-databricks-direct-query.sh
```

If `PLANNER_API_BEARER_TOKEN` is set, the validation uses the delegated OBO
path. Otherwise it falls back to local Databricks auth already available in the
environment, such as Azure CLI or a local PAT.

Expected outcomes:

1. `SELECT current_user()` succeeds
2. secure-view row counts are returned
3. auth mode is reported as OBO, PAT, or local identity fallback
4. `accounts`, `reps`, `opportunities`, and `contacts` all appear in the output

### 2.5 Databricks readiness for the deployed planner

Before moving on to ACA deployment, confirm the following are true:

1. the planner app registration has delegated Databricks access configured
2. tenant admin consent has been granted for that Databricks delegated
   permission
3. the signed-in seller used for testing can reach Databricks through OBO
4. the seeded `veeam_demo.ri_secure.*` views are queryable by the delegated
   identity path the planner will use

For a stronger delegated-path validation, set a real seller planner token and
rerun:

```bash
PLANNER_API_BEARER_TOKEN=<planner-api-user-token> \
bash mvp/infra/scripts/validate-databricks-direct-query.sh
```

This verifies the same OBO pattern the planner service will use in production.

### 2.6 Secure repeatability destroy/rebuild flow

After the secure seed succeeds once in the current stack, validate the secure
path is repeatable with a full teardown and rebuild:

1. destroy the secure stack, secure app registrations, and secure M365 app
   publication:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/destroy-stack.sh secure
```

2. wait until the secure resource group is fully deleted before redeploying
3. redeploy the secure stack from the canonical entrypoint:

```bash
ENV_FILE=mvp/.env.secure bash mvp/infra/scripts/deploy-stack.sh secure
```

`deploy-stack.sh` now passes `ENV_FILE` and `DEPLOYMENT_MODE` through to every
child script, so the secure path stays pinned to `.env.secure` for app
registration updates, planner deployment, secure seed, wrapper deployment, and
bot setup. The app-registration step also writes any newly generated IDs,
secrets, scopes, and audiences back into `.env.secure`.

4. publish the secure M365 package:

```bash
ENV_FILE=mvp/.env.secure bash mvp/scripts/publish-m365-app-package-graph.sh
```

5. self-install the secure M365 package for the test operator:

```bash
ENV_FILE=mvp/.env.secure bash mvp/scripts/install-m365-app-for-self-graph.sh
```

6. rerun seller and end-to-end checks:
   - `bash mvp/infra/scripts/validate-databricks-direct-query.sh`
   - `bash mvp/infra/scripts/validate-seller-access.sh`
   - `bash mvp/infra/scripts/validate-planner-service-e2e.sh`

## 3. Local MVP bring-up

Use the local path before ACA deployment when you want to validate config,
planner behavior, and wrapper forwarding without waiting on Azure changes.

### 3.1 Local prerequisites

Before using the local path, make sure you have:

1. Python 3.11 available
2. Docker with Compose support
3. Azure CLI sign-in if you will use local Databricks auth via Azure CLI
4. `mvp/.env` populated with the same core values you plan to use in Azure

### 3.2 Start both services with Docker Compose

From the repo root:

```bash
cd mvp
docker compose up --build
```

Local defaults:

- planner service: `http://localhost:8080`
- wrapper: `http://localhost:3978`

Set these in `mvp/.env` for local validation if they are not already set:

- `PLANNER_API_BASE_URL=http://localhost:8080`
- `WRAPPER_BASE_URL=http://localhost:3978`
- `PLANNER_SERVICE_BASE_URL=http://planner-service:8080` for Compose runtime

### 3.3 Validate the planner locally

Run:

```bash
bash mvp/infra/scripts/validate-planner-service-e2e.sh
```

If `PLANNER_API_BEARER_TOKEN` is not set, the script only validates `/healthz`.
If it is set, the script also:

1. creates a session
2. sends a first prompt
3. sends a follow-up prompt
4. confirms session continuity

### 3.4 Validate the wrapper locally

Run:

```bash
bash mvp/scripts/validate-wrapper-playground.sh
```

This validates wrapper reachability and prints the supported Agents Playground
channel test steps. For local wrapper testing:

1. use the wrapper URL for the custom engine endpoint
2. sign in with a user who has planner API consent and Databricks access
3. verify follow-up turns stay in one conversation session

### 3.5 Benchmark Account Pulse before cutover

To compare legacy sequential and dynamic parallel Account Pulse behavior:

```bash
bash mvp/scripts/benchmark-account-pulse.sh
```

Use this before changing the default `ACCOUNT_PULSE_EXECUTION_MODE`.

## 4. Deploy planner service

Build and publish the planner image, then set `PLANNER_API_IMAGE`.

For new environments, deploy the shared infra foundation first:

```bash
bash mvp/infra/scripts/deploy-foundation.sh open
```

or:

```bash
bash mvp/infra/scripts/deploy-foundation.sh secure
```

Deploy or update the planner ACA app:

```bash
bash mvp/infra/scripts/deploy-planner-api.sh
```

For secure mode, this script also provisions or reuses the Databricks bootstrap
managed identity, attaches it to the planner app and secure ACA Job, resolves
the workspace catalog, and configures the secure seed job contract.

After deployment:

1. copy the printed FQDN into `PLANNER_API_BASE_URL`
2. also set `PLANNER_SERVICE_BASE_URL` to the same value for the wrapper
3. keep the planner replica count pinned to one

## 5. Validate planner service

If you have a planner API bearer token for the signed-in seller, set:

- `PLANNER_API_BEARER_TOKEN`

Then run:

```bash
bash mvp/infra/scripts/validate-planner-service-e2e.sh
```

Minimum checks:

1. `GET /healthz` succeeds
2. session creation succeeds
3. the first planner turn succeeds
4. a follow-up turn reuses the same session

## 6. Deploy M365 wrapper

Build and publish the wrapper image, then set `WRAPPER_IMAGE`.

Deploy or update the wrapper ACA app:

```bash
bash mvp/infra/scripts/deploy-m365-wrapper.sh
```

After deployment:

1. copy the printed FQDN into `WRAPPER_BASE_URL`
2. confirm `GET /healthz`
3. verify `PLANNER_SERVICE_BASE_URL` still points at the planner service
4. verify long-running wrapper settings remain aligned with the MVP contract:
   - `WRAPPER_FORWARD_TIMEOUT_SECONDS=300`
   - `WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS=10`
   - `WRAPPER_ENABLE_LONG_RUNNING_MESSAGES=true`
5. create or update the Azure Bot registration after the wrapper endpoint exists:

```bash
bash mvp/infra/scripts/create-azure-bot-resource.sh
```

This creates or updates the Azure Bot resource and keeps its messaging endpoint
aligned with `https://<wrapper-host>/api/messages`.

6. create or update the Azure Bot OAuth connection:

```bash
bash mvp/infra/scripts/setup-bot-oauth-connection.sh
```

This creates the Azure Bot `SERVICE_CONNECTION` AAD v2 auth setting on the bot
app itself, with:

- `clientId = BOT_APP_ID`
- `TokenExchangeUrl = BOT_SSO_RESOURCE`
- planner API delegated scope in `provider-scope-string`

This is the live sign-in path used by non-agentic Microsoft 365 Copilot and
Teams traffic.

Do not create a second OAuth client app for this step unless you are
intentionally changing the auth model. The working MVP path uses the bot app
itself as the OAuth client for the Azure Bot connection.

For secure CLI proof against the wrapper-owned debug path, also set:

- `WRAPPER_ENABLE_DEBUG_CHAT=true`
- `WRAPPER_DEBUG_ALLOWED_UPNS`

Then validate seller-specific access with:

```bash
bash mvp/infra/scripts/validate-seller-access.sh
```

If you use the wrapper debug endpoint from the Azure CLI, the signed-in user
must also consent to the wrapper audience `api://botid-<bot-app-id>/access_as_user`.
For a newly created side-by-side bot identity, that usually means one
interactive sign-in like:

```bash
az logout
az login --tenant <tenant-id> --scope api://botid-<bot-app-id>/access_as_user
```

After that, `az account get-access-token --scope api://botid-<bot-app-id>/access_as_user`
can mint the wrapper token needed by `validate-seller-access.sh`.

## 7. Wrapper preflight

Run the wrapper health and Playground preflight:

```bash
bash mvp/scripts/validate-wrapper-playground.sh
```

This confirms the wrapper is reachable and prints the local/manual channel test
steps for Agents Playground.

Important limitation:

- a raw local `curl` to `POST /api/messages` is not a full Copilot simulation
- the wrapper endpoint expects a Bot or channel-issued bearer token whose
  audience matches `BOT_APP_ID`
- use local service tests for wrapper forwarding logic, then use Agents
  Playground or Azure Bot for real channel validation

Important live-auth note:

- normal Copilot/Teams traffic uses Azure Bot `UserAuthorization`, not
  connector-token OBO
- the bot OAuth connection must exist as `SERVICE_CONNECTION`
- the bot app advertised in `webApplicationInfo` and the bot OAuth connection
  `clientId` must match
- Bot Framework sign-in invokes are expected during auth and should not produce
  a seller-visible error response

Important wrapper implementation note:

- the wrapper code is designed to be reused for other M365 agentic services
- in most cases you only replace the downstream service client and the
  activity-to-service payload translation
- the current wrapper also carries a gateway-local compatibility bridge for the
  Python Microsoft Agents SDK long-running proactive path so the original user
  message text is preserved across delayed replies

## 8. Build the Microsoft 365 app package

Build the Custom Engine app package ZIP:

```bash
WRAPPER_BASE_URL=https://<wrapper-host> \
bash mvp/scripts/build-m365-app-package.sh
```

This writes:

- `mvp/appPackage/build/manifest.json`
- `mvp/appPackage/build/color.png`
- `mvp/appPackage/build/outline.png`
- `mvp/appPackage/build/daily-account-planner-m365.zip`

The ZIP is the artifact to upload to the tenant app catalog or admin center.

The generated manifest also carries:

- `webApplicationInfo.id = BOT_SSO_APP_ID`
- `webApplicationInfo.resource = BOT_SSO_RESOURCE`
- `token.botframework.com` in `validDomains`

This is the SSO posture expected for a bot-style custom engine app package.

Current package note:

- `copilotAgents.customEngineAgents[0].functionsAs = agentOnly`
- moving to an agentic-user template later would require additional schema
  fields such as `agenticUserTemplateId`

## 9. Wire to Azure Bot and Copilot

1. Create or update the Azure Bot registration:

```bash
bash mvp/infra/scripts/create-azure-bot-resource.sh
```

2. Create or update the Azure Bot OAuth connection:

```bash
bash mvp/infra/scripts/setup-bot-oauth-connection.sh
```

3. Build and publish the custom engine package / app manifest through the tenant
   path.
4. Install the app for your test user or assign it through your tenant process.
5. Test in Microsoft 365 Copilot with a signed-in seller account.

Optional Graph publish path:

```bash
bash mvp/scripts/setup-m365-cli-publisher-app.sh
bash mvp/scripts/publish-m365-app-package-graph.sh
bash mvp/scripts/install-m365-app-for-self-graph.sh
```

For a side-by-side secure M365 app that reuses the existing secure planner API
but creates a separate bot identity, run app registration setup like this:

```bash
ENV_FILE=mvp/.env.secure \
APP_NAME_PREFIX=daily-secured-planner \
REUSE_PLANNER_API_APP_ID=$PLANNER_API_CLIENT_ID \
bash mvp/infra/scripts/setup-custom-engine-app-registrations.sh
```

The CLI publisher app uses device-code sign-in and delegated Microsoft Graph
permissions. Recommended permission set:

- `AppCatalog.ReadWrite.All` for direct org-catalog upload
- `TeamsAppInstallation.ReadWriteForUser` for CLI self-install
- `User.Read` for current-user resolution

If your tenant only grants `AppCatalog.Submit`, the upload can still be
submitted for review, but an admin approval step is still required before broad
tenant rollout.

If you previously saw:

- `The provided token is not exchangeable`
- `400 ... /api/usertoken/exchange`

re-open the Copilot/Teams chat in a new conversation after wrapper or bot OAuth
connection changes, so the channel does not reuse stale sign-in state.

Suggested prompts:

- `Give me my morning briefing`
- `Where should I focus?`
- `Draft me an email for adidas AG`

## 10. Acceptance checks

- the same Copilot conversation maps to the same planner session
- the wrapper stays stateless and orchestration-free
- secure Databricks seeding succeeds through the private ACA Job without
  temporarily enabling Databricks public access
- secure planner runtime stays on seller OBO access for seller data
- seller-specific Databricks results differ for the two test identities
- `destroy-stack.sh secure` and `deploy-stack.sh secure` can be used as the
  canonical secure repeatability path once Azure deletion fully completes
- planner auth failures return a clean sign-in / retry message
- planner data access is governed by the signed-in user context
- planner data access runs through the app-owned direct-query layer
