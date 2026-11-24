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
- **Idempotent jobs (D30):** "Jobs run at least once, so every job is safe to run twice: the worker replaces an item's whole chunk set and marks the job done in one transaction, so a crash repeats work instead of losing it, and a shorter re-chunk leaves no orphans."
- **Business key (D31):** "A link's business key is its normalised URL, enforced by a unique index, so racing saves can't both win; I strip only what can't change the page, because a false merge silently loses a page while a false split is visible and fixable."
- **File uploads (D34):** "A file's identity is the SHA-256 of its bytes and also its storage key, so the same file under two names is one item and one copy, enforced by a unique index; I judge the type by the file's first bytes, never the client's claim, and cap uploads at 25 MB while reading, because the length header can lie."
- **Page history (D32):** "I keep versions with valid-from and valid-to, starting a new one only when the article text changes, so 'what did it say on 1 November?' is one query; versions are rebuilt from the raw files when the extractor improves."
