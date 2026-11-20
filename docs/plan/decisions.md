# Decisions register

> Read this when: you are about to add any library, framework, tool or service, or you need to know what is already approved. This file is the single source of truth for decision status.

Every decision starts **Open**. Nothing is installed until it is **Approved** and has a record in `docs/decisions/`. "Lean" is Claude's suggestion for you to challenge, not a choice.

Read each row in this order: what breaks without a solution, then the options, then the lean.

## Approved

| # | Decision | Chosen | Why |
| --- | --- | --- | --- |
| D19 | App shell | Native Swift: SwiftUI inside a floating macOS panel | Smoothest, fastest, most polished feel. Real notch-level placement is native on macOS. Tauri needed a community plugin for it and could stutter when the window resizes. |
| D22 | App signing | Unsigned for now, with build-from-source docs | Signing needs a paid Apple Developer account. Revisit when real users arrive. |
| D10 | Frontend stack | Replaced by D19. islandcn is the design reference only | React cannot run inside a native Swift app. |
| D1 | Backend language | Python (3.12 in the container) | San knows it; strongest data and AI libraries. See `docs/decisions/D01-backend-language.md`. |
| D13 | Packaging | Docker | Same image on any Mac, in CI and in Azure. See `docs/decisions/D13-packaging.md`. |
| D14 | CI/CD | GitHub Actions | Code is on GitHub; free Linux and macOS runners. Uses actions from GitHub, the uv team and Docker. See `docs/decisions/D14-ci.md`. |
| D17 | Licence | MIT | Short and well known. See `docs/decisions/D17-licence.md`. |
| D2 | Web framework | FastAPI on Uvicorn | Input checks from type hints, generated docs, async. See `docs/decisions/D02-web-framework.md`. |
| D24 | Python and dependency manager | uv with `uv.lock` | Same Python and exact library versions everywhere. See `docs/decisions/D24-python-env.md`. |
| D25 | Test runner | pytest (+ httpx2 for TestClient) | Plain asserts, clear failures, fixtures. Dev only. See `docs/decisions/D25-test-runner.md`. |
| D26 | Lint and format | ruff | One tool for lint and format, enforced in CI. Dev only. See `docs/decisions/D26-lint-format.md`. |
| D3 | Main database | Postgres 18 with pgvector | Many writers, SKIP LOCKED queue, vectors, managed in Azure. See `docs/decisions/D03-database.md`. |
| D27 | Postgres driver | psycopg 3 + psycopg_pool | Plain SQL, safe parameters, pooling. See `docs/decisions/D27-postgres-driver.md`. |
| D4 | Schema changes | Own runner, numbered SQL files | No dependency; shows how migrations work. See `docs/decisions/D04-migrations.md`. |
| D18 | How others run it | Docker Compose | One command, health-gated order, data on a volume. See `docs/decisions/D18-compose.md`. |
| D29 | Branch flow and hotfixes | Trunk-based: one `main`, PR required (0 approvals) with CI checks, build once and promote; hotfixes fix forward through DEV | The image tested in DEV is the image that ships; no merge-backs. See `docs/decisions/D29-branch-flow.md`. |
| D28 | Raw landing for links | Land every fetch, gzipped and append-only, with a `fetches` record; extract only from the landed file; raw and text hashes decide what to store and redo | Re-process offline after a parser bug or a dead page; no new dependency. Needs D5. See `docs/decisions/D28-raw-landing.md`. |

## Open

| # | Decision | Problem it solves | Options to compare | Lean and why | Needed by |
| --- | --- | --- | --- | --- | --- |
| D5 | File storage | Large files do not belong in database rows | Local folder, object storage (Azure Blob) | Local folder behind one small interface; Blob in M7. | M2 |
| D6 | Background work | Slow processing must not block "Saved" | Work inside the request, a jobs table in Postgres, a broker (Redis, Azure Service Bus) | Jobs table in Postgres. No new service, and it teaches queues from the inside. | M3 |
| D7 | Content extraction | Turn web pages, PDFs and images into text | Reuse the RAG Galaxy parser, an HTML reader, OCR, a vision model | Reuse what you built. Add one source type at a time. | M3 |
| D8 | Embeddings and LLM | Search by meaning; answers and tags | Hosted model API (owner's key), a local model | Hosted API with the owner's key; a local model as an option later. | M3 |
| D20 | Reranking | The first search returns close but not best chunks | None, a cross-encoder model, ask the LLM to rank | None first. Add one only if the eval set shows a gain. | M4 |
| D9 | Agent loop | The model must call search and read items | Hand-written tool loop, an agent framework | Hand-written loop. About 100 lines, and you see every step. | M5 |
| D21 | Chat history | A chat about a citation needs memory of earlier turns | Tables in Postgres, keep it in the app only | Postgres tables. Same backups, and you can query it. | M5 |
| D11 | Live status | Push "Uploading 40%" to the app | Polling, Server-Sent Events, WebSockets | Polling first, then Server-Sent Events. One-way push is enough. | M6 |
| D23 | Global shortcut | Open the panel from any app | macOS hot key API, a key event monitor (needs Accessibility permission) | Hot key API. No extra permission prompt. | M6 |
| D12 | Who is the user | Only the owner reaches the API | A local token in the settings file, a hosted identity provider | Local token. One owner per copy. | M9 |
| D15 | Cloud and IaC | Repeatable cloud setup for your demo | Terraform on Azure, Bicep | Terraform on Azure. Matches the CarbonAI role. | M7 |
| D16 | Observability | See errors, speed and cost | Structured logs, OpenTelemetry, Application Insights | Structured logs first, then OpenTelemetry into Application Insights. | M8 |


To record a new decision, copy `docs/decisions/TEMPLATE.md`.
