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

## Open

| # | Decision | Problem it solves | Options to compare | Lean and why | Needed by |
| --- | --- | --- | --- | --- | --- |
| D1 | Backend language | One language for API and workers | Python, TypeScript, Swift on the server | Python. You know it, and the data tools are strong. | M0 |
| D13 | Packaging | Same backend on any Mac and in the cloud | Docker | Docker. You already know it. | M0 |
| D14 | CI/CD | Test and build on every push | GitHub Actions, others | GitHub Actions. The code is on GitHub, and public repos get free minutes. | M0 |
| D17 | Licence | People need to know what they may do with the code | MIT, Apache-2.0, AGPL-3.0 | MIT or Apache-2.0. Short and well known; Apache adds a patent grant. | M0 |
| D2 | Web framework | Routing, input checks, API docs | Python standard library server, FastAPI, Flask | Write the raw standard library version first (M0), then decide in M1. | M1 |
| D3 | Main database | Store items, tags, status, vectors | Postgres (with pgvector), SQLite | Postgres. One store for rows and vectors, same as RAG Galaxy. | M1 |
| D4 | Schema changes | Change tables safely as the app grows | Numbered SQL files run by a small script, a migration tool | Numbered SQL files first. Feel the pain, then pick a tool. | M1 |
| D18 | How others run it | A stranger must start it in minutes, on their own bill | `docker compose up` on their Mac, a cloud template | Docker Compose. Your Azure deploy is your own proof. | M1 |
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
