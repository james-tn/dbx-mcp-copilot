# Revenue Intelligence MVP

This repo is currently centered on the Daily Account Planner MVP: a stateful
planner service in Azure Container Apps, a thin Microsoft 365 wrapper for
Custom Engine exposure, delegated Databricks access, and Microsoft Agent
Framework orchestration for `DailyAccountPlanner`, `AccountPulse`, and
`NextMove`.

## Start Here

- Architecture: [mvp/daily-account-planner-architecture.md](mvp/daily-account-planner-architecture.md)
- MVP setup and deployment runbook: [mvp/mvp-setup-and-deployment-runbook.md](mvp/mvp-setup-and-deployment-runbook.md)
- Environment contract: [mvp/.env.example](mvp/.env.example)
- General M365 integration guidance: [docs/m365-agentic-service-developer-guide.md](docs/m365-agentic-service-developer-guide.md)

## MVP Layout

- [mvp/agents](mvp/agents): planner API, orchestration, Databricks query layer, tests, and Account Pulse benchmarking support
- [mvp/m365_wrapper](mvp/m365_wrapper): thin M365 Custom Engine wrapper that forwards authenticated turns to the planner
- [mvp/scripts](mvp/scripts): app registration, deploy, validate, seed, packaging, and publish scripts
- [mvp/appPackage](mvp/appPackage): Microsoft 365 app manifest template and build output

## Common Flows

Create app registrations and bot auth wiring:

```bash
bash mvp/scripts/setup-custom-engine-app-registrations.sh
bash mvp/scripts/setup-bot-oauth-connection.sh
```

Seed Databricks and validate direct planner data access:

```bash
bash mvp/scripts/seed-databricks-ri.sh
bash mvp/scripts/validate-databricks-direct-query.sh
```

Deploy the planner and wrapper:

```bash
bash mvp/scripts/deploy-planner-api.sh
bash mvp/scripts/deploy-m365-wrapper.sh
```

Validate locally or against deployed services:

```bash
bash mvp/scripts/validate-planner-service-e2e.sh
bash mvp/scripts/validate-wrapper-playground.sh
bash mvp/scripts/benchmark-account-pulse.sh
```

Build and publish the Microsoft 365 app package:

```bash
bash mvp/scripts/build-m365-app-package.sh
bash mvp/scripts/publish-m365-app-package-graph.sh
```

## Notes

- The MVP currently uses in-memory planner sessions, so the planner service
  stays pinned to one replica.
- The wrapper is intentionally thin: it handles Microsoft 365/Bot protocol and
  forwards a service-scoped delegated token to the planner.
- The planner service remains the data trust boundary and validates the inbound
  service token before downstream OBO.
