# GitHub Actions CI/CD Validation And Operations

## Purpose

This document explains how to validate that the CI/CD setup works after the
customer team finishes wiring GitHub Actions, Azure OIDC, environment variables,
and runtime access.

Use this file when you need:

- an end-to-end validation drill
- suggested low-risk changes to exercise different workflow paths
- rollout evidence to capture for customer handoff
- operational guardrails for production promotion

If you need the conceptual model, use [`cicd-overview.md`](cicd-overview.md).

If you need the implementation steps, use
[`cicd-setup-guide.md`](cicd-setup-guide.md).

## Why CI/CD Uses Ephemeral Env Files

Human operators often use `mvp/.env` or `mvp/.env.secure` directly. That is
fine for local or manual operations.

CI/CD should behave differently:

- it should not rewrite tracked env templates
- it should not commit runtime mutations back to the repo
- it should render a temporary env file inside the runner workspace
- it should populate that env file from:
  - release metadata
  - GitHub Environment variables
  - GitHub Environment secrets
  - optionally Azure Key Vault

That is what
[`scripts/ci-render-runtime-env.sh`](scripts/ci-render-runtime-env.sh) does.

## Supported Customer Scenario

This validation guide assumes the customer already has:

- an Azure subscription and resource group
- an existing customer Databricks workspace
- existing customer data sources
- working manual deployment knowledge from a previous version of the repo
- a previous `mvp/.env.secure` or equivalent manual deployment configuration

The goal is to automate planner and wrapper delivery without re-bootstraping
the customer's live platform on every release.

## Most Common Customer Case: Existing Databricks, No Mock In Production

For most customer tenants, the correct production model is:

- planner and wrapper deploy into existing Azure resources
- planner points to the customer's already-existing Databricks workspace
- planner reads customer-owned AIQ and vPower-backed sources
- no mock Databricks in production
- no seeding in production
- no schema mutation in production

For `integration`, the customer has two reasonable options:

1. recommended: use an existing non-production customer Databricks workspace
   and populate it with the data needed for validation
2. acceptable when the customer intentionally wants extra scaffolding: use a
   dedicated mock or seeded Databricks environment

## Run An End-To-End Drill

Recommended first drill:

1. create a small test change on `feature/*`
2. merge into `dev`
3. confirm `ci.yml` passes
4. promote to `integration`
5. confirm `deploy-integration.yml` passes
6. promote to `main`
7. confirm the `production` environment pauses for approval
8. approve it
9. confirm `deploy-production.yml` completes without rebuilding artifacts

## Post-Setup Validation Matrix

After the team finishes GitHub and Azure setup, do not rely on a single PR to
prove everything. Run a short sequence of deliberately small changes so the
customer can see which workflow paths fire and which ones do not.

### Drill 1: Documentation-Only Change

Purpose:

- prove the repo still accepts low-risk documentation updates
- prove deploy workflows do not perform a runtime deployment for docs-only
  changes

Suggested change:

- edit one markdown file such as:
  - `README.md`
  - `mvp/infra/cicd-setup-guide.md`

Expected result:

- PR to `dev` runs `ci.yml`
- docs-only checks fast-pass where designed
- no integration runtime deploy should happen from a docs-only promotion
- no production runtime deploy should happen from a docs-only promotion

### Drill 2: Planner Runtime Change

Purpose:

- prove planner code changes run CI
- prove the integration deploy path builds or resolves release metadata and
  deploys the planner and wrapper stack correctly
- prove production promotion reuses the tested artifact

Suggested change:

- add a safe, visible non-functional change such as:
  - adjust one log line in `mvp/agents/api.py`
  - add a tiny unit test in `mvp/agents/tests/`

Expected result:

1. PR into `dev`
   - `ci.yml` runs tests, shell validation, Docker smoke, and package build
2. promote `dev` to `integration`
   - `deploy-integration.yml` runs
   - integration environment receives the new release
   - deployed validations pass
3. promote `integration` to `main`
   - `deploy-production.yml` pauses for approval
   - after approval, production deploy completes using the same release
     metadata and image refs rather than rebuilding

### Drill 3: Infra-Oriented Deploy Helper Change

Purpose:

- prove infra-adjacent script changes still run the correct guarded deployment
  path
- prove the team understands when a change is deploy-relevant

Suggested change:

- make a small safe change to one deploy or validation helper, for example:
  - add or adjust a non-sensitive log line in
    `mvp/infra/scripts/ci-validate-integration.sh`
  - add a harmless log line in `mvp/infra/scripts/deploy-planner-api.sh`

Expected result:

- PR into `dev` runs `ci.yml`
- promotion to `integration` triggers `deploy-integration.yml`
- promotion to `main` triggers `deploy-production.yml` and approval gating

### Drill 4: Direct Customer Query Validation

Purpose:

- prove the Databricks-backed customer query path works in automation when the
  customer wants that extra check

Suggested setup:

- set `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION=true`
- set `VALIDATE_USER_UPN=<real seller upn>`
- only do this on a runner that can actually reach the target Databricks
  workspace

Expected result:

- integration or production validation runs:
  - `bash mvp/infra/scripts/validate-customer-vpower-query.sh`
- the chosen seller UPN resolves territory and scoped accounts successfully

If this drill fails, the likely causes are:

- the runner cannot reach Databricks
- the selected seller UPN has no expected scope in that workspace
- Databricks grants or warehouse access are incomplete

### Drill 5: Optional Authenticated Planner E2E

Purpose:

- prove authenticated planner API chat validation if the customer explicitly
  wants it

Suggested setup:

- provide a freshly minted delegated token in `PLANNER_API_BEARER_TOKEN`
- make sure the token audience matches `PLANNER_API_SCOPE`

Expected result:

- `validate-planner-service-e2e.sh` performs:
  - health check
  - session creation
  - authenticated message turn(s)

If this token is not supplied, the workflow should still succeed with health-only
validation.

## Recommended Order For Customer Validation

1. run Drill 1 first
2. run Drill 2 next
3. run Drill 3 if the customer expects to maintain deploy helpers or IaC
4. enable Drill 4 only after the basic pipeline is already healthy
5. enable Drill 5 only if the customer specifically wants authenticated API
   smoke tests in CI/CD

## Suggested Evidence To Capture

For the customer's internal handoff, save:

- the successful `ci.yml` run URL for a docs-only PR
- the successful `deploy-integration.yml` run URL for a runtime PR
- the successful `deploy-production.yml` run URL for a promoted release
- the GitHub Environment screenshot showing production approval gating
- the integration validation output for `VALIDATE_USER_UPN` if Drill 4 is
  enabled

## First-Time Customer Cutover Checklist

- GitHub Environments created
- production approval gate configured
- OIDC identities created
- Azure RBAC assigned
- GitHub variables populated
- GitHub secrets or Key Vault secrets populated
- existing customer Azure resource names confirmed
- existing customer Databricks runtime inputs confirmed
- validation UPN confirmed
- integration drill completed
- production approval drill completed
- Teams publish path documented separately

## Operational Notes

- normal application CD should stay separate from bootstrap and foundation
  changes
- production should promote immutable image references from integration
- normal deploy workflows should not repair RBAC dynamically
- Teams publish should stay independent from Azure runtime deployment
- integration should be realistic enough to prove the customer query path

## Final Guidance

If a customer is coming from an older version of this repo and a manually
maintained `mvp/.env.secure`, keep the validation plan simple:

- first prove docs-only changes behave correctly
- then prove runtime changes build and deploy correctly
- then optionally add direct Databricks query validation
- only add authenticated planner E2E when the customer truly wants that extra
  smoke coverage
