"""The FastAPI app. Uvicorn imports `app` from here (see the Dockerfile CMD)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mind_dump import items
from mind_dump.config import load_settings
from mind_dump.db import create_pool
from mind_dump.storage import create_storage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pool at startup, close it at shutdown.

    `wait=True` fails startup if Postgres is unreachable, so a broken setup shows up
    at once (and Compose restarts the API) instead of on the first request.
    """
    settings = load_settings()
    app.state.storage = create_storage(settings)  # D5: where raw pages live
    pool = create_pool(settings)
    pool.open(wait=True, timeout=10)
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Mind Dump API", version="0.4.0", lifespan=lifespan)
app.include_router(items.router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness check: answers 200 while the process can serve requests.

    The Mac app's status dot, Docker's HEALTHCHECK and (in M7) Azure's probes
    all call this. Keep it cheap: no database or model calls here.
    """
    return {"status": "ok"}
