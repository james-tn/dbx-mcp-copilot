# Daily Account Planner Architecture

## Status

This document is the implementation source of truth for the `mcp-dev` branch.

The current branch preserves the old deployed environment, but local `.env` files
now default to a side-by-side `mcp-dev` environment after automatic one-time
backup to:

- `.env.pre-mcpdev`
- `.env.secure.pre-mcpdev`
- `.env.inputs.pre-mcpdev`
- `.env.secure.inputs.pre-mcpdev`

## Goals

- Make MCP the only enterprise tool boundary for planner agents.
- Remove planner-owned Databricks auth and source-specific execution logic.
- Keep open/local development fast with public endpoints and simple compose.
- Keep secure/private deployment as the next validation lane.
- Reduce steady-state hosted secrets:
  - planner runtime should not require `PLANNER_API_CLIENT_SECRET`
  - wrapper runtime should not require `BOT_APP_PASSWORD`
  - MCP should prefer managed-identity-backed OBO over `MCP_CLIENT_SECRET`

## Runtime Topology

```text
Seller
  -> M365 / Teams / Copilot
  -> M365 wrapper
  -> planner API
  -> agent handoff workflow
  -> MCP server
  -> backend services
       - Databricks SQL
       - Databricks App for get_top_opportunities
       - EDGAR lookup
```

## Component Responsibilities

### Planner service

The planner owns only:

- planner API auth
- session state
- seller turn history
- top-level routing between `AccountPulse` and `NextMove`
- seller-facing response shaping
- MCP connectivity
- local/dev SSE streaming for the end-user experience

The planner does not own:

- Databricks OBO
- Databricks SQL execution
- Databricks App routing
- EDGAR transport logic
- backend-specific query normalization

### MCP server

The MCP server is the enterprise trust boundary.

It owns:

- tool definitions exposed to the planner through MCP
- Databricks delegated auth and OBO
- Databricks SQL execution
- routing of `get_top_opportunities` to the Databricks App backend
- EDGAR integration
- backend normalization into stable seller-tool payload shapes

Current stable tool contract:

- `get_scoped_accounts`
- `lookup_rep`
- `get_top_opportunities`
- `get_account_contacts`
- `edgar_lookup`

### Databricks App

`get_top_opportunities` is intentionally app-backed on this branch to
demonstrate backend variety.

The app:

- validates the incoming bearer token
- reuses the shared enterprise auth and Databricks backend code
- returns the same payload shape that MCP expects

The app does not import MCP-specific service modules anymore.

### M365 wrapper

The wrapper is intentionally thin.

It owns:

- Bot / M365 channel ingress
- sign-in and planner token acquisition
- fast-turn vs delayed-ack behavior
- planner API forwarding
- final channel delivery

The wrapper now defaults to the planner SSE endpoint even when it finally sends
one buffered answer back to the channel.

## Local/Open Development Model

The primary inner loop for this branch is local/open development.

The first-class local stack is:

- `planner-service`
- `mcp-server`
- `top-opportunities-app`
- `m365-wrapper`
- `dev-ui`

Use:

```bash
cd mvp
docker compose up --build
```

The preferred developer UI is `dev_ui`, which uses planner SSE and shows:

- `ack`
- `tool_call_started`
- `tool_call_progress`
- `text_delta`
- `final`
- `error`

Open/local development intentionally allows:

- public planner and MCP endpoints
- unsecured/private-network-free local Databricks connectivity
- direct or pre-provisioned Databricks App hookup

## Agent Tool Loading

Planner agents do not define enterprise tools locally.

Instead:

- the planner uses the built-in Agent Framework MCP tool client
- tool schemas are loaded from the MCP server
- source-specific tool logic stays out of planner code

This branch keeps the planner developer experience intentionally simple:

- authenticate to planner
- connect to MCP
- call enterprise tools through the MCP contract

## Auth Model

### Planner inbound auth

- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`

The planner runtime no longer needs `PLANNER_API_CLIENT_SECRET` in steady state.

### MCP inbound and downstream auth

The planner forwards the authenticated user bearer token to MCP.

MCP validates the inbound token against `MCP_EXPECTED_AUDIENCE`.
On this branch, that audience may intentionally be the same as the planner API
audience if the planner is forwarding the original planner-bound user token.

For downstream Databricks delegated auth, MCP uses:

- `MCP_CLIENT_ID`
- optional `MCP_CLIENT_SECRET`
- preferred `MCP_MANAGED_IDENTITY_CLIENT_ID`

The explicit MCP identity now lives in MCP config instead of falling back to
planner identity variables.

### Wrapper auth

The wrapper runtime supports:

- `BOT_AUTH_TYPE=client_secret`
- `BOT_AUTH_TYPE=user_managed_identity`
- `BOT_AUTH_TYPE=system_managed_identity`

The default branch direction is managed identity, with `BOT_APP_PASSWORD`
treated as transitional.

## Shared Backend Package

Shared enterprise backend code now lives under `mvp/shared/`:

- `shared/enterprise_auth.py`
- `shared/databricks_sql.py`
- `shared/databricks_network.py`
- `shared/enterprise_tool_backend.py`

These modules are the shared source of truth for:

- request auth and request identity binding
- Databricks SQL access
- backend payload shaping for enterprise tools

Compatibility wrappers still exist under `mvp/mcp_server/` for the current test
and script surface, but the backend implementation no longer belongs there.

## Deployment Shape

Hosted open/secure deployment now targets four runtime components:

1. planner image
2. wrapper image
3. MCP image
4. Databricks App deployment for top opportunities

`dev_ui` remains local-only.

Default side-by-side names now use the `mcp-dev` prefixes:

- open resource group: `rg-daily-account-planner-mcpdev`
- secure resource group: `rg-daily-account-planner-mcpdev-secure`
- open prefix: `dailyacctplannermcpdev`
- secure prefix: `dailyacctplannermcpdevsec`

## Bootstrap Order

The intended bootstrap order on this branch is:

1. foundation
2. app registrations / identities
3. planner image build
4. wrapper image build
5. MCP image build
6. Databricks App deploy
7. MCP deploy
8. planner deploy
9. wrapper deploy
10. Azure Bot resource
11. bot OAuth / auth wiring
12. M365 publish / install

## Current Transitional Notes

- The wrapper runtime supports managed identity, but Azure Bot OAuth connection
  automation still depends on the current `az bot authsetting create` command
  surface. In managed-identity mode, the script now skips destructive secret
  wiring instead of forcing a secret path.
- `deploy-top-opportunities-app.sh` supports both:
  - direct Databricks CLI deployment
  - pre-provisioned external URL hookup through `TOP_OPPORTUNITIES_APP_BASE_URL`
- Secure bootstrap still carries some Databricks bootstrap and seed logic in the
  planner deploy script because the private seed job is not yet fully split into
  a separate provisioning surface.

## Acceptance Focus

The branch should be considered healthy when these flows work:

- `hello`
- `where should I focus?`
- `give me my morning briefing`
- same-session follow-up after a completed turn
- long-running briefing with delayed ack and successful final delivery
- local `dev_ui` streaming
