# M0: Skeleton, tests, CI

> Read this when: you are working on M0, or need its tasks and done-when list.

**Goal:** an empty but real project. Every push is tested and built. The notch panel appears on screen and shows if the backend is alive.
**Decisions needed first:** D1, D13, D14, D17 (then D2, D24, D25, D26).
**Concepts you learn:** Git flow, HTTP basics, containers, CI pipelines, native window levels.

Step by step on your Mac:

1. **Clone the repo.**
   ```bash
   cd ~/Documents
   git clone https://github.com/sxnfer/mind-dump.git
   cd mind-dump
   git switch -c m0-skeleton
   ```
2. **Make the decisions.** D1, D13, D14, D17, plus D2 (FastAPI), D24 (uv), D25 (pytest), D26 (ruff), added after the explainer. Records are in `docs/decisions/`.
3. **Add `.gitignore` and `.env.example`.** `.gitignore` must list `.env`. `.env.example` lists setting names with empty values.
4. **Write `/health` in FastAPI** (`backend/src/mind_dump/main.py`). It returns `{"status": "ok"}`. We skipped the standard library version; the explainer artifact shows the plumbing it would need.
   ```bash
   cd backend && uv sync && uv run uvicorn mind_dump.main:app --reload
   curl -i http://localhost:8000/health
   ```
5. **Write tests with pytest** (`backend/tests/`). `TestClient` calls the app in-process: 200 on `/health`, 404 on an unknown path, 405 on the wrong method.
6. **Write a two-stage `Dockerfile`.** A builder stage runs `uv sync --frozen`; the runtime stage holds only the virtual environment, runs as a non-root user, and has a `HEALTHCHECK`.
   ```bash
   docker build -t mind-dump-api ./backend
   docker run --rm -p 8000:8000 mind-dump-api
   ```
7. **The Mac app** (`app/`). A borderless, non-activating `NSPanel` above the main menu level, with `.canJoinAllSpaces` and `.fullScreenAuxiliary`, placed at the notch (`NotchPanel.swift`). A dot polls `GET http://127.0.0.1:8000/health` every 5 s with a 2 s timeout (`HealthMonitor.swift`). The sandbox has Outgoing Connections (Client) on (`MindDump.entitlements`). Apple frameworks only.
8. **The CI workflow** (`.github/workflows/ci.yml`):
   - `backend` on Ubuntu: `uv sync --frozen`, ruff check and format, pytest, Docker build (layer cache), then a smoke test that runs the container and curls `/health`.
   - `app` on macOS: unsigned `xcodebuild` (D22).
9. **Turn on secret protection and branch protection.** In the repo on GitHub: Settings > Code security > turn on secret scanning and push protection. Settings > Branches > add a rule for `main` that requires the `backend` and `app` checks to pass.
10. **Push and open a PR.**
    ```bash
    git add .
    git commit -m "M0: FastAPI health endpoint, Docker image, notch panel, CI"
    git push -u origin m0-skeleton
    ```
    Merge only when both CI jobs are green.

**Done when:**
- [x] CI is green on the PR, both jobs.
- [ ] The black panel shows at the notch, over a fullscreen app, on every Space.
- [ ] The dot is green with the container running. It turns red within 5 s when you stop the container, and within 7 s when the backend hangs (check interval 5 s + timeout 2 s).
- [x] Eight decision records are in `docs/decisions/` (D1, D2, D13, D14, D17, D24, D25, D26).

**Proof:** CI badge in the README, a short screen recording of the panel over a fullscreen app.
