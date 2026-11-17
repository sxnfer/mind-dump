# D18: How others run it

Status: Approved, 2026-11-17
Problem: Postgres, then migrations, then the API must start in order, with the right network, ports, volume and secrets, by one command a stranger can type.
Without a tool: several fragile `docker run` commands plus a hand-written wait loop.
Options: Docker Compose; a shell script; a cloud template.
Chosen: Docker Compose (`docker-compose.yml` at the repo root).
Why: comes with Docker Desktop; `depends_on` with `service_healthy` and `service_completed_successfully` gates the order; a named volume keeps data; reads `.env` for secrets. San's Azure deploy (M7) is the cloud proof.
Cost: Compose-specific YAML; on Azure we describe the same services again in Terraform.
We will switch if: we need more than one machine. Then a real orchestrator, which is out of scope.
