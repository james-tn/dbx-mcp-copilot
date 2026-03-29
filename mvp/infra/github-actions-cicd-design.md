# GitHub Actions CI/CD Docs Index

## Purpose

The original CI/CD guide grew large because it was covering architecture,
customer setup, migration, and operational validation in one file.

That content is now split into focused documents:

- [`cicd-overview.md`](cicd-overview.md)
  - conceptual architecture
  - branch and environment model
  - workflow responsibilities
  - Azure OIDC, RBAC, and team collaboration model
- [`cicd-setup-guide.md`](cicd-setup-guide.md)
  - step-by-step technical setup
  - GitHub Environments
  - Azure OIDC identities
  - required variables and secrets
  - migration from older `.env.secure` deployments
- [`cicd-validation-and-operations.md`](cicd-validation-and-operations.md)
  - end-to-end drills
  - rollout validation
  - evidence capture
  - operational guardrails

## Recommended Reading Order

For a new customer or internal operator:

1. read [`cicd-overview.md`](cicd-overview.md)
2. implement using [`cicd-setup-guide.md`](cicd-setup-guide.md)
3. validate using
   [`cicd-validation-and-operations.md`](cicd-validation-and-operations.md)

## Related Docs

- Infra index: [`README.md`](README.md)
- Operator runbook:
  [`../mvp-setup-and-deployment-runbook.md`](../mvp-setup-and-deployment-runbook.md)
- Runtime architecture:
  [`../daily-account-planner-architecture.md`](../daily-account-planner-architecture.md)
