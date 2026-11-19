"""The FastAPI app. Uvicorn imports `app` from here (see the Dockerfile CMD)."""

from fastapi import FastAPI

app = FastAPI(title="Mind Dump API", version="0.1.0")


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness check: answers 200 while the process can serve requests.

    The Mac app's status dot, Docker's HEALTHCHECK and (in M7) Azure's probes
    all call this. Keep it cheap: no database or model calls here.
    """
    return {"status": "ok"}
