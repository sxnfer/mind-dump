"""The FastAPI app. Uvicorn imports `app` from here (see the Dockerfile CMD)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mind_dump import chat_api, items, search_api
from mind_dump.config import load_settings
from mind_dump.db import create_pool
from mind_dump.embeddings import create_embedder
from mind_dump.llm import create_llm
from mind_dump.storage import create_storage
from mind_dump.usage import Usage, UsageRecorder


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pool at startup, close it at shutdown.

    `wait=True` fails startup if Postgres is unreachable, so a broken setup shows up
    at once (and Compose restarts the API) instead of on the first request.
    """
    settings = load_settings()
    app.state.settings = settings
    app.state.storage = create_storage(settings)  # D5: where raw pages live
    pool = create_pool(settings)
    # D39: every model call writes a model_usage row, each in its own short transaction
    # on a connection from the pool. Looked up at call time, so tests can swap it.
    app.state.usage = UsageRecorder(pool.connection, settings)

    def record(usage: Usage) -> int:
        return app.state.usage(usage)

    # D8, D37: embeds search queries. None without VOYAGE_API_KEY: search falls back to
    # keyword results (hybrid) or answers 503 (vector).
    app.state.embedder = create_embedder(settings, on_usage=record)
    # D9: Claude answers questions. None without ANTHROPIC_API_KEY: asking answers 503.
    app.state.llm = create_llm(settings)
    pool.open(wait=True, timeout=10)
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Mind Dump API", version="0.7.0", lifespan=lifespan)
app.include_router(items.router)
app.include_router(search_api.router)
app.include_router(chat_api.router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness check: answers 200 while the process can serve requests.

    The Mac app's status dot, Docker's HEALTHCHECK and (in M7) Azure's probes
    all call this. Keep it cheap: no database or model calls here.
    """
    return {"status": "ok"}
