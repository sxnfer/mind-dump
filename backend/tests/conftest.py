"""Shared pytest fixtures. Tests run against a REAL Postgres (explainer section 07).

Locally:  docker compose up -d db   then   uv run pytest
          (uses the database mind_dump_test on the Compose server, password from ../.env;
           your real items in the mind_dump database are never touched)
In CI:    a Postgres service container; the workflow sets TEST_DATABASE_URL.
The tests refuse to run against any database not named mind_dump_test.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from mind_dump.config import load_settings
from mind_dump.migrate import migrate

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_database_url() -> str:
    """Build the Compose database URL from ../.env, so `uv run pytest` just works."""
    env: dict[str, str] = {}
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    password = env.get("POSTGRES_PASSWORD", "")
    port = env.get("DB_PORT") or "5432"
    # A SEPARATE database: tests empty their tables, and must never touch your real items.
    return f"postgresql://mind_dump:{password}@localhost:{port}/{_TEST_DB}"


_TEST_DB = "mind_dump_test"


def _ensure_test_database(url: str) -> None:
    """Create the test database on first run (CREATE DATABASE can't run in a transaction)."""
    info = psycopg.conninfo.conninfo_to_dict(url)
    if info.get("dbname") != _TEST_DB:
        raise RuntimeError(
            f"Refusing to run tests against {info.get('dbname')!r}: use {_TEST_DB!r}"
        )
    with psycopg.connect(
        psycopg.conninfo.make_conninfo(url, dbname="postgres"), autocommit=True, connect_timeout=3
    ) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,)
        ).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE "{_TEST_DB}"')  # noqa: S608 (constant name)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or _default_database_url()
    os.environ["DATABASE_URL"] = url  # what the app's lifespan reads
    try:
        _ensure_test_database(url)
    except psycopg.OperationalError as exc:
        pytest.exit(f"Postgres is not reachable. Start it: docker compose up -d db\n{exc}", 2)
    migrate(url, load_settings().migrations_dir)  # same runner as production
    return url


@pytest.fixture(autouse=True)
def clean_tables(database_url: str) -> None:
    """Every test starts with empty tables and ids from 1."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("TRUNCATE items, tags, item_tags RESTART IDENTITY CASCADE")


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    """The `with` block runs the app's lifespan, so the pool opens and closes like in production."""
    from mind_dump.main import app

    with TestClient(app) as test_client:
        yield test_client
