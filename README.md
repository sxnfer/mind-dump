# Mind Dump

[![CI](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml)

An open-source "second brain" for macOS. Drop links, files, images and notes on a black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

> Status: Milestone 1. Press + on the panel to save the link on your clipboard, or a note. Items live in Postgres and survive restarts. Nothing is processed or searchable yet.

## Run it

You need Docker Desktop and Xcode.

```bash
# 1. Settings: copy the example and set a database password
cp .env.example .env
sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env

# 2. Backend: Postgres, then migrations, then the API, in order
docker compose up -d --wait api
curl -i http://localhost:8000/health          # {"status":"ok"}
open http://localhost:8000/docs               # generated API docs

# 3. Mac app: the notch panel (green dot = backend alive, + = save)
xcodebuild -project app/MindDump.xcodeproj -scheme MindDump -derivedDataPath app/build/dd build
open app/build/dd/Build/Products/Debug/MindDump.app
```

Right-click the panel to quit. The app is unsigned (see `docs/decisions/`), so build it from source.

`docker compose down` stops everything and keeps your data. `docker compose down -v` deletes the data too.

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
