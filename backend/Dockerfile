# syntax=docker/dockerfile:1
# Mind Dump API image (D13). Two stages:
#   builder: has uv, resolves and installs packages into /app/.venv
#   runtime: only Python + that .venv. No uv, no compilers, no tests.
# Layers go from "changes rarely" to "changes every commit", so a code change
# reuses the cached library layer.

ARG PYTHON_VERSION=3.12

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

# uv itself, pinned to the same version we use locally (D24).
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

# Compile .pyc at build time (faster start); copy files instead of hard links
# (the cache is a separate mount); use the image's Python, never download one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Layer 1: libraries only. Re-runs only when pyproject.toml or uv.lock change.
# --frozen fails the build if uv.lock is out of date, instead of silently re-resolving.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# Layer 2: our code. Re-runs on every code change, but it is fast.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

# A non-root user: if the app is ever compromised, the attacker is not root.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# Docker checks /health itself and marks the container healthy or unhealthy.
# Uses the standard library, so the image needs no curl.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# 0.0.0.0, not 127.0.0.1: inside a container, 127.0.0.1 means "this container
# only", so Docker's port forwarding could not reach the app.
CMD ["uvicorn", "mind_dump.main:app", "--host", "0.0.0.0", "--port", "8000"]
