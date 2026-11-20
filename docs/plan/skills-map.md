# Skills map

> Read this when: you write the README, a portfolio note, or prepare to explain the project to an employer.

| Area | You can say | Proof in the repo |
| --- | --- | --- |
| API design | "I designed a small REST API with clear errors and limits." | Endpoint tests, M1 |
| Data modelling | "I designed the schema and changed it safely with migrations." | `migrations/`, schema diagram |
| Async processing | "Saving is fast because slow work goes through a queue with retries and no duplicates." | Kill-the-worker test, M3 |
| Search and RAG | "Hybrid search with citations, measured with a golden set." | `docs/results.md`, M4 and M5 |
| Containers | "One command runs the whole backend on any Mac." | `docker-compose.yml` |
| CI/CD | "Every push is tested; every merge deploys." | CI workflows, M0 and M7 |
| IaC | "All cloud parts are in Terraform and can be rebuilt." | `infra/` |
| Observability | "I set an SLO, and alerts tell me when we miss it." | Dashboard, runbook, M8 |
| Security and cost | "Secrets in a vault, least-privilege identity, cost caps." | Key Vault, budget alert, rate limits |
| Decision making | "Every tool in the project has a written reason." | `docs/decisions/` |

## Data collection choices

One line per choice, to say in an interview. The full explanation is in `docs/learning/concepts.md`.

- **Raw landing (D28):** "Workers land every fetched page, gzipped and append-only, with a fetch record before parsing, so a parser bug or a dead page never costs me the source; a raw hash skips storing identical bytes and a text hash skips re-embedding unchanged articles."
