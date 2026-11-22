"""Settings come from environment variables, the same way locally, in CI and in Azure.

Compose fills them from `.env` (git-ignored). Code never holds a secret.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# backend/migrations, when running from a checkout (local dev and tests).
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
# <repo>/data, when running from a checkout. The Docker image sets STORAGE_DIR=/data (a volume).
_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[3] / "data"


@dataclass(frozen=True)
class Settings:
    database_url: str
    migrations_dir: Path
    pool_min_size: int = 1
    pool_max_size: int = 10
    storage_backend: str = "local"  # D5: "local" now, "azure" in P6
    storage_dir: Path = _DEFAULT_STORAGE_DIR


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
    )
