# Mind Dump

[![CI](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml)

An open-source "second brain" for macOS. Drop links, files, images and notes on a black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

> Status: Milestone 4. Press + on the panel to save the link on your clipboard, or a note; upload PDFs and images through the API. A worker reads, chunks and (with your key) embeds each item in the background. Search finds passages by exact words, by meaning, or both, with filters; an eval measures how well.

## Run it

You need Docker Desktop and Xcode.

```bash
# 1. Settings: copy the example and set a database password
cp .env.example .env
sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
#    Optional: add VOYAGE_API_KEY and ANTHROPIC_API_KEY to .env (see "AI model keys" below)

# 2. Backend: Postgres, then migrations, then the API and the worker, in order
docker compose up -d --wait api worker
curl -i http://localhost:8000/health          # {"status":"ok"}
open http://localhost:8000/docs               # generated API docs

# 3. Mac app: the notch panel (green dot = backend alive, + = save)
xcodebuild -project app/MindDump.xcodeproj -scheme MindDump -derivedDataPath app/build/dd build
open app/build/dd/Build/Products/Debug/MindDump.app
```

### Fetch saved links and see their history

The worker fetches saved links by itself (P3). This command fetches them now, in the terminal. Each fetch lands the raw page, records it, and opens a new version only when the article text changes.

```bash
docker compose exec api python -m mind_dump.refetch --all     # fetch every saved link now
curl -s localhost:8000/items/1/versions                       # every text the page has shown
curl -s "localhost:8000/items/1?as_of=2026-11-01"             # what it said on 1 November
docker compose exec api python -m mind_dump.refetch --all --rebuild   # re-extract from landed files, no network
```

Upload a PDF, PNG or JPEG (at most 25 MB) as the raw request body. The same bytes twice give a 409 that points at the first copy, whatever the file is called. The worker reads a PDF's text page by page; an image gets a description from Claude (it needs `ANTHROPIC_API_KEY`).

```bash
curl -si --data-binary @file.pdf -H 'Content-Disposition: attachment; filename="file.pdf"' localhost:8000/items/upload
curl -s localhost:8000/items/1                 # status: queued, processing, ready or error
curl -s localhost:8000/items/1/text            # the file's text, with its pages
curl -s localhost:8000/items/1/chunks          # passages with page numbers, and whether each has a vector
```

### Search

`GET /search` finds passages (chunks), not just items (D37). Each hit carries its item, its place in the text (`start_char`, `end_char`, and `page` for PDFs) and how it was found: its rank in the keyword list, its rank in the vector list, and the merged score.

```bash
curl -s "localhost:8000/search?q=buffer+pool"                          # hybrid: words and meaning, merged
curl -s "localhost:8000/search?q=%22buffer+pool%22+-fire&mode=keyword" # exact phrase, without "fire"
curl -s "localhost:8000/search?q=woodland+catches+fire&mode=vector"    # by meaning (needs VOYAGE_API_KEY)
curl -s "localhost:8000/search?q=registry&type=link&from=2026-11-01&to=2026-11-30&tag=carbon&limit=5"
curl -s "localhost:8000/search?q=leakage&item_id=7"                    # one item's passages
curl -s -X PUT localhost:8000/items/7/tags -H 'content-type: application/json' \
  -d '{"tags": ["Carbon Markets", "MRV"]}'                              # replace the tags ([] clears)
```

- `mode`: `hybrid` (default), `keyword` or `vector`. The response's `mode` says what really ran.
- Keywords: chunks with all your words come first, then chunks with any of them (words are stemmed, so "retries" finds "retry"). `"quotes"` for a phrase or `-word` to exclude switch to exact matching: every word must appear. `or` means either.
- Filters: `type` (note, link, file), `from` and `to` (a date means the whole day, UTC, or a full time), `tag`, `item_id`. They narrow both lists before ranking, so a filter never hides a match.
- Without `VOYAGE_API_KEY`, or when Voyage is failing, hybrid returns keyword results with `"degraded": true` and a `notice`; `mode=vector` answers 503 with the reason.
- Tags are normalised one way: trimmed, inner spaces squeezed to one, lowercased. So `?tag=carbon markets` finds an item tagged `Carbon  Markets`.

### Measure search

The eval loads 12 original documents (`backend/eval/corpus/`) through the real pipeline into its own database (`mind_dump_eval`, created if missing; it refuses any database not named `*_eval` or `*_test`), asks 20 golden questions (`backend/eval/golden.json`), and prints recall@5 and MRR@10 per mode, with every miss. It writes `docs/results.md`. Run it from a checkout, with the Compose database up:

```bash
cd backend
uv run python -m mind_dump.eval                 # keyword only without a key; all three modes with one
uv run python -m mind_dump.eval --no-results    # print only
```

It reads `VOYAGE_API_KEY` from the environment or from `.env`; with a key, the 30 chunks and 20 questions are embedded (a few thousand tokens). CI runs it in keyword mode and fails if keyword recall@5 drops below its floor.

### AI model keys (optional)

Each owner uses their own keys (D8). Both are optional:

| Key | Get it at | Without it |
| --- | --- | --- |
| `VOYAGE_API_KEY` | dashboard.voyageai.com | Everything is saved, read and chunked; chunks have no vectors, so search finds exact words only (hybrid falls back to keyword results). |
| `ANTHROPIC_API_KEY` | platform.claude.com | Images end in `error` (there is nothing to read without a description); notes, links and PDFs work. |

Put them in `.env`, then:

```bash
docker compose up -d worker api                                   # pick up the new settings
docker compose exec worker python -m mind_dump.check_models       # OK per provider, or the reason
docker compose exec worker python -m mind_dump.embed_missing      # embed what was saved before
```

Run `embed_missing` again after changing `EMBEDDING_MODEL`: vectors from two models can't be compared. When keys are set, item text goes to Voyage AI and images go to Anthropic.

Right-click the panel to quit. The app is unsigned (see `docs/decisions/`), so build it from source.

`docker compose down` stops everything and keeps your data (the database and the landed pages, in two volumes). `docker compose down -v` deletes the data too.

## Develop the backend

The backend uses [uv](https://docs.astral.sh/uv/) for Python 3.12 and locked dependencies.

```bash
cd backend
uv sync                                       # install exactly what uv.lock says
docker compose up -d db                       # tests need Postgres (they use mind_dump_test)
uv run pytest                                 # tests
uv run python -m mind_dump.migrate            # apply new migrations (needs DATABASE_URL)
uv run ruff check . && uv run ruff format .   # lint and format
```

## Layout

| Path | What lives there |
| --- | --- |
| `backend/` | API (FastAPI) and, from M3, workers. `Dockerfile`, tests. `eval/`: the search eval's corpus and golden questions (M4). |
| `app/` | Native macOS app (SwiftUI in an `NSPanel`). |
| `.github/workflows/` | CI: lint, test, image build and smoke test; Xcode build. |
| `docs/plan/` | The plan and milestones. |
| `docs/decisions/` | One record per approved decision, with the reasons. |
| `docs/results.md` | The latest search eval numbers (M4). |

## Licence

MIT. See `LICENSE`.
