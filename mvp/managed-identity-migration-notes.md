# Managed Identity Migration Notes

## Goal

Reduce long-lived Entra application secrets in the Daily Account Planner M365 path while moving source-specific delegated auth out of the planner and into the MCP middle tier.

## Current Secret Count

For the current hosted runtime path, the repo still has **2 Entra app secrets** in the active deployment model:

1. `BOT_APP_PASSWORD`
   Used by the M365 wrapper as the bot/channel confidential-client credential.

2. `MCP_CLIENT_SECRET`
   Used by the MCP middle tier as the confidential-client credential for delegated Databricks on-behalf-of token exchange when managed-identity assertion mode is not yet enabled.

These secrets are still generated and persisted by the current app-registration bootstrap in
[`mvp/infra/scripts/setup-custom-engine-app-registrations.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-custom-engine-app-registrations.sh#L394).

## Where Each Secret Is Used

### 1. `BOT_APP_PASSWORD`

This is the wrapper-side secret.

- The wrapper config requires it in
  [`mvp/m365_wrapper/config.py`](/mnt/c/testing/veeam/revenue_intelligence/mvp/m365_wrapper/config.py#L120).
- The wrapper deployment injects it into the Container App in
  [`mvp/infra/scripts/deploy-m365-wrapper.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/deploy-m365-wrapper.sh#L119).
- The bot OAuth connection setup also uses it in
  [`mvp/infra/scripts/setup-bot-oauth-connection.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-bot-oauth-connection.sh).

### 2. `MCP_CLIENT_SECRET`

This is now the MCP-side secret.

- The delegated Databricks OBO logic now lives in
  [`mvp/mcp_server/auth_context.py`](/mnt/c/testing/veeam/revenue_intelligence/mvp/mcp_server/auth_context.py#L84).
- The planner no longer owns Databricks token exchange. Planner-side code only validates planner tokens and calls MCP tools.
- The current env examples still surface legacy planner-secret fields for transition, but the architecture boundary has moved.

## What We Can Reduce

### Near-Term: remove 1 app secret from the wrapper path

We can realistically remove **`BOT_APP_PASSWORD`** in the next few days if we move the wrapper/bot hosting path to a managed-identity or federated-credential-based setup supported by the Bot / Agents platform.

That would reduce the active hosted path from **2 Entra app secrets -> 1 Entra app secret**.

### Also now unlocked: remove the MCP middle-tier secret

Unlike the old planner-owned OBO design, the new MCP auth code already supports a **managed-identity assertion mode**. When `MCP_CLIENT_SECRET` is empty, the MCP server can use:

- `MCP_MANAGED_IDENTITY_CLIENT_ID`
- trusted app-registration federation
- `OnBehalfOfCredential(..., client_assertion_func=...)`

to request delegated downstream tokens without a client secret.

So the architecture now supports a future reduction from **1 -> 0** without another planner-runtime redesign. The remaining work is deployment/bootstrap wiring, not a new code-path invention.

## Recommended Phasing

### Phase 1: Wrapper Secret Reduction

Target:

- Keep `BOT_APP_ID`
- Stop requiring `BOT_APP_PASSWORD` in wrapper runtime and deployment
- Move the wrapper/bot hosting path to managed identity or federated credentials

Concrete repo changes:

1. Update wrapper config to support secretless bot auth
   File:
   [`mvp/m365_wrapper/config.py`](/mnt/c/testing/veeam/revenue_intelligence/mvp/m365_wrapper/config.py)

2. Update wrapper deployment to stop requiring `BOT_APP_PASSWORD`
   File:
   [`mvp/infra/scripts/deploy-m365-wrapper.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/deploy-m365-wrapper.sh)

3. Update app-registration/bootstrap flow so it no longer creates or persists `BOT_APP_PASSWORD` for the managed-identity path
   File:
   [`mvp/infra/scripts/setup-custom-engine-app-registrations.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-custom-engine-app-registrations.sh)

4. Update any bot OAuth connection setup that still assumes a client secret
   File:
   [`mvp/infra/scripts/setup-bot-oauth-connection.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/setup-bot-oauth-connection.sh)

Expected result:

- `BOT_APP_PASSWORD` removed from runtime env files
- `BOT_APP_PASSWORD` removed from wrapper Container App secrets
- one fewer long-lived Entra app secret to rotate and protect

### Phase 2: MCP Secret Rework

Target:

- Remove `MCP_CLIENT_SECRET` from the hosted MCP runtime

This phase still touches delegated Databricks OBO, but the responsibility is now isolated to the MCP middle tier instead of the planner.

Concrete repo areas:

- MCP auth flow:
  [`mvp/mcp_server/auth_context.py`](/mnt/c/testing/veeam/revenue_intelligence/mvp/mcp_server/auth_context.py)
- MCP deployment/bootstrap wiring:
  `mvp/infra/scripts/*` on this branch still needs to stop persisting the secret and instead provision managed-identity trust for the MCP app registration
- env/runtime configuration:
  [`mvp/.env.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.example)
  and
  [`mvp/.env.secure.example`](/mnt/c/testing/veeam/revenue_intelligence/mvp/.env.secure.example)

Expected result:

- hosted runtime can move from **1 remaining middle-tier Entra app secret -> 0** once the MCP managed-identity bootstrap is wired through deployment

## Suggested Next-Days Worklist

1. Confirm the exact Bot / Agents managed-identity configuration we want to use for the wrapper path.
2. Prototype wrapper startup without `BOT_APP_PASSWORD`.
3. Update `deploy-m365-wrapper.sh` to support a managed-identity mode.
4. Update bootstrap scripts to avoid generating `BOT_APP_PASSWORD` in that mode.
5. Wire the MCP app registration trust and managed-identity settings into deployment/bootstrap.
6. Re-run secure deployment and validate Teams + Copilot sign-in, MCP tool calls, and normal messaging.

## Bottom Line

If we move forward with the managed-identity-aligned approach:

- **Definite near-term reduction:** **1 app secret**
  `BOT_APP_PASSWORD`

- **Also architecturally enabled now:** **1 more app secret**
  `MCP_CLIENT_SECRET`

So the updated realistic plan is:

- **first:** reduce from **2 -> 1** by removing `BOT_APP_PASSWORD`
- **then:** reduce from **1 -> 0** by switching the MCP OBO path to managed-identity assertion mode
