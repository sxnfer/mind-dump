# Mind Dump

[![CI](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml)

An open-source "second brain" for macOS. Drop links, files, images and notes on a black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

> Status: Milestone 0, the skeleton. The panel shows whether the backend is alive. Nothing is saved yet.

## Run it

You need Docker Desktop and Xcode.

```bash
# Backend: build and run the API container
docker build -t mind-dump-api ./backend
docker run --rm -p 8000:8000 mind-dump-api
curl -i http://localhost:8000/health          # {"status":"ok"}
open http://localhost:8000/docs               # generated API docs

# Mac app: build and open the notch panel (green dot = backend alive)
xcodebuild -project app/MindDump.xcodeproj -scheme MindDump -derivedDataPath app/build/dd build
open app/build/dd/Build/Products/Debug/MindDump.app
```

Right-click the panel to quit. The app is unsigned (see `docs/decisions/`), so build it from source.

## Develop the backend

The backend uses [uv](https://docs.astral.sh/uv/) for Python 3.12 and locked dependencies.

```bash
cd backend
uv sync                                       # install exactly what uv.lock says
uv run uvicorn mind_dump.main:app --reload    # run without Docker, reloads on save
uv run pytest                                 # tests
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
