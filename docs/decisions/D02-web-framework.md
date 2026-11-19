# D2: Web framework

Status: Approved, 2026-11-17
Problem: every endpoint must read raw HTTP, route to the right function, check input, return JSON with the right status code, and be documented.
Without a tool: the standard library `http.server` makes us write routing, JSON, input checks, error codes and docs by hand for every endpoint. It also handles one request at a time unless we add threading.
Options: Python standard library server; Flask + Gunicorn; FastAPI + Uvicorn.
Chosen: FastAPI (framework) on Uvicorn (ASGI server).
Why: type hints become input checks with clear 422 errors; `/docs` is generated from the code, so it never drifts; async support suits the slow model and web calls in later milestones. Uvicorn is the server that handles connections; FastAPI decides what each request means.
Cost: two runtime dependencies (plus pydantic and starlette underneath). Some framework magic to learn: dependency injection and Pydantic models.
We will switch if: never likely. If we needed a different server, Uvicorn could be swapped for another ASGI server without touching app code.
Note: San chose to skip building the standard library version (see the M0 explainer artifact). The plain version was explained instead of built.
