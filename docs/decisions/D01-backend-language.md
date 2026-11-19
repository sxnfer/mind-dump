# D1: Backend language

Status: Approved, 2026-11-17
Problem: the API and the background workers need one language, so they share code, tests and one container image.
Without a tool: two stacks, two sets of tests, two images to build and keep up to date.
Options: Python; TypeScript (Node); Swift on the server.
Chosen: Python (3.12 in the container).
Why: San already knows it, and the data and AI libraries (text extraction, embeddings, Postgres drivers) are strongest in Python.
Cost: slower than compiled languages, and loose typing unless we add type checks. The Mac app stays in Swift, so the project has two languages.
We will switch if: a single process cannot keep up with one owner's load, which is unlikely for a personal app.
