"""Shared pytest fixtures. Tests run against a REAL Postgres (explainer section 07).

Locally:  docker compose up -d db   then   uv run pytest
          (uses the database mind_dump_test on the Compose server, password from ../.env;
           your real items in the mind_dump database are never touched)
In CI:    a Postgres service container; the workflow sets TEST_DATABASE_URL.
The tests refuse to run against any database not named mind_dump_test.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow, dict_row

from mind_dump.config import load_settings
from mind_dump.migrate import migrate
from mind_dump.storage import LocalStorage

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


# Every model key and the settings that choose a provider or a model (D8). A test that
# wants one sets it with monkeypatch.
MODEL_SETTINGS = (
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "ANTHROPIC_API_KEY",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_PRICE_PER_MTOK",
    "LLM_MODEL",
)


@pytest.fixture(scope="session", autouse=True)
def no_model_keys() -> None:
    """Tests never call a model API (D8): drop any real keys from this process, so the
    app, the commands and any worker subprocess see none. Tests that need a "configured"
    key set a dummy one with monkeypatch, and use fakes or mock transports."""
    for name in MODEL_SETTINGS:
        os.environ.pop(name, None)
    # The evals also read the repo's .env (eval._dotenv), where the owner's real keys
    # live: hand them a copy without any model setting, so no eval test can reach a model.
    from mind_dump import eval as search_eval

    read_dotenv = search_eval._dotenv

    def dotenv_without_model_settings() -> dict[str, str]:
        return {k: v for k, v in read_dotenv().items() if k not in MODEL_SETTINGS}

    search_eval._dotenv = dotenv_without_model_settings


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


@pytest.fixture(scope="session")
def storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Landed files go to a throwaway folder, never to <repo>/data."""
    path = tmp_path_factory.mktemp("storage")
    os.environ["STORAGE_DIR"] = str(path)  # what the app's lifespan reads
    return path


@pytest.fixture(autouse=True)
def clean_tables(database_url: str, storage_dir: Path) -> None:
    """Every test starts with empty tables, ids from 1 and no landed files."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE items, tags, item_tags, fetches, item_versions, jobs, chunks, file_texts,"
            " conversations, messages, model_usage, ask_requests RESTART IDENTITY CASCADE"
        )
    shutil.rmtree(storage_dir)
    storage_dir.mkdir()


@pytest.fixture
def storage(storage_dir: Path) -> LocalStorage:
    """The same folder the app uses, so API tests see what pages.py landed."""
    return LocalStorage(storage_dir)


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection[DictRow]]:
    """Autocommit, like the refetch command and (P3) the worker."""
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        yield connection


@pytest.fixture
def client(database_url: str, storage_dir: Path) -> Iterator[TestClient]:
    """The `with` block runs the app's lifespan, so the pool opens and closes like in production."""
    from mind_dump.main import app

    with TestClient(app) as test_client:
        yield test_client
