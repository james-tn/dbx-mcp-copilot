# GitHub Actions CI/CD Overview

## Purpose

This document explains the conceptual CI/CD model for this repository.

Use this file when you want to understand:

- the branch promotion model
- what each workflow is responsible for
- how GitHub Environments, Azure OIDC, RBAC, and Key Vault fit together
- how Dev, Infra, and M365 Admin teams should collaborate

If you need step-by-step setup instructions, use
[`cicd-setup-guide.md`](cicd-setup-guide.md).

If you need validation drills, rollout checks, or operational troubleshooting,
use [`cicd-validation-and-operations.md`](cicd-validation-and-operations.md).

## Executive Summary

The recommended operating model for this repo is:

- developers work on `feature/*`
- `dev` is the shared engineering branch
- `integration` is the automatic non-production deployment branch
- `main` is the approved production branch
- GitHub Actions uses Azure OIDC instead of long-lived Azure credentials
- CI builds release artifacts on `integration`
- `integration` deploys and validates the tested release
- `main` promotes that already-tested release to production
- Teams/M365 app publish remains separate from Azure runtime deployment

## Branch And Environment Model

### Branches

- `feature/*`
  - developer working branches
  - merged into `dev`
- `dev`
  - engineering integration branch
  - validates buildability and tests
- `integration`
  - secure non-production deployment branch
  - validates Azure deployment and runtime behavior
- `main`
  - production branch
  - deploys the already-tested release after approval

### GitHub Environments

- `integration`
  - secure non-production Azure deployment target
- `production`
  - secure customer-target production deployment
- `teams-catalog-admin`
  - manual publish/install path for Teams/M365 package operations
- `bootstrap-foundation`
  - manual, privileged environment for bootstrap and foundation changes

### Protection Rules

- `integration`
  - normally limited to the `integration` branch
  - optional reviewer gate if Infra wants one
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

## Repository Deployment Model

This repo has four distinct deployment concerns:

1. planner runtime deployment
2. wrapper runtime deployment
3. foundation and bootstrap changes
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

## Deployment Profiles

### Secure Mock Integration

Used by `deploy-integration.yml`.

Purpose:

- prove planner and wrapper deployment in a secure Azure shape
- validate the Databricks-backed customer query path continuously
- avoid touching production customer data

Expected characteristics:

- private or secure-style Azure topology
- Databricks-backed data that is safe for non-production validation
- at least one known validation UPN
- stable integration resource group, ACR, Container Apps names, and runtime
  values

Customer copy and deployment note:

- the upstream repo uses the profile name `secure-mock`, but customer copies
  should not assume a seeded Databricks workspace already exists
- the normal customer pattern is:
  - use an existing non-production Databricks workspace
  - populate it separately with the data needed for validation
  - point the `integration` GitHub Environment at that workspace
- if a customer wants a mock or seeded Databricks path, treat that as an
  explicit extra setup decision, not as the default assumption

### Secure Customer Production

Used by `deploy-production.yml`.

Purpose:

- deploy the tested planner and wrapper release into the existing customer
  environment

Expected characteristics:

- existing customer Azure resources already provisioned and approved
- existing customer Databricks workspace already provisioned and accessible
- no mock seeding
- no bootstrap or foundation mutation during normal delivery
- no schema mutation or seed mutation against the customer's live workspace

## Workflow Catalog

### `ci.yml`

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
  - runs:
    - `mvp/infra/tests`
    - `mvp/m365_wrapper/tests`
    - `mvp/dev_ui/tests`
    - `mvp/agents/tests`
- `shell-validation`
  - `bash -n` over infra and selected repo scripts
- `docker-build-smoke`
  - local planner and wrapper Docker builds
- `package-m365`
  - builds the Teams/M365 package artifact with CI-safe placeholders
- `build-release-artifacts`
  - runs on push to `integration`
  - builds planner and wrapper images in ACR
  - writes `release-metadata-<sha>`

Important design point:

- CI is where the release metadata artifact is created for deployable releases
- production later reuses that metadata rather than rebuilding

### `deploy-integration.yml`

Purpose:

- automatically deploy the validated `integration` release into the secure
  non-production environment

Current flow:

1. log into Azure with the integration OIDC identity
2. download `release-metadata-<sha>` produced by `ci.yml`
3. read secrets from GitHub Environment secrets and optionally Key Vault
4. render an ephemeral runtime env file
5. deploy planner and wrapper
6. run deployed validations
7. upload a redacted deployment summary artifact

### `deploy-production.yml`

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

### `build-m365-package.yml`

Purpose:

- build the Teams/M365 package artifact on demand

This workflow remains package-only. It does not deploy Azure resources.

### `publish-teams-catalog.yml`

Purpose:

- support the admin-controlled publish and install path for the Teams/M365
  package

This workflow stays manual and separate from Azure runtime deployment.

### `bootstrap-foundation.yml`

Purpose:

- handle rare foundation and bootstrap changes with a larger blast radius

This workflow should not be part of the normal planner/wrapper release path.

## Authentication And Secret Model

### Azure OIDC

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
- keep bootstrap or foundation on a separate identity when the customer wants a
  tighter separation of duties

This repo includes OIDC setup helpers:

- [`scripts/setup-github-oidc.sh`](scripts/setup-github-oidc.sh)
- [`scripts/setup-github-oidc.ps1`](scripts/setup-github-oidc.ps1)

### Azure RBAC

Pre-provision stable access for the OIDC identities.

Do not rely on normal deploy workflows to repair missing RBAC dynamically.

Deploy workflows should fail fast if RBAC is missing.

#### Exact RBAC For The `integration` OIDC Principal

Assign exactly these roles:

- `Contributor`
  - scope: the integration deployment scope
  - recommended scope: the integration resource group
- `AcrPush`
  - scope: the integration ACR resource or the resource group containing it
- `Key Vault Secrets User`
  - scope: the integration Key Vault resource when Key Vault lookup is enabled

Do not grant `User Access Administrator` or `Role Based Access Control
Administrator` for routine integration delivery.

#### Exact RBAC For The `production` OIDC Principal

Assign exactly these roles:

- `Contributor`
  - scope: the production deployment scope
  - recommended scope: the production resource group
- `Key Vault Secrets User`
  - scope: the production Key Vault resource when Key Vault lookup is enabled

Do not grant `AcrPush` to the production identity unless the customer has a
non-standard production flow that actually builds images in production.

#### Exact RBAC For The Optional `bootstrap-foundation` OIDC Principal

If the customer wants bootstrap isolated from normal release delivery, create a
separate `bootstrap-foundation` principal.

Assign:

- `Contributor`
  - scope: the bootstrap or foundation resource group or subscription slice

Only add broader roles if the customer proves they are required for a specific
bootstrap variant.

### GitHub Secrets Vs Key Vault

This repo supports both:

- GitHub Environment secrets
- Azure Key Vault lookup at workflow runtime

Practical guidance:

- keep non-secret settings in GitHub Environment variables
- keep sensitive values in GitHub Environment secrets or Key Vault
- if Key Vault is private-endpoint-only and the workflow runs on a GitHub-hosted
  runner, direct Key Vault access may not work
- in that case, GitHub Environment secrets are the practical path even though
  Azure OIDC is still used for deployment authentication

### Teams/M365 Publish Auth

Treat Teams/M365 publish as a separate admin-controlled trust path.

Do not try to make the normal Azure OIDC deployment identity also satisfy the
Teams catalog publish/install path.

## Team Collaboration Model

### Dev Team

Owns:

- planner code
- wrapper code
- tests
- package content
- non-privileged deploy helper changes
- release notes for functional behavior

### Infra Team

Owns:

- Azure IaC
- GitHub OIDC setup
- Azure RBAC
- Key Vault integration
- Container Apps environment settings
- networking
- Databricks platform integration model
- production deployment approval

### M365 Admin Team

Owns:

- Teams catalog publish
- tenant install policy
- app exposure governance
- delegated admin publish/install operations

### Collaboration Sequence

#### App-Only Change

1. Dev implements on `feature/*`
2. merge to `dev`
3. CI passes
4. promote to `integration`
5. integration deploy validates runtime
6. promote to `main`
7. Infra approves production deployment
8. production deploy runs
9. M365 admin is only involved if the package changed and must be republished

#### App Plus Infra Change

1. Dev and Infra align during design
2. Dev implements code and deployment changes
3. Infra reviews deployment impact
4. merge to `dev`
5. promote to `integration`
6. run integration deployment and, if necessary, manual bootstrap or foundation
   work
7. confirm runtime behavior
8. promote to `main`
9. Infra approves production deployment

#### App Plus Infra Plus M365 Publish Change

1. Dev and Infra align on runtime and auth implications
2. code and manifest/package changes land on `dev`
3. integration validates runtime first
4. production runtime deployment happens after approval
5. M365 admin runs `publish-teams-catalog.yml` after runtime is healthy

Key rule:

- do not make Teams publish the first release step for a change that also
  affects backend runtime behavior

## Best Practices

1. Keep normal application CD separate from bootstrap and foundation changes.
2. Promote immutable image references from integration to production.
3. Keep RBAC repair out of normal deploy workflows.
4. Keep Teams publish independent from Azure deploy.
5. Make integration realistic enough to prove the customer query path.
6. Use human approvals only at the high-value control points.
