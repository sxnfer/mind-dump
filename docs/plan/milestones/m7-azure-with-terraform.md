# M7: Azure with Terraform

> Read this when: you are working on M7, or need its tasks and done-when list.

**Goal:** your own demo copy of the backend runs on Azure, deployed from CI.
**Decisions needed:** D15.
**Tasks:**
- Terraform for: resource group, budget alert, container registry, Container App (API), Container App Job (workers), Postgres Flexible Server with pgvector, Blob storage, Key Vault, managed identity. Reuse what you built for RAG Galaxy.
- Blob storage behind the same `Storage` interface from M2.
- Two environments: staging and production.
- CI: `terraform plan` on each PR; deploy on merge to `main`; images tagged by commit.
- Test a rollback to the previous image.
**Concepts:** IaC, environments, secrets, managed identity, continuous delivery, rollback.
**Done when:** a merge to `main` deploys to staging with no manual step; `terraform destroy` removes everything.
