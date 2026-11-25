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
| D28 | Raw landing for links | Land every fetch, gzipped and append-only, with a `fetches` record; extract only from the landed file; raw and text hashes decide what to store and redo | Re-process offline after a parser bug or a dead page; no new dependency. Uses D5. See `docs/decisions/D28-raw-landing.md`. |
| D5 | File storage | Local folder behind a `Storage` interface (`STORAGE_BACKEND=local`, a Compose volume); Azure Blob adapter in P6 | Users need no Azure account; a container's disk is wiped on redeploy, so cloud files live in Blob. No new dependency now. See `docs/decisions/D05-file-storage.md`. |
| D7 | Content extraction (web pages) | BeautifulSoup on the built-in `html.parser`: `<main>`, else `<article>`, else `<body>`; drop page furniture; collapse whitespace before hashing | San can explain every rule, and so every version a page gets. PDFs and images are decided in P2 and M2. See `docs/decisions/D07-content-extraction.md`. |
| D30 | Idempotent jobs | At-least-once: work first, mark done last; replace the item's chunk set (DELETE then INSERT) and mark the job done in one transaction | A crash repeats work, never loses it; no duplicates and no orphan chunks. See `docs/decisions/D30-idempotent-jobs.md`. |
| D31 | Business key for links | Normalised URL (drop fragment and named tracking parameters; keep trailing slash), partial unique index, 409 on a repeat save | One item per page, even under racing saves; a false split beats a silent false merge. See `docs/decisions/D31-link-business-key.md`. |
| D32 | Page history | `item_versions` with `valid_from` / `valid_to`; a new version only when the text hash changes; rebuilt from raw when the extractor changes | "What did it say on 1 November?" is one query. See `docs/decisions/D32-page-history.md`. |
| D33 | Fetch client | `httpx2`, moved from dev to runtime | Already in the lockfile; mock transport for tests; redirect history for D28. See `docs/decisions/D33-fetch-client.md`. |
| D34 | File uploads | SHA-256 of the bytes is the identity and the key (`files/<sha256>`), partial unique index, 409 on a repeat upload; raw request body; 25 MB, PDF/PNG/JPEG decided by magic bytes (413, 415); delete rows, then the file | Names and sizes lie, a client can claim any type, and a raw body needs no new dependency. See `docs/decisions/D34-file-uploads.md`. |
| D6 | Background work | `jobs` table in Postgres, a status per job (queued, running, done, failed, dead), claimed with `FOR UPDATE SKIP LOCKED` and a lease; P3 chunks only, embedding waits for D8 | No new service; each job fails and retries on its own; "done" commits with the chunks (D30). See `docs/decisions/D06-background-work.md`. |
| D35 | Retries | Retry only timeouts, connection errors, 429 and 5xx; never other 4xx; 3 tries, waits of 1 then 5 minutes; then `dead` | Asking again only helps when the answer can change. See `docs/decisions/D35-retries.md`. |
| D36 | Chunking | Paragraphs packed to about 1,000 characters, long paragraphs cut at sentence ends, no overlap; each chunk keeps its index and character positions | Whole ideas stay together, the rule is explainable, and no tokenizer is needed before D8. See `docs/decisions/D36-chunking.md`. |
| D7b | Content extraction (PDFs, images) | PDFs: `pypdf`, page by page; chunks never cross a page. Images: a description from Claude (D8), including visible text, stored as a description. Text kept in `file_texts`, rebuildable from the file | BSD and pure Python, gives pages for citations; PyMuPDF is AGPL, pdfplumber pulls in more than we use; OCR can't describe a photo. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D07b-file-extraction.md`. |
| D8 | Embeddings and LLM | OpenAI `text-embedding-3-small` (1,024 dimensions via `dimensions`) over plain `httpx2`, Voyage AI `voyage-4` selectable (`EMBEDDING_PROVIDER=voyage`); Claude (`claude-opus-5-5`) through the `anthropic` SDK; all behind small interfaces; keys optional, from the environment | OpenAI (San): San already has an OpenAI key with credits, so no new account or second paid provider. One endpoint needs no SDK; the Claude SDK gives typed errors, retries and tool-use types. Approved 2026-11-25: LLM part by Claude under San's delegation (San to review); embeddings provider changed by San the same day (first choice: Voyage). See `docs/decisions/D08-models.md`. |
| D37 | Hybrid search | Postgres full-text search (generated `tsvector`, GIN, `websearch_to_tsquery`: all words first, then any word, ranked by `ts_rank`; quotes or `-word` mean exact) plus pgvector cosine distance (HNSW), merged by Reciprocal Rank Fusion (`1 / (60 + rank)`); filters are WHERE clauses on both lists before their LIMIT; exact vector scan under a filter | Exact words (IDs, codes) and paraphrases both found; RRF needs only positions, so nothing to normalise or tune; no new service, same backups. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D37-hybrid-search.md`. |
| D20 | Reranking | None for now | A reranker is a second model call per search and a new dependency or API; the eval shows whether it would pay. Switch if recall@5 is high but MRR is low. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D20-reranking.md`. |
| D9 | Agent loop | Hand-written loop on the Messages API with the `anthropic` SDK: two tools (`search`, `get_item`) run by our code against Postgres, several calls per turn answered in one message, tool errors as `is_error` results, a turn limit (`AGENT_MAX_TURNS`, 6) | About 100 lines San can explain; the tools run next to the local database; the turn limit and the cost cap sit in our code where tests prove them. Rejected: Tool Runner (hides the loop, beta), Managed Agents (hosted sandbox we don't need), a framework. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D09-agent-loop.md`. |
| D21 | Chat history | `conversations` and `messages` tables in Postgres; each turn keeps the exact API content blocks (JSON) plus display text and citations; history replayed unchanged and append-only | Same backups, queryable; current Claude models need thinking blocks replayed unchanged; survives an app restart. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D21-chat-history.md`. |
| D38 | Citations and grounding | The model writes `[c:<chunk id>]` after each claim; code keeps only chunks a tool returned in this conversation, removes and reports the rest; no valid citation means `grounded: false`; each citation stored as a snapshot (item, page, positions, quote) | A check in code can be tested and can't be talked out of; chunk ids already map to item, page and position (D36). Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D38-citations.md`. |
| D39 | Cost cap and rate limit | Every model call writes a `model_usage` row (tokens, estimated cost from a price table in settings); before an ask, today's UTC total vs `DAILY_COST_CAP_USD` (2.00) and questions in the last 60 s vs `ASK_RATE_PER_MINUTE` (10); 429 with a reason (and `Retry-After`) | The owner pays for their own key; a loop bug or stuck client must not run up a bill; no new service, cost per day is one query. Approved 2026-11-25 by Claude under San's delegation; San to review. See `docs/decisions/D39-cost-cap.md`. |

## Open

| # | Decision | Problem it solves | Options to compare | Lean and why | Needed by |
| --- | --- | --- | --- | --- | --- |
| D11 | Live status | Push "Uploading 40%" to the app | Polling, Server-Sent Events, WebSockets | Polling first, then Server-Sent Events. One-way push is enough. | M6 |
| D23 | Global shortcut | Open the panel from any app | macOS hot key API, a key event monitor (needs Accessibility permission) | Hot key API. No extra permission prompt. | M6 |
| D12 | Who is the user | Only the owner reaches the API | A local token in the settings file, a hosted identity provider | Local token. One owner per copy. | M9 |
| D15 | Cloud and IaC | Repeatable cloud setup for your demo | Terraform on Azure, Bicep | Terraform on Azure. Matches the CarbonAI role. | M7 |
| D16 | Observability | See errors, speed and cost | Structured logs, OpenTelemetry, Application Insights | Structured logs first, then OpenTelemetry into Application Insights. | M8 |


To record a new decision, copy `docs/decisions/TEMPLATE.md`.
