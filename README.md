# Daily Account Planner MVP

This repo is currently centered on the Daily Account Planner MVP architecture: a stateful
planner service in Azure Container Apps, a thin Microsoft 365 wrapper for
Custom Engine exposure, delegated Databricks access, and Microsoft Agent
Framework orchestration for `DailyAccountPlanner`, `AccountPulse`, and
`NextMove`.

## Start Here

- Architecture: [mvp/daily-account-planner-architecture.md](mvp/daily-account-planner-architecture.md)
- MVP setup and deployment runbook: [mvp/mvp-setup-and-deployment-runbook.md](mvp/mvp-setup-and-deployment-runbook.md)
- Open input env contract: `mvp/.env.inputs`
- Secure input env contract: `mvp/.env.secure.inputs`
- Generated runtime envs: `mvp/.env` and `mvp/.env.secure`
- Infra entrypoints: [mvp/infra/README.md](mvp/infra/README.md)
- General M365 integration guidance: [docs/m365-agentic-service-developer-guide.md](docs/m365-agentic-service-developer-guide.md)

## MVP Layout

- [mvp/agents](mvp/agents): planner API, orchestration, Databricks query layer, tests, and Account Pulse benchmarking support
- [mvp/m365_wrapper](mvp/m365_wrapper): thin M365 Custom Engine wrapper that forwards authenticated turns to the planner
- [mvp/infra](mvp/infra): infra foundation, app-registration, deploy, seed, and validation assets
- [mvp/scripts](mvp/scripts): packaging, publishing, benchmarking, and local channel helpers
- [mvp/appPackage](mvp/appPackage): Microsoft 365 app manifest template and build output

## Reusable M365 Wrapper Pattern

Most of the wrapper is reusable for other agentic services that need to surface
in Microsoft 365 Copilot.

Usually reusable as-is:

- Bot and Agents SDK bootstrap
- auth handler wiring for agentic and connector traffic
- conversation-to-session mapping
- delayed long-running acknowledgement behavior
- busy-turn rejection per conversation
- seller-safe auth and temporary-unavailable messaging
- Custom Engine `/api/messages` hosting shape

Usually swapped per service:

- downstream service client in `mvp/m365_wrapper/planner_client.py`
- wrapper config names and scopes
- response extraction and any protocol translation between Bot activities and the
  target service API
- service-specific fallback messages or telemetry labels

Important current note:

- the wrapper carries a local long-running compatibility bridge for the current
  Python Microsoft Agents SDK because the SDK's built-in proactive path does not
  match the adapter contract we observed in live testing

## Operator Quick Start

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

Before running the bootstrap scripts:

- fill `mvp/.env.secure.inputs` or `mvp/.env.inputs`
- run `az login`
- treat `mvp/.env` and `mvp/.env.secure` as generated runtime state, not hand-edited operator files

Important auth note:

- the Azure bootstrap attempts admin consent automatically when the operator has permission
- the critical delegated grants are:
  - Planner API -> Azure Databricks `user_impersonation`
  - Wrapper/channel app -> Planner API `access_as_user`
- if those grants are missing, Teams sign-in or scoped Databricks access can fail even when infra deployment succeeded

## Advanced / Recovery Flows

Create app registrations and bot auth wiring:

```bash
bash mvp/infra/scripts/setup-custom-engine-app-registrations.sh
```

Seed Databricks and validate direct planner data access:

```bash
bash mvp/infra/scripts/seed-databricks-ri.sh
bash mvp/infra/scripts/validate-databricks-direct-query.sh
```

Deploy the planner and wrapper:

```bash
bash mvp/infra/scripts/deploy-foundation.sh open
bash mvp/infra/scripts/deploy-planner-api.sh
bash mvp/infra/scripts/deploy-m365-wrapper.sh
bash mvp/infra/scripts/create-azure-bot-resource.sh
bash mvp/infra/scripts/setup-bot-oauth-connection.sh
```

Validate locally or against deployed services:

```bash
bash mvp/infra/scripts/validate-planner-service-e2e.sh
bash mvp/infra/scripts/validate-network.sh
bash mvp/infra/scripts/validate-seller-access.sh
bash mvp/scripts/validate-wrapper-playground.sh
bash mvp/scripts/benchmark-account-pulse.sh
```

Build and publish the Microsoft 365 app package manually:

```bash
bash mvp/scripts/build-m365-app-package.sh
bash mvp/scripts/publish-m365-app-package-graph.sh
bash mvp/scripts/install-m365-app-for-self-graph.sh
```

## Notes

- The MVP currently uses in-memory planner sessions, so the planner service
  stays pinned to one replica.
- The operator-first path is the runbook plus the two bootstrap scripts; the
  lower-level scripts remain available for debugging and recovery.
- The wrapper is intentionally thin: it handles Microsoft 365/Bot protocol and
  forwards a service-scoped delegated token to the planner.
- The planner service remains the data trust boundary and validates the inbound
  service token before downstream OBO.
