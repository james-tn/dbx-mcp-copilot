# GitHub Actions CI/CD Design

## Purpose

This document describes the recommended GitHub Actions CI/CD design for this
repository.

It covers:

- branch strategy
- workflow layout
- Azure OIDC authentication
- artifact promotion
- environment protection
- multi-team collaboration
- release sequencing when infrastructure changes are involved

This design is intentionally aligned to the current repository shape:

- planner service and M365 wrapper are deployed independently
- Azure runtime deployment is script-driven
- Microsoft 365 app package publish is a separate admin-controlled concern
- local and CI Python execution now use `uv`

## Goals

- make `dev` the main developer integration branch
- make `integration` the first real deployment branch
- make `main` the production branch
- use GitHub OIDC for Azure deployment authentication
- avoid long-lived Azure secrets in GitHub
- build once and promote forward instead of rebuilding different artifacts per environment
- keep Teams app catalog publish separate from normal Azure deploys
- preserve separation of duties between Dev, Infra, and M365 Admin teams

## Non-Goals

- full automation of Teams catalog publish by the same Azure OIDC identity
- forcing every code change through infra or M365 approval
- using committed `.env` files as the CI/CD system of record

## Repo Deployment Context

The repository already has clear operational boundaries:

- planner image build + deploy
- wrapper image build + deploy
- customer-target deploy
- foundation/bootstrap deploy
- M365 app package build and publish
- validation scripts for planner, Databricks, and customer query behavior

The CI/CD design should preserve those boundaries instead of collapsing
everything into one monolithic workflow.

## Target Branch Model

### Branches

- `feature/*`
  - developer feature branches
  - opened as PRs into `dev`
- `dev`
  - shared engineering branch
  - receives normal feature merges after CI passes
- `integration`
  - deployment branch for non-production Azure validation
  - receives merges from `dev`
- `main`
  - production branch
  - receives controlled merges from `integration`

### Branch Intent

- `dev` answers: "does the code build and pass tests?"
- `integration` answers: "does the code deploy and work in a real Azure environment?"
- `main` answers: "is this version approved for production?"

## Target GitHub Environment Model

### Environments

- `ci`
  - optional logical environment for CI-only jobs
- `integration`
  - non-production Azure deployment target
- `production`
  - production Azure deployment target
- `teams-catalog-admin`
  - separate manual environment for Teams app publish/install steps
- `bootstrap-foundation`
  - optional protected environment for rare privileged bootstrap runs

### Environment Protections

- `integration`
  - branch restriction: only `integration`
  - optional reviewer requirement for infra-affecting changes
- `production`
  - branch restriction: only `main`
  - required reviewers from Infra team
- `teams-catalog-admin`
  - manual workflow only
  - required reviewers from M365 admin team
- `bootstrap-foundation`
  - manual workflow only
  - required reviewers from Infra team

## Authentication And Secret Model

## Azure

Use GitHub OIDC for Azure login.

Recommended setup:

- one Entra application or user-assigned identity for `integration`
- one separate Entra application or user-assigned identity for `production`
- each one has its own federated credential bound to:
  - this repository
  - the intended branch or environment
  - the intended workflow scope

Recommended examples:

- `gh-dbx-mcp-copilot-integration`
- `gh-dbx-mcp-copilot-production`

### Azure Scope

Grant the integration identity only what it needs in the integration resource
group or subscription slice.

Grant the production identity only what it needs in the production resource
group or subscription slice.

Do not use one broad Azure identity for all environments.

### Key Vault

Keep sensitive runtime values in Azure Key Vault instead of GitHub repository
secrets whenever possible.

Typical values to source from Key Vault at workflow runtime:

- planner confidential client secret
- bot app password
- any explicit API key fallback values
- optional Databricks PAT values for special environments

GitHub environment variables can still hold non-secret settings such as:

- Azure tenant ID
- subscription ID
- resource group
- deployment mode
- container app names
- ACR name
- expected base URLs

### Hosted Runner Note

GitHub-hosted runners cannot read a private-only Key Vault unless the vault is
reachable from the public runner network.

For this repo, that means:

- if the target Key Vault allows public access or the workflow runs on a
  self-hosted runner inside the private network, runtime secrets can be read
  directly from Key Vault
- if the target Key Vault is private-endpoint-only and the workflow runs on a
  GitHub-hosted runner, the practical path is GitHub Environment secrets with
  Azure OIDC still used for Azure deployment auth

The workflows in this repo therefore support GitHub Environment secrets as the
first path and Key Vault lookup as an optional fallback.

## Microsoft 365 Graph And Teams Publish

Treat Teams app catalog publish as a separate trust path from Azure resource
deployment.

Reason:

- Azure OIDC is the right model for Azure deploy
- Teams app catalog publish is a Graph delegated-admin concern
- the same Azure OIDC identity should not be expected to also satisfy Teams
  catalog publish requirements

That means:

- build the Teams package in standard CI
- publish the Teams package in a separate manual admin workflow
- require M365 admin approval before that workflow can access its environment

## Artifact Strategy

## Principle

Build once, promote many.

Do not rebuild planner and wrapper images separately in integration and
production if the intent is to promote the same tested release.

## Recommended Artifacts

Each successful CI build should produce:

- planner image reference or immutable digest
- wrapper image reference or immutable digest
- M365 app package zip
- release metadata artifact, for example:
  - commit SHA
  - branch
  - build timestamp
  - planner image digest
  - wrapper image digest
  - package artifact path

## Promotion Rule

- `integration` deploy consumes the build artifact created from the merged code
- `production` deploy reuses the same exact image references or digests after
  approval

This is better than rebuilding on `main`, because rebuilds can drift from what
was validated in `integration`.

## Env File Strategy In CI/CD

The current operator scripts update runtime `.env` files with generated image
tags and discovered values. That behavior is useful for human-operated
deployment, but CI should not rely on committing `.env` mutations back into the
repo.

Recommended CI behavior:

- render an ephemeral runtime env file inside the GitHub runner workspace
- populate it from:
  - GitHub environment variables
  - Azure Key Vault secrets
  - artifact metadata
- pass that ephemeral env file into deploy and validation scripts
- never commit CI-generated runtime env files

## Workflow Catalog

## 1. `ci.yml`

### Trigger

- pull requests to `dev`, `integration`, and `main`
- pushes to `dev`

### Purpose

Validate code quality and buildability without deploying to Azure.

### Jobs

- `python-tests`
  - `uv sync --project mvp --group dev`
  - run tests under:
    - `mvp/infra/tests`
    - `mvp/m365_wrapper/tests`
    - `mvp/dev_ui/tests`
    - `mvp/agents/tests`
- `shell-validation`
  - run `bash -n` on `mvp/infra/scripts/*.sh` and selected `mvp/scripts/*.sh`
- `docker-build-smoke`
  - build planner Dockerfile
  - build wrapper Dockerfile
- `package-m365`
  - build the Teams package zip artifact

### Path Filtering

Use path filters so expensive jobs run only when needed.

Example:

- planner jobs on changes under `mvp/agents/**`, `mvp/shared/**`, `mvp/infra/**`
- wrapper jobs on changes under `mvp/m365_wrapper/**`, `mvp/shared/**`
- package jobs on changes under `mvp/appPackage/**`, `mvp/scripts/build-m365-app-package.sh`

## 2. `deploy-integration.yml`

### Trigger

- push to `integration`

### Purpose

Deploy the validated build to a non-production Azure environment automatically.

### Authentication

- GitHub OIDC to Azure
- protected `integration` environment

### Inputs

- artifact metadata from CI
- GitHub environment variables
- Key Vault secrets

### Recommended Steps

1. download the build artifact metadata
2. log into Azure with OIDC
3. fetch required secrets from Key Vault
4. render an ephemeral env file for integration
5. deploy planner
6. deploy wrapper
7. run integration validations
8. publish deployment summary as workflow artifact

### Integration Validations

Run a small but meaningful deployed validation set, for example:

- planner health
- planner session/message E2E
- customer vPower query validation for a known test user
- optional wrapper health validation

The integration environment should be the place where seeded or mockable
Databricks-backed behavior is proven continuously.

## 3. `deploy-production.yml`

### Trigger

- push to `main`
- optional `workflow_dispatch`

### Purpose

Promote the already-tested build to production.

### Authentication

- GitHub OIDC to Azure
- protected `production` environment
- required Infra approval

### Recommended Steps

1. resolve the exact artifact/image refs previously validated in integration
2. log into Azure with production OIDC identity
3. fetch production secrets from Key Vault
4. render an ephemeral production env file
5. deploy planner
6. deploy wrapper
7. run post-deploy smoke validation
8. record release metadata

### Production Guardrails

- no rebuilds in this workflow
- no automatic infra bootstrap
- no automatic Teams publish

## 4. `bootstrap-foundation.yml`

### Trigger

- `workflow_dispatch` only

### Purpose

Handle rare privileged infrastructure changes:

- new Azure resources
- new foundation networking
- new OIDC prerequisites
- new bootstrap-time app registration setup

### Why Separate

Bootstrap has a different blast radius from normal app delivery. It may involve:

- foundation resources
- role assignments
- managed identities
- networking
- initial environment creation

That should not run on every merge.

## 5. `build-m365-package.yml`

### Trigger

- changes to manifest/package inputs
- release tags
- optional `workflow_dispatch`

### Purpose

Build the M365 package zip and store it as a reusable artifact.

### Important Rule

This workflow builds the package artifact only. It does not publish it to the
tenant app catalog.

## 6. `publish-teams-catalog.yml`

### Trigger

- `workflow_dispatch` only

### Purpose

Allow the M365 admin team to publish or update the Teams/M365 app package after
backend runtime validation is already complete.

### Protection

- protected `teams-catalog-admin` environment
- required M365 admin reviewers

### Recommended Behavior

1. download the approved app package artifact
2. show release metadata and target wrapper endpoint
3. perform the publish/update path
4. optionally perform install-for-user or install-for-test-tenant steps
5. record the Graph response artifact

### Operational Choice

There are two acceptable operating modes:

- manual-assisted
  - workflow prepares everything, but admin runs the final Graph publish step
- admin-automated
  - workflow runs on an admin-controlled runner or admin-controlled delegated
    auth setup

For this repo, manual-assisted is the safer first version.

## Recommended Release Paths

## Class A: App-Only Change

Examples:

- prompt changes
- planner behavior changes
- wrapper logic changes
- tests
- local dev improvements

### Sequence

1. Dev implements the change on `feature/*`
2. PR into `dev`
3. CI passes
4. merge to `dev`
5. PR or merge from `dev` to `integration`
6. integration deploy runs automatically
7. if good, PR from `integration` to `main`
8. Infra approves production deploy gate
9. production deploy runs
10. if the Teams package did not change, M365 admin is not involved

## Class B: App + Infra Change

Examples:

- new Container App setting
- new managed identity use
- new Key Vault secret
- new Databricks resource dependency
- new infra module or Azure resource

### Sequence

1. Dev and Infra align during design before code is finalized
2. Dev implements app code and IaC changes
3. Infra reviews the PR for deployment impact
4. CI passes on `dev`
5. merge to `integration`
6. bootstrap or deploy workflow runs depending on change type
7. integration validation proves both infra and app behavior
8. PR from `integration` to `main`
9. Infra approves production environment deployment
10. production deploy runs
11. M365 admin participates only if app publish is also required

## Class C: App + Infra + M365 Publish Change

Examples:

- auth surface changes that affect Teams app package
- manifest changes
- bot app or SSO metadata changes
- endpoint or domain changes exposed through the app package

### Sequence

1. Dev and Infra align on runtime and infrastructure impact
2. Dev implements code and package changes
3. Infra reviews infra/auth implications
4. CI passes on `dev`
5. merge to `integration`
6. integration deploy validates runtime first
7. PR from `integration` to `main`
8. production runtime deploy completes
9. M365 admin runs `publish-teams-catalog.yml`
10. admin validates installation and catalog visibility

### Key Rule

Never make Teams catalog publish the first deployment step for a release that
also changes runtime behavior. Publish only after backend runtime is healthy.

## Team Responsibilities

## Dev Team

Owns:

- planner code
- wrapper code
- tests
- package content
- non-privileged deployment script changes
- release notes for functional behavior

Dev is required on:

- all feature PRs
- app-level bug fixes
- package changes

## Infra Team

Owns:

- Azure IaC
- GitHub OIDC setup
- Azure RBAC model
- Key Vault integration
- Container Apps environment settings
- private networking
- Databricks platform integration model
- production deployment approvals

Infra is required on:

- infra-affecting PRs
- production environment approval
- bootstrap/foundation changes

## M365 Admin Team

Owns:

- Teams catalog publish
- install policy and app availability
- Entra/M365 app publishing governance
- any delegated-admin publish path

M365 admin is required on:

- app publish/update
- package approval for tenant exposure
- changes that affect app registration/publish policy

## Collaboration Model

### Work In Parallel

Dev and Infra should work in parallel at design time when a change clearly needs
new infrastructure.

That is better than Dev finishing a feature first and only then discovering:

- missing identity permissions
- missing secrets
- missing network path
- missing resource provisioning

### Work In Sequence At Control Points

The teams should converge only at these control points:

1. PR review
2. integration deployment
3. production deployment
4. Teams catalog publish

That keeps collaboration intentional instead of making every change a three-team
serial handoff.

## Required PR Metadata For Infra-Affecting Changes

Every PR that changes deployment behavior should include a short deployment
impact note.

Recommended template:

- change class: `app-only`, `app+infra`, or `app+infra+m365`
- new Azure resources: yes/no
- new secrets: yes/no
- new RBAC: yes/no
- new environment variables: list
- bootstrap required: yes/no
- Teams republish required: yes/no
- rollback method: short description

## Best Practices For This Repo

## 1. Separate Normal CD From Bootstrap

Normal application delivery should not invoke full bootstrap on every merge.

Use:

- deploy workflows for routine planner/wrapper promotion
- bootstrap workflows only for foundational infra changes

## 2. Prefer Immutable Image Promotion

Promote the same planner and wrapper artifacts from integration to production.

## 3. Keep Role Assignment Out Of Routine CD Where Possible

Pre-provision stable RBAC instead of relying on normal deploy workflows to
create role assignments dynamically.

## 4. Keep Teams Publish Independent

Do not block normal Azure deployment on Teams publish unless the release
actually changes the published app surface.

## 5. Make Integration Real

The integration environment should be stable enough to prove:

- planner deploy works
- wrapper deploy works
- Databricks query path works
- auth path works for at least one known integration identity

## 6. Preserve Human Approval At The Right Boundaries

Use automation for repetition.

Use human approval for:

- production environment promotion
- foundation/bootstrap changes
- tenant app catalog publish

## Suggested Initial Implementation Order

### Phase 1

- add `ci.yml`
- add `build-m365-package.yml`
- add branch protections

### Phase 2

- add Azure OIDC identities for integration and production
- add `deploy-integration.yml`
- add `deploy-production.yml`
- store secrets in Key Vault and fetch them during workflow execution

### Phase 3

- add `bootstrap-foundation.yml`
- add `publish-teams-catalog.yml`
- add deployment metadata artifacts and release summaries

### Phase 4

- refine path-based workflow routing
- add rollback workflow or rollback runbook
- add release dashboards or change summaries

## Decision Summary

The recommended operating model for this repo is:

- CI on `dev`
- automatic Azure CD on `integration`
- approved Azure CD on `main`
- separate manual admin workflow for Teams catalog publish
- OIDC for Azure
- Key Vault for secrets
- immutable artifact promotion across environments
- Dev, Infra, and M365 Admin collaboration only at clear control points

This gives you speed for normal development, proper control for infrastructure
changes, and a clean separation of duties for M365 publish operations.
