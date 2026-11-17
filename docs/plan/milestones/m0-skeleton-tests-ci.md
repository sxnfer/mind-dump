# M0: Skeleton, tests, CI

> Read this when: you are working on M0, or need its tasks and done-when list.

**Goal:** an empty but real project. Every push is tested and built. The notch panel appears on screen and shows if the backend is alive.
**Decisions needed first:** D1, D13, D14, D17.
**Concepts you learn:** Git flow, HTTP basics, containers, CI pipelines, native window levels.

Step by step on your Mac:

1. **Clone the repo.**
   ```bash
   cd ~/Documents
   git clone https://github.com/sxnfer/mind-dump.git
   cd mind-dump
   git switch -c m0-skeleton
   ```
2. **Make the four decisions.** Write `docs/decisions/D01-backend-language.md`, `D13-packaging.md`, `D14-ci.md`, `D17-licence.md` from the template. Add the `LICENSE` file for your D17 choice.
3. **Add `.gitignore` and `.env.example`.** `.gitignore` must list `.env`. `.env.example` lists setting names with empty values.
4. **Write a `/health` endpoint with no framework.** If D1 is Python, use only the standard library (`http.server`). It returns `{"status": "ok"}`. Check it:
   ```bash
   curl -i http://localhost:8000/health
   ```
   Notice what you had to write by hand: routing, JSON, status codes. That pain is the input to D2.
5. **Write one test with the standard library** (`unittest` in Python). It starts the server and checks that `/health` returns 200.
6. **Write a `Dockerfile` for the backend.** Build and run it:
   ```bash
   docker build -t mind-dump-api ./backend
   docker run --rm -p 8000:8000 mind-dump-api
   ```
7. **Make the Mac app.** In Xcode: File > New > Project > macOS > App. Name `MindDump`, interface SwiftUI, save it in `app/`. Then build the notch panel with Apple's own frameworks (no third-party code):
   - a borderless, non-activating `NSPanel` (it does not steal focus from the app you are typing in),
   - background `.black`, no shadow, no title bar,
   - window level above the menu bar, so it can sit at the notch,
   - collection behaviour that joins all Spaces and shows over fullscreen apps,
   - placed top centre on the screen with the notch.
   Inside it, one small dot: green when `GET http://localhost:8000/health` answers, red when it does not. Check it every 5 seconds.
   Watch out: a sandboxed Mac app cannot make network calls until you turn on **Outgoing Connections (Client)** under Signing & Capabilities > App Sandbox.
8. **Write the CI workflow** in `.github/workflows/ci.yml` with two jobs:
   - `backend`: on Ubuntu, run the tests, then build the Docker image.
   - `app`: on a macOS runner, build the app with `xcodebuild` (no signing, from D22).
9. **Turn on secret protection.** In the repo on GitHub: Settings > Code security > turn on secret scanning and push protection.
10. **Push and open a PR.**
    ```bash
    git add .
    git commit -m "M0: health endpoint, Docker image, notch panel, CI"
    git push -u origin m0-skeleton
    ```
    Merge only when both CI jobs are green.

**Done when:**
- [ ] CI is green on the PR, both jobs.
- [ ] The black panel shows at the notch, over a fullscreen app, on every Space.
- [ ] The dot is green with the container running and turns red within 5 s when you stop it.
- [ ] Four decision records are in `docs/decisions/`.

**Proof:** CI badge in the README, a short screen recording of the panel over a fullscreen app.
