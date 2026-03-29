# GitHub Actions CI/CD Setup Guide

## Purpose

This document is the step-by-step setup guide for customer teams or internal
operators wiring this repository to GitHub Actions, Azure OIDC, and an
existing or newly provisioned Azure runtime.

Use this file when you need to:

- choose the right customer setup path
- create GitHub Environments
- create Azure OIDC identities
- assign Azure RBAC
- migrate from an older `.env.secure` deployment model
- populate the required variables and secrets

If you want the conceptual model first, use
[`cicd-overview.md`](cicd-overview.md).

If you want validation drills and rollout checks after setup, use
[`cicd-validation-and-operations.md`](cicd-validation-and-operations.md).

## Quick Setup Overview

This repo's GitHub Actions model is based on these fixed choices:

- branch flow: `feature/*` -> `dev` -> `integration` -> `main`
- `dev` runs CI validation
- `integration` builds the release artifact, deploys it to Azure, and validates
  the deployed integration environment
- `main` promotes the already-tested integration artifact to production
- Azure authentication uses GitHub OIDC, not stored Azure credentials
- Teams/M365 catalog publish stays separate from normal Azure deployment

## Prerequisites

Before a customer sets up CI/CD, they should already have:

- admin access to the GitHub repository or an internal repo fork
- Azure rights to create Entra app registrations or user-assigned managed
  identities for GitHub OIDC
- Azure RBAC rights on the target subscription or resource groups
- a decision on whether `integration` will use:
  - an existing non-production Databricks workspace that the customer will
    populate, or
  - an explicit mock/seed path if they intentionally want that extra setup
- a decision on whether production points at:
  - an already-existing customer environment, or
  - a newly bootstrapped environment created by this repo

For the most common customer path, the customer already has:

- existing Azure resource group
- existing Container Apps environment
- existing planner and wrapper app registrations
- existing customer Databricks workspace
- existing AIQ and vPower-backed sources
- a separate plan for how the non-production Databricks workspace will be
  populated with validation data when integration tests depend on it

## Start Here: Choose Your Path

There are two customer setup paths in this repo.

### Path A: Customer Already Has Infrastructure

Choose this path when the customer already has:

- Azure resource group
- Container Apps environment
- planner and wrapper deployment targets
- app registrations
- existing Databricks workspace
- existing AIQ and vPower-backed data sources

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

Important current truth:

- routine deploy workflows are already GitHub-native and use ephemeral runner
  env files
- bootstrap is still more stateful, but the GitHub bootstrap workflow now uses
  temporary input/runtime files inside the runner instead of treating tracked
  `.env*` files as the automation source of truth

## Setup Checklist

Use this checklist for the shortest possible setup sequence:

1. choose Path A or Path B
2. create the GitHub Environments:
   - `integration`
   - `production`
   - `teams-catalog-admin`
   - `bootstrap-foundation`
3. create one Azure OIDC principal for `integration`
4. create one separate Azure OIDC principal for `production`
5. add GitHub federated credentials for those two environments
6. assign Azure RBAC to those OIDC principals
7. populate GitHub Environment variables and secrets
8. if starting from scratch, run `bootstrap-foundation.yml`
9. run a small PR through `dev`
10. promote the tested change to `integration`
11. verify deployed integration validation succeeds
12. promote to `main`
13. approve the `production` environment deployment

## First-Time Setup Order

### 1. Set Up GitHub

- create the target branches: `dev`, `integration`, `main`
- configure branch protection
- create GitHub Environments
- add required reviewers for `production`
- add required reviewers for `teams-catalog-admin`

### 2. Set Up Azure OIDC

- create the Azure principal for `integration`
- create the Azure principal for `production`
- add federated credentials that bind each principal to the matching GitHub
  Environment
- assign Azure RBAC

### 3. Move Runtime Configuration Into GitHub

- treat the old `mvp/.env.secure` as migration inventory only
- move non-secret values into GitHub Environment variables
- move secrets into GitHub Environment secrets or Azure Key Vault
- keep customer runtime Databricks settings in the `CUSTOMER_*` namespace for
  production until the code path is fully renamed

### 4. Validate Integration First

- merge a safe change into `integration`
- let CI build release metadata and images
- let `deploy-integration.yml` deploy the tested artifact
- confirm deployed validation succeeds before promoting to `main`

### 5. Promote To Production

- merge `integration` into `main`
- let `deploy-production.yml` reuse the tested release metadata
- approve the `production` GitHub Environment when prompted

## Step-By-Step Customer Setup

### 1. Copy The Repo And Choose The Branch Model

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

### 2. Inventory The Existing Manual `.env.secure`

Before configuring GitHub, freeze a copy of the existing manual secure env.

For example:

- current planner app IDs and audience
- current bot app IDs
- current Azure resource names
- current customer Databricks settings
- current table/view names
- current secrets and who owns them

Do not start by renaming everything. First map the old values into the new
CI/CD surfaces.

## Customer Setup Surface: Required Vs Generated Vs Optional

For a customer using an existing manually provisioned Azure and Databricks
environment, variables are easiest to understand in three classes:

1. customer-provided setup inputs
2. values generated by bootstrap or CI/CD
3. optional validation-only values

### 1. Customer-Provided Setup Inputs

These values describe the existing environment or existing app registrations.

Typical examples:

- Azure tenant, subscription, resource group, and region
- existing ACR name
- existing Container Apps environment name
- existing planner and wrapper app names
- existing planner app registration IDs and audience or scope
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
from `INFRA_NAME_PREFIX`.

### 3. Optional Validation-Only Values

These are helpful for automated validation, but they are not core environment
setup inputs.

Examples:

- `PLANNER_API_BEARER_TOKEN`
- `VALIDATE_USER_UPN`
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`
- `ENABLE_WRAPPER_HEALTHCHECK`
- `REQUIRE_AUTHENTICATED_E2E`

## 3. Create GitHub Environments

Create these GitHub Environments in the customer repo:

- `integration`
- `production`
- `teams-catalog-admin`
- `bootstrap-foundation`

Required production protection settings:

- required reviewers from the Infra team
- `Prevent self-review`
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
- scope Azure RBAC only to the required subscription or resource-group surface

Recommended split:

- integration identity can manage the integration environment only
- production identity can manage the production Azure resources only

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

Recommended subject examples:

- `repo:<owner>/<repo>:environment:integration`
- `repo:<owner>/<repo>:environment:production`

Use the helper scripts in this repo if you want repeatable setup:

- [`scripts/setup-github-oidc.sh`](scripts/setup-github-oidc.sh)
- [`scripts/setup-github-oidc.ps1`](scripts/setup-github-oidc.ps1)

## 5. Populate GitHub Environment Variables And Secrets

Move the old `.env.secure` values into GitHub Environments.

General rule:

- identifiers, names, and URLs -> GitHub Environment variables
- passwords, client secrets, bearer tokens -> GitHub Environment secrets or
  Key Vault

Important operating rule:

- workflow environment values are now the CI/CD source of truth
- the old manual `.env.secure` file becomes a migration reference only

### 5A. Absolute Minimum Inputs For A Customer Using An Existing Environment

If the customer is automating an already-existing manually provisioned secure
environment, the minimum meaningful production setup surface is:

#### Required Production Variables

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

#### Usually Required Depending On The Customer Environment

- `CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID`
- `CUSTOMER_DATABRICKS_OBO_SCOPE`
- `CUSTOMER_SCOPE_ACCOUNTS_CATALOG`
- `CUSTOMER_SALES_TEAM_MAPPING_CATALOG`
- `ACA_MIN_REPLICAS`
- `ACA_MAX_REPLICAS`

#### Required Production Secrets

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`

#### Optional Production Secrets

- `PLANNER_API_BEARER_TOKEN`
  - optional, advanced validation only
  - not a normal long-lived secret
  - only use this if the customer has a separate pre-run process that mints a
    fresh delegated Entra token for `PLANNER_API_SCOPE`

#### Optional Production Variables

- `VALIDATE_USER_UPN`
  - seller email or UPN used by the direct `sf_vpower_bronze` validation query
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`
  - validation flag for the direct Databricks-backed query path
- `WRAPPER_ENABLE_DEBUG_CHAT`
- `WRAPPER_DEBUG_ALLOWED_UPNS`
- `WRAPPER_DEBUG_EXPECTED_AUDIENCE`

### 5B. What Customers Do Not Need To Provide Manually

Customers should not manage these as manual GitHub setup values for normal
delivery:

- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`
- release metadata JSON fields
- temporary env files on runners
- deployment summary artifact contents

### 5C. From-Scratch Bootstrap: How Values Get Populated

For customers starting from zero infra, values are populated in three layers.

#### Layer 1: Small Operator-Owned Bootstrap Inputs

- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `INFRA_NAME_PREFIX`

#### Layer 2: Names Derived Automatically By Bootstrap

Bootstrap can derive defaults such as:

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

#### Layer 3: Values Generated Or Discovered During Provisioning

Bootstrap writes back discovered or generated values such as:

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

## 6. Decide How Secrets Will Be Resolved

Two supported patterns:

- GitHub Environment secrets only
- GitHub Environment secrets plus Key Vault fallback

If the runner cannot reach Key Vault, keep deploy-critical secrets directly in
GitHub Environment secrets.

## 7. Configure The Existing Customer Runtime Inputs

Production automation should point at the existing manually provisioned
environment:

- existing resource group
- existing Container Apps environment
- existing planner and wrapper app names
- existing customer Databricks host and warehouse
- existing AIQ table/view names
- existing customer vPower bronze catalog qualifiers when needed

Do not enable mock seed values in production.

## 8. Configure The Integration Environment

Integration should be safe to deploy repeatedly.

Recommended characteristics:

- separate non-production resource group
- separate non-production ACR
- separate Container Apps environment
- separate integration Databricks workspace or seeded mock path
- a known validation UPN

If the customer does not want to use a mock Databricks environment for
integration, replace the mock path with an existing non-production Databricks
workspace and populate the `integration` GitHub Environment with the same class
of customer runtime inputs used in production, but pointed at the
non-production workspace instead.

## Migration Guide For Older `.env.secure` Users

This repo evolved over time, and older manual secure deployments may not map
one-to-one with the current templates.

The safest migration approach is to group values by responsibility.

### A. Azure Platform And Runtime Names

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

### B. Planner And Wrapper Auth Settings

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

### C. Existing Customer Databricks Runtime Inputs

For customer-target production CI/CD, the repo still expects the
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

Use this rule:

- `CUSTOMER_DATABRICKS_*` = customer production runtime inputs
- `DATABRICKS_*` = foundation/mock/internal workspace inputs

### D. Scope And Territory Mapping Inputs

Normal hosted customer mode now defaults to built-in Databricks vPower queries.

That means these older static inputs are no longer normal required hosted
inputs:

- `CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH`
- `CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH`
- `CUSTOMER_SALES_TEAM_STATIC_MAP_JSON`

### E. Values That CI/CD Should Not Carry Forward From Old `.env.secure`

Do not treat these as GitHub-managed production inputs:

- `PLANNER_API_IMAGE`
- `WRAPPER_IMAGE`

These are release outputs, not operator-supplied inputs.

## Recommended Customer Migration Checklist

1. copy the old `.env.secure` into a temporary migration worksheet
2. classify each key as:
   - GitHub variable
   - GitHub secret
   - Key Vault secret
   - no longer required
3. keep `CUSTOMER_DATABRICKS_*` for the customer production runtime path
4. remove static scope and sales-team JSON from the normal hosted setup unless
   the customer explicitly needs a legacy fallback
5. preserve only `CUSTOMER_SCOPE_ACCOUNTS_CATALOG` and
   `CUSTOMER_SALES_TEAM_MAPPING_CATALOG` if the vPower bronze tables need a
   non-default catalog qualifier
6. validate a real seller UPN against the customer Databricks workspace before
   the first production rollout
7. test `integration` first, then `main`

## Suggested GitHub Environment Inventory

### Integration Variables

- Azure subscription, tenant, location, resource group
- ACR name
- Container Apps names
- planner/wrapper public IDs and audiences
- either customer non-production Databricks values or optional mock/seed
  Databricks values
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

### Integration Secrets

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- optional `PLANNER_API_BEARER_TOKEN`

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
- `ENABLE_CUSTOMER_VPOWER_QUERY_VALIDATION`

### Production Secrets

- `PLANNER_API_CLIENT_SECRET`
- `BOT_APP_PASSWORD`
- optional `PLANNER_API_BEARER_TOKEN`

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
10. copy values from the customer's old `.env.secure` into the matching GitHub
    variables and secrets
11. for production, keep the customer Databricks connection values in the
    `CUSTOMER_*` namespace
12. run a small PR through `dev`
13. promote to `integration`
14. validate that `deploy-integration.yml` works against either the customer's
    non-production Databricks environment or an intentionally provisioned
    mock/seed path
15. promote to `main`
16. confirm the production environment pauses for approval
17. approve and complete the first production release

## Final Guidance

If a customer is coming from an older version of this repo and a manually
maintained `mvp/.env.secure`, the most important migration rule is:

- do not start by rewriting env names
- first identify which values are still runtime inputs, which are now CI
  outputs, and which old static JSON inputs are no longer part of the normal
  hosted customer path
