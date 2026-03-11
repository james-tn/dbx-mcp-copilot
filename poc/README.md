# Revenue Intelligence End-to-End POC

This folder contains an end-to-end POC implementation for:

- Copilot-facing Revenue Intelligence service (FastMCP + HTTP compatibility endpoint)
- Auth Broker performing OAuth2 OBO for Azure Databricks and broker-side SQL execution
- Azure IaC for Container Apps deployment
- Entra app registration automation scripts
- Databricks SQL seed scripts for semantic revenue data and regional security
- End-to-end validation scripts

## Components

- `infra/`: Bicep templates for Azure infrastructure.
- `services/auth-broker/`: OBO token broker service.
- `services/revenue-mcp/`: Revenue Intelligence FastMCP service with 3 expert tools:
	- `revenue_performance_expert`
	- `quota_attainment_expert`
	- `retention_margin_expert`

## Runtime flow

- Copilot presents a bearer token to the MCP endpoint.
- MCP checks for a missing or near-expiry bearer token and returns an OAuth challenge before tool execution.
- MCP generates guarded SQL and sends it to the broker.
- Broker validates the user assertion, acquires or reuses an OBO token for Databricks, retries once on downstream authorization failures, and executes the SQL itself.
- MCP receives rows only and returns tool output without handling Databricks tokens directly.
- `scripts/`: Deployment, app registration, and data seeding scripts.
- `tests/`: Unit and E2E test scripts.

## MCP exposure

- Streamable HTTP MCP endpoint is mounted at `/mcp`.
- Legacy compatibility endpoint remains at `/mcp/tools/ask_revenue_intelligence`.
- OAuth protected resource metadata is published at `/.well-known/oauth-protected-resource` (root discovery URL).

Refer to `deployment-runbook.md` for step-by-step deployment and testing.
