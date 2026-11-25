# Mind Dump

[![CI](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml)

An open-source "second brain" for macOS. Drop links, files, images and notes on a black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

> Status: Milestone 3. Press + on the panel to save the link on your clipboard, or a note; upload PDFs and images through the API. A worker reads, chunks and (with your key) embeds each item in the background. Search arrives in Milestone 4.

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

### AI model keys (optional)

Each owner uses their own keys (D8). Both are optional:

| Key | Get it at | Without it |
| --- | --- | --- |
| `VOYAGE_API_KEY` | dashboard.voyageai.com | Everything is saved, read and chunked; chunks have no vectors, so no search by meaning yet. |
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
| `backend/` | API (FastAPI) and, from M3, workers. `Dockerfile`, tests. |
| `app/` | Native macOS app (SwiftUI in an `NSPanel`). |
| `.github/workflows/` | CI: lint, test, image build and smoke test; Xcode build. |
| `docs/plan/` | The plan and milestones. |
| `docs/decisions/` | One record per approved decision, with the reasons. |

## Licence

MIT. See `LICENSE`.
