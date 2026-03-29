# GitHub Actions CI/CD Setup And Operations Guide

## Purpose

This document is the canonical CI/CD design and setup guide for this repository.

It is written for two audiences:

- the internal team operating this repo today
- customer teams that will copy this repo and automate deployments against an
  already-existing manually configured Azure and Databricks environment

This guide explains:

- the delivery model implemented in this repo
- what each workflow is responsible for
- how GitHub OIDC, Azure RBAC, Key Vault, and GitHub Environments fit together
- how Dev, Infra, and M365 Admin teams should collaborate
- how to migrate from an older manually maintained `mvp/.env.secure` setup into
  GitHub Actions automation

This guide complements, but does not replace, the operational notes in
[`mvp/infra/README.md`](README.md).

## Start Here: Choose Your Path

There are two very different customer setup paths in this repo.

### Path A: Customer Already Has Infrastructure

Choose this path when the customer already has:

- Azure resource group
- Container Apps environment
- planner and wrapper deployment targets
- app registrations
- existing Databricks workspace
- existing AIQ / vPower-backed data sources

This is the easier and more common enterprise customer path.

Recommended operating model:

- do not use bootstrap for routine delivery
- populate GitHub Environments directly
- use `deploy-integration.yml` and `deploy-production.yml`
- treat old `.env.secure` only as a migration inventory

### Path B: Customer Starts From Scratch

Choose this path when the customer wants this repo to stand up the environment
for the first time.

Recommended operating model:

- run `bootstrap-foundation.yml` intentionally and sparingly
- let bootstrap derive names and write generated runtime state
- after bootstrap completes, transition to normal deploy workflows for routine
  delivery

Important truth about the current implementation:

- routine deploy workflows are already GitHub-native and use ephemeral runner
  env files
- bootstrap is still more stateful, but the GitHub bootstrap workflow now uses
  temporary input/runtime files inside the runner instead of treating
  repo-local `.env*` files as the automation source of truth

## Executive Summary

The current recommended operating model for this repo is:

- developers work on `feature/*` branches
- `dev` is the shared engineering branch
- `integration` is the automatic Azure deployment branch
- `main` is the approved production branch
- GitHub Actions uses Azure OIDC instead of long-lived Azure credentials
- CI builds planner and wrapper artifacts once on `integration`
- `integration` deploys those artifacts into a secure mock environment
- `main` promotes the already-tested release into the production customer
  environment
- Teams/M365 app publish remains a separate manual admin path

For customer adoption, the most important point is this:

- **production CI/CD in this repo is designed to deploy planner and wrapper into
  an existing customer environment**
- it does **not** expect to create the customer's Databricks workspace during
  normal delivery
- it does **not** expect to seed or mutate an existing customer workspace during
  normal delivery
- it does **not** use committed `.env.secure` files as the CI/CD source of
  truth

Instead, customer teams should treat their old `.env.secure` as a migration
inventory and move the relevant values into GitHub Environment variables,
GitHub Environment secrets, and optionally Azure Key Vault.

## Repository Deployment Model

This repo has four distinct deployment concerns and they should stay separate:

1. Planner runtime deployment
2. Wrapper runtime deployment
3. Foundation/bootstrap changes
4. Teams/M365 app package publish

That separation is intentional.

Normal application delivery should only update planner and wrapper.

Bootstrap should be reserved for rare cases such as:

- new Azure resource creation
- new networking or private endpoint work
- new app registration or OIDC prerequisites
- large infra refactors

Teams/M365 catalog publish should remain a separate trust boundary because it
requires different tenant-level permissions than Azure deployment.

## Branch And Environment Model

### Branches

- `feature/*`
  - developer working branches
  - merged into `dev`
- `dev`
  - engineering integration branch
  - validates buildability and tests
- `integration`
  - secure mock deployment branch
  - validates real Azure deployment and runtime behavior
- `main`
  - production branch
  - deploys the already-tested release into the customer environment after
    approval

### GitHub Environments

- `integration`
  - secure mock Azure deployment target
- `production`
  - secure customer-target production deployment
- `teams-catalog-admin`
  - manual publish/install path for Teams/M365 package operations
- `bootstrap-foundation`
  - manual, privileged environment for bootstrap/foundation changes

### Protection Rules

- `integration`
  - normally limited to the `integration` branch
  - optional reviewer gate if your Infra team wants one
- `production`
  - limited to the `main` branch
  - required reviewers from the Infra team
  - self-review should be disabled
- `teams-catalog-admin`
  - manual workflow only
  - required reviewers from the M365 admin team
- `bootstrap-foundation`
  - manual workflow only
  - required reviewers from the Infra team

## Deployment Profiles

The repo currently supports two important secure deployment profiles.

### Secure Mock Integration

Used by `deploy-integration.yml`.

Purpose:

- prove planner and wrapper deployment in a secure Azure shape
- validate the Databricks-backed customer query path continuously
- avoid touching production customer data

Expected characteristics:

- private or secure-style Azure topology
- mock or seeded Databricks-backed data
- at least one known validation UPN
- stable integration resource group, ACR, Container Apps names, and runtime
  values

Customer copy/deployment note:

- the repo currently names the default `integration` profile "secure mock"
- that is the recommended default for this upstream repo because it gives a
  safe place to validate release behavior without touching production data
- however, a customer copying this repo does **not** have to use a mock
  Databricks workspace for integration if they already have a separate
  non-production Databricks workspace
- in that case, they can point the `integration` GitHub Environment at that
  existing non-production Databricks workspace by supplying the appropriate
  customer runtime values and by **not** enabling seed/bootstrap behavior
- what should be avoided is pointing automated integration validation at the
  live production customer workspace unless that is an explicit, accepted
  operating decision

### Secure Customer Production

Used by `deploy-production.yml`.

Purpose:

- deploy the tested planner and wrapper release into the existing customer
  environment

Expected characteristics:

- existing customer Azure resources already provisioned and approved
- existing customer Databricks workspace already provisioned and accessible
- no mock seeding
- no bootstrap/foundation mutation during normal delivery
- no schema mutation or seed mutation against the customer's live workspace

## Workflow Catalog

## `ci.yml`

Purpose:

- run unit and repo validation checks
- smoke-build planner and wrapper images locally
- build the M365 package artifact
- create release metadata for promotion

Current trigger model:

- pull requests to `dev`, `integration`, and `main`
- pushes to `dev`, `integration`, and `main`

Current jobs:

- `python-tests`
  - `uv sync --project mvp --group dev`
  - run:
    - `mvp/infra/tests`
    - `mvp/m365_wrapper/tests`
    - `mvp/dev_ui/tests`
    - `mvp/agents/tests`
- `shell-validation`
  - `bash -n` over infra and selected repo scripts
- `docker-build-smoke`
  - local planner and wrapper Docker builds
- `package-m365`
  - build the Teams/M365 package artifact with CI-safe placeholders
- `build-release-artifacts`
  - runs on push to `integration`
  - builds planner and wrapper images in ACR
  - writes `release-metadata-<sha>`

Important design point:

- CI is where the release metadata artifact is created for deployable releases
- the artifact includes planner and wrapper image references and digests
- production later reuses that metadata rather than rebuilding

## `deploy-integration.yml`

Purpose:

- automatically deploy the validated `integration` release into the secure mock
  environment

Current flow:

1. log into Azure with the integration OIDC identity
2. download `release-metadata-<sha>` produced by `ci.yml`
3. read secrets from GitHub Environment secrets and optionally Key Vault
4. render an ephemeral runtime env file
5. deploy planner and wrapper
6. run deployed validations
7. upload a redacted deployment summary artifact

Important design point:

- this workflow proves the exact release that production will later promote

## `deploy-production.yml`

Purpose:

- promote the already-tested release into the secure customer production
  environment

Current flow:

1. resolve the promoted release SHA
2. log into Azure with the production OIDC identity
3. download the matching integration release metadata artifact
4. read secrets from GitHub Environment secrets and optionally Key Vault
5. render an ephemeral production env file
6. deploy planner and wrapper
7. run production smoke validation
8. upload a redacted deployment summary artifact

Important design points:

- production should not rebuild planner or wrapper images
- production should not run bootstrap
- production should not seed Databricks
- production should pause on the GitHub `production` environment approval gate

## `build-m365-package.yml`

Purpose:

- build the Teams/M365 package artifact on demand

This workflow should remain package-only. It should not deploy Azure resources.

## `publish-teams-catalog.yml`

Purpose:

- support the admin-controlled publish/install path for the Teams/M365 package

This workflow should stay manual and separate from Azure runtime deployment.

## `bootstrap-foundation.yml`

Purpose:

- handle rare foundation/bootstrap changes with a larger blast radius

This workflow should not be part of the normal planner/wrapper release path.

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

That is what [`mvp/infra/scripts/ci-render-runtime-env.sh`](scripts/ci-render-runtime-env.sh)
does today.

## Authentication And Secret Model

## Azure OIDC

Use two separate Azure trust paths from day one:

- one OIDC identity for `integration`
- one separate OIDC identity for `production`

Recommended naming:

- `gh-dbx-mcp-copilot-integration`
- `gh-dbx-mcp-copilot-production`

Recommended security posture:

- bind each federated credential to this repo
- scope each identity only to the Azure resources it actually needs
- do not use a single broad Azure identity for every environment

## Azure RBAC

Pre-provision stable access for the OIDC identities.

Do not rely on normal deploy workflows to repair missing RBAC dynamically.

Deploy workflows should fail fast if RBAC is missing.

## GitHub Secrets Vs Key Vault

This repo supports both:

- GitHub Environment secrets
- Azure Key Vault lookup at workflow runtime

Practical guidance:

- keep non-secret settings in GitHub Environment variables
- keep sensitive values in GitHub Environment secrets or Key Vault
- if your Key Vault is private-endpoint-only and your workflow runs on a
  GitHub-hosted runner, direct Key Vault access may not work
- in that case, GitHub Environment secrets are the practical path even though
  Azure OIDC is still used for deployment authentication

Typical secrets:

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- `PLANNER_API_BEARER_TOKEN`
- optional registry password or API-key fallback values

## Teams/M365 Publish Auth

Treat Teams/M365 publish as a separate admin-controlled trust path.

Do not try to make the normal Azure OIDC deployment identity also satisfy the
Teams catalog publish/install path.

That is why this design keeps `publish-teams-catalog.yml` separate.

## Customer Adoption Guide

This section is the most important one for a customer team copying this repo.

## Supported Customer Scenario

This guide assumes the customer already has:

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

1. recommended: use a separate secure mock or non-production Databricks
   environment
2. acceptable if available: use an existing non-production customer Databricks
   workspace with the same query surface

The least desirable option is using the live production Databricks workspace as
the integration validation target. Only do that if the customer explicitly
accepts the risk and there is no lower-risk non-production environment.

## Path A Setup: Existing Infra Customer

This is the recommended path for a customer who already has their Azure and
Databricks environment.

### What They Should Do

1. copy the repo
2. create GitHub branches and protections
3. create GitHub Environments
4. create Azure OIDC identities
5. populate GitHub Environment variables and secrets
6. point `integration` at either:
   - a secure mock environment, or
   - an existing non-production customer Databricks workspace
7. point `production` at the existing production environment
8. use `deploy-integration.yml` and `deploy-production.yml` for normal delivery

### What They Should Not Do

- do not use mock Databricks in production
- do not run seeding in production
- do not rerun bootstrap as the normal release mechanism
- do not treat repo-local `.env.secure` as the source of truth for CI/CD

## Path B Setup: Customer Starting From Scratch

This path is for greenfield setup where the customer wants the repo to help
stand up the first environment.

### What They Should Do

1. copy the repo
2. create GitHub branches and protections
3. create GitHub Environments
4. create Azure OIDC identities
5. populate the bootstrap GitHub Environment with the small seed inputs
6. run `bootstrap-foundation.yml`
7. review the generated outputs and deployed resources
8. finish the M365 bootstrap path
9. after bootstrap is complete, switch to normal deploy workflows for routine
   delivery

### Important Current Limitation

For this repo today:

- bootstrap still uses generated working env files during the bootstrap flow
- normal deploy workflows do not rely on persisted tracked env files

In GitHub Actions, those bootstrap files can now live in runner temp storage.
In local/manual operator flows, they still default to `mvp/.env.inputs`,
`mvp/.env.secure.inputs`, `mvp/.env`, and `mvp/.env.secure`.

So the cleanest mental model is:

- bootstrap is a one-time or rare setup path
- deploy workflows are the steady-state path

## What The Customer Should Preserve

From the previous manual deployment, preserve the inventory of:

- Azure resource names
- app registration IDs and expected audiences
- bot app identifiers
- Databricks workspace connection values
- AIQ table/view names
- vPower catalog qualifiers if needed
- secret names and where those secrets live

Treat the old `.env.secure` as an input inventory, not as the CI/CD runtime
contract.

## Step-By-Step Customer Setup

## 1. Copy The Repo And Choose The Branch Model

Recommended branch model:

- `feature/*` -> `dev` -> `integration` -> `main`

Recommended branch protection:

- `dev`
  - require PR review
  - require `ci.yml`
- `integration`
  - require PR review
  - require `ci.yml`
- `main`
  - require PR from `integration`
  - require `ci.yml`
  - production deployment approval handled by the GitHub `production`
    environment

## 2. Inventory The Existing Manual `.env.secure`

Before configuring GitHub, freeze a copy of the existing manual secure env.

For example:

- current planner app IDs and audience
- current bot app IDs
- current Azure resource names
- current customer Databricks settings
- current table/view names
- current secrets and who owns them

Do not start by renaming everything. First map the old values into the new CI/CD
surfaces.

## Customer Setup Surface: Required Vs Generated Vs Optional

For a customer using an **existing manually provisioned Azure + Databricks
environment**, the setup is easiest to understand if variables are grouped into
three classes:

1. customer-provided setup inputs
2. values generated by bootstrap or CI/CD
3. optional validation-only values

### 1. Customer-Provided Setup Inputs

These are the values the customer normally must provide because they describe
their existing environment or existing app registrations.

For an existing customer environment, CI/CD does **not** invent these values
for them.

Typical examples:

- Azure tenant, subscription, resource group, and region
- existing ACR name
- existing Container Apps environment name
- existing planner and wrapper app names
- existing planner app registration IDs and audience/scope
- existing bot app IDs
- existing customer Databricks workspace values
- existing AIQ source names
- existing vPower catalog qualifiers when needed

### 2. Generated By Bootstrap Or CI/CD

These are not customer setup inputs and should not be managed manually in
GitHub as operator-owned values.

Typical examples:

- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`
- release metadata artifact contents
- redacted deployment summary artifacts
- ephemeral env files created inside the runner
- discovered or refreshed service base URLs written during deployment

If the customer uses a bootstrap-managed new environment instead of an existing
one, bootstrap can also derive names such as `ACR_NAME`,
`ACA_ENVIRONMENT_NAME`, `PLANNER_ACA_APP_NAME`, and `WRAPPER_ACA_APP_NAME`
from `INFRA_NAME_PREFIX`. That is a bootstrap convenience, not a normal
production CI/CD requirement.

### 3. Optional Validation-Only Values

These are helpful for automated validation, but they are not core environment
setup inputs.

Examples:

- `PLANNER_API_BEARER_TOKEN`
- `VALIDATE_USER_UPN`
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`
- `ENABLE_WRAPPER_HEALTHCHECK`
- `REQUIRE_AUTHENTICATED_E2E`

The important operator message is:

- if a value exists only to make a validation step richer, it should be treated
  as optional validation configuration, not as a mandatory customer runtime
  setup field

## 3. Create GitHub Environments

Create these GitHub Environments in the customer repo:

- `integration`
- `production`
- `teams-catalog-admin`
- `bootstrap-foundation`

Required production protection settings:

- required reviewers from the Infra team
- `prevent self-review`
- branch policy limited to `main`

Recommended integration protection settings:

- branch policy limited to `integration`
- optional reviewer gate if the customer wants tighter infra control

Practical GitHub UI steps:

1. open the copied repo in GitHub
2. go to `Settings` -> `Environments`
3. create `integration`
4. create `production`
5. create `teams-catalog-admin`
6. create `bootstrap-foundation`
7. open `production`
8. add required reviewers from the Infra team
9. enable `Prevent self-review`
10. add a deployment branch policy that allows only `main`
11. open `integration`
12. optionally add reviewers
13. add a deployment branch policy that allows only `integration`

## 4. Create Azure OIDC Identities

Create:

- one Azure OIDC identity for `integration`
- one Azure OIDC identity for `production`

For each identity:

- create the Entra app registration or user-assigned identity
- add the GitHub federated credential
- scope Azure RBAC only to the required subscription/resource-group surface

Recommended split:

- integration identity can manage the secure mock environment only
- production identity can manage the customer production Azure resources only

Practical Azure setup sequence:

1. decide whether to use Entra app registrations or user-assigned managed
   identities as the GitHub OIDC principals
2. create one principal for `integration`
3. create one principal for `production`
4. record the client ID for each principal
5. record the tenant ID
6. record the subscription ID
7. add a GitHub federated credential for the `integration` principal
8. add a GitHub federated credential for the `production` principal

Recommended federated credential binding:

- bind the `integration` Azure principal to the GitHub `integration`
  environment
- bind the `production` Azure principal to the GitHub `production`
  environment

That keeps the trust relationship aligned with the actual protected deployment
target.

Recommended subject examples:

- `repo:<owner>/<repo>:environment:integration`
- `repo:<owner>/<repo>:environment:production`

Recommended RBAC model:

- `integration` principal
  - access only to the integration resource group or integration subscription
    slice
- `production` principal
  - access only to the production resource group or production subscription
    slice

Minimum practical Azure permissions for the deploy workflows:

- permission to deploy/update the target Container Apps resources
- permission to read or configure the target Container Apps environment
- permission to queue ACR builds or otherwise push the release images used by
  the repo's CI flow
- permission to read Key Vault secrets if Key Vault lookup is used

In practice, many teams start with:

- `Contributor` on the target resource group
- a scoped role on ACR sufficient for `az acr build`
- `Key Vault Secrets User` on the target Key Vault

Then they tighten permissions later once the workflow shape is stable.

## 5. Populate GitHub Environment Variables And Secrets

Move the old `.env.secure` values into GitHub Environments.

General rule:

- identifiers, names, and URLs -> GitHub Environment variables
- passwords, client secrets, bearer tokens -> GitHub Environment secrets or
  Key Vault

Practical GitHub UI steps:

1. open repo `Settings` -> `Environments`
2. open `integration`
3. add the required environment variables
4. add the required environment secrets
5. open `production`
6. add the required environment variables
7. add the required environment secrets
8. if using Key Vault, also add the non-secret Key Vault locator values such as
   vault name and secret names

Important operating rule:

- the workflow environment values are now the CI/CD source of truth
- the old manual `.env.secure` file becomes a migration reference only

## 5A. Absolute Minimum Inputs For A Customer Using An Existing Environment

If the customer is **not** asking this repo to create a fresh environment and is
instead automating an already-existing manually provisioned secure environment,
the minimum meaningful production setup surface is:

### Required Production Variables

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `ACR_NAME`
- `ACA_ENVIRONMENT_NAME`
- `PLANNER_ACA_APP_NAME`
- `WRAPPER_ACA_APP_NAME`
- `AZURE_OPENAI_ACCOUNT_NAME`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_SSO_APP_ID`
- `BOT_SSO_RESOURCE`
- `CUSTOMER_DATABRICKS_HOST`
- `CUSTOMER_DATABRICKS_WAREHOUSE_ID`
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`

### Usually Required Depending On The Customer Environment

- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID`
  - required when the Databricks workspace/resource header must be supplied for
    Azure auth/OBO
- `CUSTOMER_DATABRICKS_OBO_SCOPE`
  - usually the default Databricks Azure scope, but keep it explicit if the
    customer wants clarity
- `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
  - needed when the vPower bronze tables are not on the workspace default
    catalog
- `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`
  - same reason as above
- `ACA_MIN_REPLICAS`
- `ACA_MAX_REPLICAS`

### Required Production Secrets

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`

### Optional Production Secrets

- `CONTAINER_REGISTRY_PASSWORD`
  - only when the registry model requires it
- `PLANNER_API_BEARER_TOKEN`
  - optional, only for richer automated smoke validation

### Optional Production Variables

- `VALIDATE_USER_UPN`
  - only needed when the customer wants automated direct validation of the
    vPower query path in CI/CD
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`
  - optional validation flag
- `WRAPPER_ENABLE_DEBUG_CHAT`
- `WRAPPER_DEBUG_ALLOWED_UPNS`
- `WRAPPER_DEBUG_EXPECTED_AUDIENCE`
- `CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH`
  - legacy/optional

## 5B. What Customers Do Not Need To Provide Manually

Customers should **not** manage these as manual GitHub setup values for normal
delivery:

- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`
- release metadata JSON fields
- temporary env files on runners
- deployment summary artifact contents

These are created by CI/CD itself.

## 5C. From-Scratch Bootstrap: How Values Get Populated

For customers starting from zero infra, values are populated in three layers.

### Layer 1: Small Operator-Owned Bootstrap Inputs

The bootstrap helper requires only a small seed input set:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`

These are the true bootstrap-owned inputs.

For the optional seeded demo path, you may also provide:

- `SELLER_A_UPN`
- `SELLER_B_UPN`

Those are mock/demo helpers only. They are not required for the normal
customer-hosted runtime path and should not be treated as core production
GitHub Environment inputs.

### Layer 2: Names Derived Automatically By Bootstrap

Bootstrap then derives defaults such as:

- `APP_NAME_PREFIX`
- `AZURE_OPENAI_ACCOUNT_NAME`
- `ACA_ENVIRONMENT_NAME`
- `PLANNER_ACA_APP_NAME`
- `WRAPPER_ACA_APP_NAME`
- `DATABRICKS_WORKSPACE_NAME`
- `KEYVAULT_NAME`
- `ACR_NAME`
- `BOT_RESOURCE_NAME`
- `M365_APP_PACKAGE_ID`

The customer can override these if they want, but they do not have to invent
them from scratch.

### Layer 3: Values Generated Or Discovered During Provisioning

After resources are created, bootstrap writes back discovered/generated values
such as:

- `DATABRICKS_HOST`
- `DATABRICKS_MANAGED_RESOURCE_GROUP`
- `AZURE_OPENAI_ENDPOINT`
- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_CLIENT_SECRET`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_APP_PASSWORD`
- `BOT_SSO_APP_ID`
- `BOT_SSO_RESOURCE`
- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`

That is why the from-scratch bootstrap path is still more stateful than the
steady-state deploy workflows.

In GitHub Actions, the bootstrap workflow writes those bootstrap inputs and
generated runtime values into runner temp files. Manual/local operator flows can
still use `mvp/.env.inputs`, `mvp/.env.secure.inputs`, `mvp/.env`, and
`mvp/.env.secure`.

## 6. Decide How Secrets Will Be Resolved

Two supported patterns:

- GitHub Environment secrets only
- GitHub Environment secrets plus Key Vault fallback

If the customer's runner cannot reach Key Vault, keep the deploy-critical
secrets directly in GitHub Environment secrets.

## 7. Configure The Existing Customer Runtime Inputs

Production automation should point at the existing manually provisioned
environment.

That means:

- existing resource group
- existing Container Apps environment
- existing planner and wrapper app names
- existing customer Databricks host and warehouse
- existing AIQ table/view names
- existing customer vPower bronze catalog qualifiers if needed

Do not enable mock seed values in production.

For customers using an existing Databricks workspace, the production workflow
should be configured with the customer runtime values and **should not** run
bootstrap, mock seed, or any foundation path against that workspace.

## 8. Configure The Integration Secure Mock Environment

Integration should be safe to deploy repeatedly.

Recommended characteristics:

- separate non-production resource group
- separate non-production ACR
- separate Container Apps environment
- separate integration Databricks workspace or seeded mock path
- a known validation UPN

If the customer does not want to use a mock Databricks environment for
integration, replace the mock path with an existing non-production Databricks
workspace and populate the integration GitHub Environment with the same class
of customer runtime inputs used in production, but pointed at the non-production
workspace instead.

This environment is where the repo proves:

- planner deploy works
- wrapper deploy works
- vPower sales-team resolution works
- scoped-account query works
- top-opps query path works

## 9. Run An End-To-End Drill

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

## Migration Guide For Older `.env.secure` Users

This repo evolved over time, and older manual secure deployments may not map
one-to-one with the current templates.

The safest migration approach is to group values by responsibility.

## A. Azure Platform And Runtime Names

These usually move directly into GitHub Environment variables with the same
name:

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`
- `ACR_NAME`
- `ACA_ENVIRONMENT_NAME`
- `PLANNER_ACA_APP_NAME`
- `WRAPPER_ACA_APP_NAME`
- `ACA_MIN_REPLICAS`
- `ACA_MAX_REPLICAS`

## B. Planner And Wrapper Auth Settings

These normally remain GitHub Environment variables:

- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_SSO_APP_ID`
- `BOT_SSO_RESOURCE`
- `WRAPPER_ENABLE_DEBUG_CHAT`
- `WRAPPER_DEBUG_ALLOWED_UPNS`
- `WRAPPER_DEBUG_EXPECTED_AUDIENCE`

These normally become secrets:

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- `PLANNER_API_BEARER_TOKEN`

## C. Existing Customer Databricks Runtime Inputs

For **customer-target production CI/CD**, the repo still expects the
`CUSTOMER_*` namespace for the customer runtime path.

That means these remain the important production inputs:

- `CUSTOMER_DATABRICKS_HOST`
- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID`
- `CUSTOMER_DATABRICKS_OBO_SCOPE`
- `CUSTOMER_DATABRICKS_WAREHOUSE_ID`
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`
- `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
- `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`

This is important because the repo now also has unprefixed `DATABRICKS_*`
values used by foundation/bootstrap/mock paths.

Use this rule:

- `CUSTOMER_DATABRICKS_*` = customer production runtime inputs
- `DATABRICKS_*` = foundation/mock/internal workspace inputs

For customers migrating an older manual secure environment, do **not** blindly
rename `CUSTOMER_DATABRICKS_*` to `DATABRICKS_*` for production automation.

This is especially important for customers who are **not** using mock
Databricks in production. In that case, the production workflow should continue
to be populated with the customer-facing `CUSTOMER_*` Databricks settings.

## D. Scope And Territory Mapping Inputs

Normal hosted customer mode now defaults to built-in Databricks vPower queries.

That means these older static inputs are no longer normal required hosted
inputs:

- `CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH`
- `CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH`
- `CUSTOMER_SALES_TEAM_STATIC_MAP_JSON`

They may still exist in the codebase as overrides or legacy fallbacks, but they
should not be part of the default customer CI/CD contract.

For customer teams copying this repo, the usual production path is:

- provide the Databricks workspace values
- provide the top-opps source
- provide the contacts source
- optionally qualify the vPower bronze catalog via:
  - `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
  - `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`

## E. Rep Lookup Input

The repo still exposes:

- `CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH`

Treat it as optional and legacy-oriented unless your customer explicitly still
wants the static rep-name lookup behavior.

It is not part of the primary signed-in-user scope path.

## F. Values That CI/CD Should Not Carry Forward From Old `.env.secure`

Do not treat these as GitHub-managed production inputs:

- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`

Those are release outputs, not operator-supplied inputs.

CI writes and consumes them through release metadata artifacts instead.

## Recommended Customer Migration Checklist

For a customer coming from a previous manual secure deployment:

1. copy the old `.env.secure` into a temporary migration worksheet
2. classify each key as:
   - GitHub variable
   - GitHub secret
   - Key Vault secret
   - no longer required
3. keep `CUSTOMER_DATABRICKS_*` for the customer production runtime path
4. remove static scope/sales-team JSON from the normal hosted setup unless the
   customer explicitly needs a legacy fallback
5. preserve only `CUSTOMER_SCOPE_ACCOUNTS_CATALOG` and
   `CUSTOMER_SALES_TEAM_MAPPING_CATALOG` if their vPower bronze tables need a
   non-default catalog qualifier
6. validate `VALIDATE_USER_UPN` against the customer Databricks workspace before
   the first production rollout
7. test `integration` first, then `main`

## Why `PLANNER_API_BEARER_TOKEN` And `VALIDATE_USER_UPN` Are Optional

These two values tend to look more important than they really are.

### `PLANNER_API_BEARER_TOKEN`

Purpose:

- lets CI run an authenticated chat turn against the planner API during
  validation

What it is **not**:

- it is not a core planner deployment input
- it is not required for the planner container to start
- it is not required for wrapper deployment

Current behavior:

- integration validation only requires it when `REQUIRE_AUTHENTICATED_E2E=true`
- the planner validation script will still do health checks without it
- production smoke validation can also skip authenticated chat when it is not
  present

Recommended treatment:

- document it as optional validation-only
- add it only if the customer wants richer automated chat validation in CI/CD

### `VALIDATE_USER_UPN`

Purpose:

- gives CI a known seller identity to test direct Databricks-backed territory
  and scoped-account resolution

What it is **not**:

- it is not a required production runtime setting
- it is not needed for planner deployment itself
- it is not needed for wrapper deployment itself

Recommended treatment:

- use it only when the customer wants automated query-path validation in CI/CD
- if present, choose a stable non-admin seller identity that is expected to
  have a predictable territory/account scope

## Suggested GitHub Environment Inventory

Use the following practical split.

### Integration Variables

- Azure subscription, tenant, location, resource group
- ACR name
- Container Apps names
- planner/wrapper public IDs and audiences
- either secure mock Databricks values or customer non-production Databricks
  values
- validation flags and validation UPN

Typical integration variable keys in this repo:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `ACR_NAME`
- `ACA_ENVIRONMENT_NAME`
- `PLANNER_ACA_APP_NAME`
- `WRAPPER_ACA_APP_NAME`
- `AZURE_OPENAI_ACCOUNT_NAME`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_SSO_APP_ID`
- `BOT_SSO_RESOURCE`
- `CUSTOMER_DATABRICKS_HOST`
- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID`
- `CUSTOMER_DATABRICKS_OBO_SCOPE`
- `CUSTOMER_DATABRICKS_WAREHOUSE_ID`
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`
- `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
- `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`
- `VALIDATE_USER_UPN`
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`

Treat the last two as validation-only knobs, not core runtime deployment inputs.

### Integration Secrets

- planner client secret
- bot password
- planner bearer token if the integration validation requires it

Typical integration secret keys in this repo:

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- `PLANNER_API_BEARER_TOKEN`

### Production Variables

- Azure subscription, tenant, location, resource group
- ACR name
- Container Apps names
- planner/wrapper public IDs and audiences
- `CUSTOMER_DATABRICKS_*`
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`
- optional vPower catalog qualifiers

Typical production variable keys in this repo:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `ACR_NAME`
- `ACA_ENVIRONMENT_NAME`
- `PLANNER_ACA_APP_NAME`
- `WRAPPER_ACA_APP_NAME`
- `AZURE_OPENAI_ACCOUNT_NAME`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `PLANNER_API_CLIENT_ID`
- `PLANNER_API_EXPECTED_AUDIENCE`
- `PLANNER_API_SCOPE`
- `BOT_APP_ID`
- `BOT_SSO_APP_ID`
- `BOT_SSO_RESOURCE`
- `CUSTOMER_DATABRICKS_HOST`
- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID`
- `CUSTOMER_DATABRICKS_OBO_SCOPE`
- `CUSTOMER_DATABRICKS_WAREHOUSE_ID`
- `CUSTOMER_TOP_OPPORTUNITIES_SOURCE`
- `CUSTOMER_CONTACTS_SOURCE`
- `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
- `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`
- `VALIDATE_USER_UPN`

Treat `VALIDATE_USER_UPN` as optional validation-only configuration. It is not
required for normal production deployment.

### Production Secrets

- planner client secret
- bot password
- optional registry password if the runtime needs it
- planner bearer token if required for smoke validation

Typical production secret keys in this repo:

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- `PLANNER_API_BEARER_TOKEN`
- `CONTAINER_REGISTRY_PASSWORD` when required by the customer's registry model

## Basic Setup Walkthrough For A Customer Team

This is the shortest end-to-end setup path for a customer who already has the
Azure and Databricks runtime environment.

1. fork or copy the repo
2. create the `dev`, `integration`, and `main` branch protections
3. create the GitHub Environments: `integration`, `production`,
   `teams-catalog-admin`, and `bootstrap-foundation`
4. create the Azure OIDC principal for `integration`
5. create the Azure OIDC principal for `production`
6. assign Azure RBAC for those principals
7. populate `integration` GitHub variables and secrets
8. populate `production` GitHub variables and secrets
9. if using Key Vault, add `KEYVAULT_NAME` plus the secret-name variables and
   confirm the runner can reach Key Vault
10. copy the values from the customer's old `.env.secure` into the matching
    GitHub variables and secrets
11. for production, keep the customer Databricks connection values in the
    `CUSTOMER_*` namespace
12. run a small PR through `dev`
13. promote to `integration`
14. validate that `deploy-integration.yml` works against either the secure mock
    environment or the customer's non-production Databricks environment
15. promote to `main`
16. confirm the production environment pauses for approval
17. approve and complete the first production release

## Team Collaboration Model

## Dev Team

Owns:

- planner code
- wrapper code
- tests
- package content
- non-privileged deploy helper changes
- release notes for functional behavior

## Infra Team

Owns:

- Azure IaC
- GitHub OIDC setup
- Azure RBAC
- Key Vault integration
- Container Apps environment settings
- networking
- Databricks platform integration model
- production deployment approval

## M365 Admin Team

Owns:

- Teams catalog publish
- tenant install policy
- app exposure governance
- delegated admin publish/install operations

## Collaboration Sequence

### App-Only Change

1. Dev implements on `feature/*`
2. merge to `dev`
3. CI passes
4. promote to `integration`
5. integration deploy validates runtime
6. promote to `main`
7. Infra approves production deployment
8. production deploy runs
9. M365 admin is only involved if the package changed and must be republished

### App Plus Infra Change

1. Dev and Infra align during design
2. Dev implements code and deployment changes
3. Infra reviews deployment impact
4. merge to `dev`
5. promote to `integration`
6. run integration deployment and, if necessary, manual bootstrap/foundation work
7. confirm runtime behavior
8. promote to `main`
9. Infra approves production deployment

### App Plus Infra Plus M365 Publish Change

1. Dev and Infra align on runtime and auth implications
2. code and manifest/package changes land on `dev`
3. integration validates runtime first
4. production runtime deployment happens after approval
5. M365 admin runs `publish-teams-catalog.yml` after runtime is healthy

Key rule:

- do not make Teams publish the first release step for a change that also
  affects backend runtime behavior

## Best Practices

1. Keep normal application CD separate from bootstrap/foundation changes.
2. Promote immutable image references from integration to production.
3. Keep RBAC repair out of normal deploy workflows.
4. Keep Teams publish independent from Azure deploy.
5. Make integration realistic enough to prove the customer query path.
6. Use human approvals only at the high-value control points.

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

## Final Guidance

If a customer is coming from an older version of this repo and a manually
maintained `mvp/.env.secure`, the most important migration rule is:

- **do not start by rewriting env names**
- first identify which values are still runtime inputs, which ones are now CI
  outputs, and which old static JSON inputs are no longer part of the normal
  hosted customer path

For this repo today, the safe production mental model is:

- planner and wrapper are deployed by GitHub Actions
- production runtime values come from GitHub Environments and optional Key Vault
- customer Databricks production inputs still live under `CUSTOMER_*`
- integration proves the release
- production promotes the same release after approval
