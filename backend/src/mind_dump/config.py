"""Settings come from environment variables, the same way locally, in CI and in Azure.

Compose fills them from `.env` (git-ignored). Code never holds a secret.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# backend/migrations, when running from a checkout (local dev and tests).
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
# <repo>/data, when running from a checkout. The Docker image sets STORAGE_DIR=/data (a volume).
_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[3] / "data"

DEFAULT_EMBEDDING_MODEL = "voyage-4"  # D8
DEFAULT_LLM_MODEL = "claude-opus-5-5"  # D8


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)  # holds the database password
    migrations_dir: Path
    pool_min_size: int = 1
    pool_max_size: int = 10
    storage_backend: str = "local"  # D5: "local" now, "azure" in P6
    storage_dir: Path = _DEFAULT_STORAGE_DIR
    # D8: the owner's model keys. Both are optional: without VOYAGE_API_KEY chunks are
    # saved with no vectors; without ANTHROPIC_API_KEY images can't be described.
    # repr=False: a logged or printed Settings never shows a key.
    voyage_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    llm_model: str = DEFAULT_LLM_MODEL


def _optional(name: str) -> str | None:
    """An unset or empty variable is None. Compose passes `${NAME:-}`, so empty is normal."""
    value = os.environ.get(name, "").strip()
    return value or None


def load_settings() -> Settings:
    try:
        database_url = os.environ["DATABASE_URL"]
    except KeyError:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env, or export it, e.g. "
            "postgresql://mind_dump:PASSWORD@localhost:5432/mind_dump"
        ) from None
    return Settings(
        database_url=database_url,
        migrations_dir=Path(os.environ.get("MIGRATIONS_DIR", _DEFAULT_MIGRATIONS_DIR)),
        pool_min_size=int(os.environ.get("DB_POOL_MIN", "1")),
        pool_max_size=int(os.environ.get("DB_POOL_MAX", "10")),
        storage_backend=os.environ.get("STORAGE_BACKEND", "local"),
        storage_dir=Path(os.environ.get("STORAGE_DIR", _DEFAULT_STORAGE_DIR)),
        voyage_api_key=_optional("VOYAGE_API_KEY"),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        embedding_model=_optional("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
        llm_model=_optional("LLM_MODEL") or DEFAULT_LLM_MODEL,
    )
