# GitHub Actions CI/CD Setup Guide

## Purpose

This document is the step-by-step setup and operating guide for CI/CD in this
repository.

It is written for the repo as it exists today, not as a generic future-state
proposal.

It covers:

- the delivery model
- the GitHub branch and environment strategy
- Azure OIDC setup
- GitHub Actions workflow setup
- runtime secret and environment wiring
- integration and production deployment flow
- Teams package handling
- how Dev, Infra, and M365 Admin teams should work together
- how to handle normal app changes versus infra-affecting changes
- how docs-only or env-template-only repo changes stay out of the normal CI/CD path

## What This Repo Deploys

This repository has three distinct delivery surfaces:

1. The planner service
   - Azure Container App
   - Databricks-backed runtime
   - OpenAI-backed orchestration/runtime

2. The M365 wrapper
   - separate Azure Container App
   - front-door integration point for the Microsoft 365 agent experience

3. The M365 package
   - app package zip and manifest
   - built in CI
   - published separately through an admin-controlled path

These should not be collapsed into one single privilege domain.

## High-Level Design

### Core Principles

- build once, promote forward
- use GitHub OIDC for Azure authentication
- keep runtime secrets out of the repo
- keep Teams app catalog publish separate from Azure deployment
- treat foundation/bootstrap as a privileged exception path
- let app-only delivery stay fast

### Deployment Model

- `feature/*` is where developers work
- `dev` is the shared engineering branch
- `integration` is the deployed secure-mock validation branch
- `main` is the production branch

The intended progression is:

1. developer change lands in `dev`
2. validated code is promoted to `integration`
3. `integration` auto-deploys to secure-mock Azure
4. validated release is promoted to `main`
5. `main` deploys to production after environment approval
6. Teams package publish stays manual and admin-controlled

### Environment Model

The repo is designed around these GitHub Environments:

- `integration`
- `production`
- `teams-catalog-admin`
- `bootstrap-foundation`

The repo already contains workflows bound to those environments under
[`.github/workflows`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows).

## Implemented Workflow Catalog

These workflows already exist in the repo:

- [`ci.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/ci.yml)
- [`deploy-integration.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/deploy-integration.yml)
- [`deploy-production.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/deploy-production.yml)
- [`build-m365-package.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/build-m365-package.yml)
- [`publish-teams-catalog.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/publish-teams-catalog.yml)
- [`bootstrap-foundation.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/bootstrap-foundation.yml)

Supporting CI/CD scripts already exist under
[`mvp/infra/scripts`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts).

The most important CI/CD entrypoints are:

- [`ci-render-runtime-env.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-render-runtime-env.sh)
- [`ci-deploy-stack.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-deploy-stack.sh)
- [`ci-validate-integration.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-validate-integration.sh)
- [`ci-download-release-artifact.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-download-release-artifact.sh)
- [`ci-write-release-metadata.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-write-release-metadata.sh)

## Step 1: Decide The Operating Model

Before wiring anything, align on these fixed choices:

- `integration` is a secure mock deployment environment
- `production` is the secure customer-target deployment environment
- normal releases deploy planner and wrapper automatically
- Teams package publish is not part of the Azure deployment workflow
- foundation/bootstrap is not part of normal application delivery

If your teams agree on those rules first, the rest of the setup becomes much
clearer.

## Step 2: Configure Repository Branching

Create or protect these branches:

- `dev`
- `integration`
- `main`

Recommended usage:

- developers branch from `dev`
- promotion into `integration` happens from `dev`
- promotion into `main` happens from `integration`

Recommended protection rules:

- `dev`
  - require pull request
  - require CI success
  - require at least one review from Dev team
- `integration`
  - require pull request
  - require CI success
  - restrict direct pushes
  - optionally require Infra review when infra files changed
- `main`
  - require pull request from `integration`
  - require CI success
  - restrict direct pushes
  - production deployment approval handled by GitHub Environment

## Step 3: Configure GitHub Environments

Create these GitHub Environments in the repository settings:

### `integration`

Use for secure-mock deployment.

Recommended protections:

- restrict to branch `integration`
- optional reviewers if you want a light gate

Recommended variables:

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
- `DATABRICKS_HOST`
- `DATABRICKS_AZURE_RESOURCE_ID`
- `DATABRICKS_OBO_SCOPE`
- `DATABRICKS_WAREHOUSE_ID`
- `TOP_OPPORTUNITIES_SOURCE`
- `CONTACTS_SOURCE`
- `SCOPE_ACCOUNTS_CATALOG`
- `SALES_TEAM_MAPPING_CATALOG`
- `WRAPPER_ENABLE_DEBUG_CHAT`
- `WRAPPER_DEBUG_ALLOWED_UPNS`
- `WRAPPER_DEBUG_EXPECTED_AUDIENCE`
- `ACA_MIN_REPLICAS`
- `ACA_MAX_REPLICAS`
- `KEYVAULT_NAME`
- `KEYVAULT_PLANNER_API_CLIENT_SECRET_NAME`
- `KEYVAULT_BOT_APP_PASSWORD_NAME`
- `KEYVAULT_PLANNER_API_BEARER_TOKEN_NAME`
- `VALIDATE_USER_UPN`
- `REQUIRE_AUTHENTICATED_E2E`
- `ENABLE_WRAPPER_HEALTHCHECK`

Recommended secrets when not sourcing directly from Key Vault:

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- `PLANNER_API_BEARER_TOKEN`

### `production`

Use for secure customer-target production deployment.

Recommended protections:

- restrict to branch `main`
- require Infra reviewers

Use the same variable model as `integration`, but with production values only.

Do not mix integration and production values in the same environment.

### `teams-catalog-admin`

Use only for the manual Teams package publish workflow.

Recommended protections:

- manual workflow only
- required reviewers from M365 Admin team

This environment should not carry Azure deployment credentials.

### `bootstrap-foundation`

Use only for foundation/bootstrap workflows.

Recommended protections:

- manual workflow only
- required reviewers from Infra team

This environment is intentionally privileged and should be used sparingly.

## Step 4: Set Up Azure OIDC

Use separate Azure trust paths for integration and production.

Recommended identities:

- `gh-dbx-mcp-copilot-integration`
- `gh-dbx-mcp-copilot-production`

You can implement these as:

- Entra app registrations with federated credentials
- or user-assigned managed identities fronted by federated credentials

### Integration OIDC Setup

For the integration identity:

1. Create the Entra application or user-assigned identity.
2. Add a federated credential for this GitHub repository.
3. Scope it to the `integration` deployment path.
4. Grant Azure RBAC only to the integration target scope.
5. Put the resulting client ID into GitHub Environment `integration` as `AZURE_CLIENT_ID`.

### Production OIDC Setup

For the production identity:

1. Create a separate Entra application or user-assigned identity.
2. Add a federated credential for this repository.
3. Scope it to the `main`/production deployment path.
4. Grant RBAC only to the production target scope.
5. Put the client ID into GitHub Environment `production` as `AZURE_CLIENT_ID`.

### Minimum GitHub Workflow Permission

The workflows already request:

- `id-token: write`
- `contents: read`

That is required for `azure/login`.

### RBAC Guidance

Do not rely on deploy workflows to create role assignments dynamically.

Best practice is:

- pre-provision RBAC once
- let routine deploys fail fast if RBAC is wrong
- repair RBAC out-of-band through Infra change control

## Step 5: Decide How Secrets Are Sourced

This repo supports two practical models:

### Model A: GitHub Environment secrets first

Use this when:

- GitHub-hosted runners cannot reach a private Key Vault
- you want the simplest initial setup

In this mode:

- non-secret settings stay in GitHub Environment variables
- secrets stay in GitHub Environment secrets
- Azure OIDC is still used for deployment authentication

### Model B: Key Vault first, GitHub secrets as fallback

Use this when:

- runners can reach Key Vault
- you want runtime secret retrieval through Azure

The deploy workflows already implement this pattern:

- if a secret is already set in the environment, the workflow uses it
- otherwise it fetches from Key Vault using `az keyvault secret show`

### Recommended Secrets

These are the main sensitive values in current runtime delivery:

- planner confidential client secret
- bot app password
- optional planner API bearer token for authenticated E2E validation
- optional API-key fallbacks where applicable

## Step 6: Understand Build-Once / Promote-Forward

This repo already supports artifact promotion through release metadata.

### What CI Builds

On push to `integration`, [`ci.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/ci.yml):

- runs Python test shards
- validates shell scripts
- smoke-builds both Docker images locally
- builds the Teams package artifact
- builds planner and wrapper images in ACR
- emits release metadata

### Release Metadata

Release metadata is written by
[`ci-write-release-metadata.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-write-release-metadata.sh).

It records:

- commit SHA
- git ref
- build timestamp
- planner image ref and digest
- wrapper image ref and digest
- M365 package artifact name
- deployment mode and profile metadata

### Promotion Rule

- integration deploy consumes the release metadata artifact for that SHA
- production deploy downloads the same release metadata artifact
- production does not rebuild planner or wrapper images

This is the most important safeguard in the flow.

## Step 6A: Keep Docs-Only Changes Out Of CD

This repo should not spend Azure build/deploy capacity on docs-only maintenance.

The workflow triggers are therefore expected to ignore changes that only touch:

- Markdown docs
- tracked env templates and example inputs

Practical effect:

- editing `README.md` or other `*.md` files should not trigger CI or deploy
  workflows by itself
- editing `mvp/.env.example`, `mvp/.env.secure.example`,
  `mvp/.env.inputs.example`, or `mvp/.env.secure.inputs.example` should also be
  treated as documentation/template maintenance
- any PR that also changes code, scripts, or workflow files still runs the
  normal CI/CD path

## Step 7: Understand The Runtime Env Rendering Model

Do not use tracked `.env` files as the CI system of record.

The CI/CD path renders ephemeral env files in the runner workspace using
[`ci-render-runtime-env.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-render-runtime-env.sh).

That script combines:

- the example env template
- workflow environment variables
- runtime secrets
- release metadata

It writes a temporary env file used only by the workflow run.

This is the correct model for CI because it:

- avoids mutating tracked env files
- keeps runtime inputs explicit
- allows promotion using the same artifact metadata

## Step 8: Set Up The Integration Environment

Integration is the first real deployment environment.

Its purpose is to answer:

"Does the merged code deploy and work in a real secure Azure environment?"

### Integration Profile

Use `secure-mock`.

That means:

- secure deployment shape
- mock or seeded Databricks-backed data
- no customer production data
- stable known validation user

### What `deploy-integration.yml` Does

The workflow:

1. triggers on push to `integration`
2. logs into Azure using OIDC
3. downloads release metadata from CI
4. fetches secrets from Key Vault when needed
5. renders an ephemeral env file
6. deploys planner and wrapper via
   [`ci-deploy-stack.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-deploy-stack.sh)
7. runs deployed validations via
   [`ci-validate-integration.sh`](/mnt/c/testing/veeam/revenue_intelligence/mvp/infra/scripts/ci-validate-integration.sh)
8. uploads redacted deployment summary artifacts

### Integration Validation Checklist

The integration environment should validate:

- planner health
- wrapper health when enabled
- authenticated planner E2E when required
- customer vPower query path for `VALIDATE_USER_UPN`
- secure deployment shape compatibility

## Step 9: Set Up The Production Environment

Production is the customer-target secure runtime environment.

Its purpose is to answer:

"Can we safely promote the already-validated release into production?"

### Production Guardrails

- no rebuilds
- no bootstrap
- no seed
- no routine RBAC mutation
- no automatic Teams publish

### What `deploy-production.yml` Does

The workflow:

1. triggers on push to `main` or manual dispatch
2. resolves the release SHA to promote
3. logs into Azure using production OIDC
4. downloads the release metadata artifact for that SHA
5. fetches secrets from Key Vault when needed
6. renders an ephemeral production env file
7. deploys planner and wrapper
8. runs smoke validation
9. writes production deployment summary artifacts

### Production Validation Checklist

At minimum validate:

- planner service health
- wrapper health if exposed
- customer vPower query validation for a known allowed user if appropriate

## Step 10: Keep Teams Package Publish Separate

Teams package build and Teams catalog publish are intentionally separate.

### Build Phase

[`build-m365-package.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/build-m365-package.yml):

- builds the M365 package artifact
- uploads the zip and manifest
- does not publish to the tenant

### Publish Phase

[`publish-teams-catalog.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/publish-teams-catalog.yml):

- is manual only
- uses environment `teams-catalog-admin`
- downloads the selected package artifact
- prepares an admin handoff summary
- stops before pretending Azure deployment auth can do tenant admin work

This separation is a feature, not a limitation.

It keeps Azure deployment privilege and Teams admin privilege from being mixed.

## Step 11: Keep Foundation Bootstrap Separate

[`bootstrap-foundation.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/bootstrap-foundation.yml)
exists for privileged foundation setup, not for normal app delivery.

Use it only when needed for things like:

- new Azure foundation resources
- initial environment creation
- rare networking or identity bootstrap
- seeded mock environment creation

Normal app delivery should not run bootstrap.

## Step 12: Understand What Counts As “App-Only” Versus “Infra”

This is where teams usually get confused.

### App-only change

Examples:

- prompt changes
- planner behavior changes
- wrapper behavior changes
- bug fixes
- tests
- local dev changes

Required action:

- rebuild affected container image
- redeploy affected service

Not required:

- full bootstrap
- foundation reprovisioning
- Teams publish unless package content changed

### App plus runtime/deploy-contract change

Examples:

- new environment variable
- changed runtime secret name
- new deploy script expectation
- new validation requirement
- new container app setting

Required action:

- integration deploy validation
- likely production redeploy
- infra review recommended

### Infra/foundation change

Examples:

- new Azure resources
- RBAC model changes
- private networking changes
- new managed identity
- foundational auth changes

Required action:

- Infra involvement from design time
- likely bootstrap/foundation workflow
- integration validation before production

### M365 publish change

Examples:

- manifest update
- bot endpoint or catalog metadata change
- changed app branding or permissions

Required action:

- build package artifact
- publish through admin workflow
- do this after runtime is healthy

## Team Collaboration Model

This repo works best with three teams:

- Dev
- Infra
- M365 Admin

### Dev Team Owns

- application code
- prompts
- planner logic
- wrapper logic
- tests
- most normal feature delivery

### Infra Team Owns

- Azure OIDC identities
- RBAC
- Key Vault
- environment configuration
- production approval gates
- privileged bootstrap/foundation changes

### M365 Admin Team Owns

- Teams app catalog publish
- tenant-level app visibility
- install/update policy where needed

## Recommended Collaboration Sequences

### Sequence A: App-only change

1. Dev implements on `feature/*`
2. PR into `dev`
3. CI passes
4. merge to `dev`
5. promote to `integration`
6. integration deploy runs automatically
7. if successful, promote to `main`
8. Infra approves production environment gate
9. production deploy runs
10. M365 Admin is involved only if package publish is required

### Sequence B: App plus infra-affecting change

1. Dev and Infra align during design
2. Dev implements code and required deployment/script changes
3. Infra reviews before `integration`
4. CI passes
5. promote to `integration`
6. integration deploy proves the full runtime
7. if foundation change is needed, run `bootstrap-foundation.yml` deliberately
8. promote to `main`
9. Infra approves production deployment

### Sequence C: App plus infra plus M365 publish change

1. Dev and Infra align first
2. runtime deploy path is validated before package publish
3. integration runtime deploy completes
4. production runtime deploy completes
5. M365 Admin runs `publish-teams-catalog.yml`
6. admin validates catalog visibility/install behavior

### Important Rule

When runtime behavior and Teams package both change, runtime goes first.

Do not use Teams publish as the first validation step for a backend release.

## End-to-End Setup Checklist

Use this checklist to stand up CI/CD from scratch.

### GitHub

1. Create protected branches:
   - `dev`
   - `integration`
   - `main`
2. Create GitHub Environments:
   - `integration`
   - `production`
   - `teams-catalog-admin`
   - `bootstrap-foundation`
3. Add environment variables for each target environment.
4. Add secrets directly in GitHub only if Key Vault lookup is not available.
5. Enable required reviewers on `production` and `teams-catalog-admin`.

### Azure

1. Create the integration OIDC identity.
2. Create the production OIDC identity.
3. Add federated credentials for this repo.
4. Grant integration RBAC only to integration scope.
5. Grant production RBAC only to production scope.
6. Create or assign Key Vault access where needed.
7. Ensure ACR permissions exist for build and deploy.
8. Ensure Container Apps permissions exist for deploy/update.

### Runtime Environments

1. Stand up secure-mock integration environment.
2. Confirm a valid `VALIDATE_USER_UPN` exists there.
3. Confirm Databricks sources and warehouse are reachable.
4. Stand up production target environment.
5. Confirm production variables match real runtime values.

### Teams Admin Path

1. Confirm package build workflow works.
2. Confirm `publish-teams-catalog.yml` can download package artifacts.
3. Confirm M365 Admin team owns the final publish/install path.

## Recommended First Simulation Drill

After setup, run one controlled drill.

### Drill Steps

1. Create a small safe change on `feature/*`.
2. Open PR into `dev`.
3. Confirm `ci.yml` passes.
4. Merge into `dev`.
5. Promote into `integration`.
6. Confirm:
   - release metadata artifact exists
   - integration OIDC login succeeds
   - env rendering succeeds
   - planner deploy succeeds
   - wrapper deploy succeeds
   - integration validations pass
7. Promote into `main`.
8. Confirm:
   - production workflow downloads the same release metadata
   - production does not rebuild images
   - production deploy succeeds after approval
9. Optionally run `publish-teams-catalog.yml` to prepare admin handoff.

That drill proves the whole chain before you depend on it for real releases.

## Runbook For Ongoing Releases

### Normal application release

1. merge feature work into `dev`
2. let CI validate
3. promote `dev` to `integration`
4. let integration deploy validate runtime
5. promote `integration` to `main`
6. approve production deployment
7. optionally publish Teams package if needed

### Emergency production redeploy

Use [`deploy-production.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/deploy-production.yml)
with `workflow_dispatch` and `release_sha`.

This should redeploy an already-built release, not create a new one.

### Foundation change

Use [`bootstrap-foundation.yml`](/mnt/c/testing/veeam/revenue_intelligence/.github/workflows/bootstrap-foundation.yml)
deliberately and document why the privileged path was needed.

## Practical Notes For This Repo

- CI currently builds release artifacts only on push to `integration`
- integration deploy currently consumes `release-metadata-${sha}`
- production deploy resolves the promoted release SHA and downloads the same
  metadata artifact
- CI and deploy workflows should ignore docs-only and env-template-only changes
- env rendering supports canonical `DATABRICKS_*`, `TOP_OPPORTUNITIES_*`,
  `CONTACTS_*`, `SCOPE_ACCOUNTS_*`, and `SALES_TEAM_MAPPING_*` names, while
  still tolerating legacy aliases during migration
- Teams publish remains intentionally manual-admin

## Suggested Follow-Up Improvements

These are good next steps, but they are not required to make the current CI/CD
system operational:

- add path-based job filtering to reduce CI cost
- add a change classifier that distinguishes app-only versus infra-affecting PRs
- add automated rollback guidance in production summary artifacts
- add a formal self-hosted runner strategy if private-only Key Vault access is
  required

## Bottom Line

The right operating model for this repo is:

- CI validates every branch promotion
- `integration` is the first real deployed proof point
- `main` promotes the already-tested release
- Azure deployment uses OIDC
- Teams publish is separate
- foundation/bootstrap stays privileged and uncommon
- Dev, Infra, and M365 Admin collaborate in sequence rather than sharing one
  oversized credential path
