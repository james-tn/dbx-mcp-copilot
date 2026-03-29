# DAP Simulator Auth Experiment Findings

> Status: experiment notes.
> This captures the DAP simulator findings and customer-question framing, not
> the mainline runtime deployment path.

## Summary

This repo now includes a simulated DAP-compatible FastAPI app under:

- `mvp/databricks_apps/dap_simulator/`

The experiment goal was to understand the planner-to-DAP auth boundary before
asking the customer detailed questions about their real Databricks App.

## What We Implemented

The simulator mirrors the target contract:

- `GET /api/v1/healthcheck`
- `POST /api/v1/accounts/query`
- `POST /api/v1/debug/headers`

The simulator supports:

- `Authorization: Bearer ...`
- `X-Forwarded-Access-Token: ...`
- Entra audience validation when configured
- local unsigned-token bypass for development via
  `DAP_SIMULATOR_LOCAL_DEV_BYPASS_AUTH=true`

## What We Verified In This Repo

Verified by automated tests and local contract wiring:

- the planner can be configured to call a DAP-compatible endpoint for
  `get_top_opportunities`
- the planner-side DAP client can send a downstream OBO token in the
  `Authorization` header
- the planner-side DAP client can also send a forwarded token in
  `X-Forwarded-Access-Token` mode
- the simulator accepts either header path
- the simulator debug endpoint reports which header path was used
- the tool layer can preserve legacy compatibility fields such as
  `xf_score_previous_day` while sourcing ranking from DAP

## What This Experiment Did Not Prove Yet

Not yet verified against a real hosted Entra app:

- whether the customer's real DAP audience accepts planner-side OBO tokens
- whether the customer's real gateway preserves `Authorization`
- whether the customer's real gateway requires `X-Forwarded-Access-Token`
- whether the customer's tenant has additional consent, allow-list, or network
  requirements

## Current Recommendation

Default architecture remains:

- wrapper obtains planner token
- planner performs downstream OBO to DAP
- planner calls DAP using `Authorization` first

Fallback if customer deployment rejects this pattern:

- enable wrapper-assisted DAP token forwarding
- switch planner DAP client to `forward_user_token`
- use `X-Forwarded-Access-Token` if their gateway strips `Authorization`

## Customer Questions That Still Matter

These remain open after the simulator experiment:

- the real DAP base URL
- the real DAP audience and delegated scope
- whether planner-side OBO is accepted in their deployment
- whether `Authorization` survives end to end
- whether `X-Forwarded-Access-Token` is required in practice
- whether `/api/v1/debug/headers` is enabled outside development
- whether caller allow-lists or network restrictions apply

## Related Tests

Focused automated coverage now exists in:

- `mvp/agents/tests/test_customer_backend.py`
- `mvp/agents/tests/test_dap_simulator.py`
