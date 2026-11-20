"""Apply new SQL migrations in order, once each (D4).

Run it with:  python -m mind_dump.migrate

How it works:
1. Take a Postgres advisory lock, so two copies never migrate at the same time.
2. Make sure the bookkeeping table `schema_migrations` exists.
3. For each `NNN_name.sql` file not yet recorded, in number order:
   run it and record it, inside ONE transaction. Either both happen or neither does.
"""

import logging
import re
import sys
from pathlib import Path

import psycopg

from mind_dump.config import load_settings

log = logging.getLogger("mind_dump.migrate")

# Any fixed number works; every migrator must use the same one.
_LOCK_KEY = 7_340_001
_FILE_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    """Return (version, path) pairs sorted by version. Rejects badly named or duplicate files."""
    found: dict[str, Path] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _FILE_PATTERN.match(path.name)
        if not match:
            raise ValueError(f"Bad migration file name: {path.name} (want NNN_snake_case.sql)")
        version = match.group(1)
        if version in found:
            raise ValueError(
                f"Two migrations share version {version}: {found[version].name}, {path.name}"
            )
        found[version] = path
    return sorted(found.items())


def migrate(database_url: str, migrations_dir: Path) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this run."""
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        # Session-level lock: released when the connection closes, even after a crash.
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     text PRIMARY KEY,
                name        text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for version, path in discover(migrations_dir):
            if version in done:
                continue
            log.info("applying %s", path.name)
            with conn.transaction():
                # No parameters, so psycopg sends the whole file, even with many statements.
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (version, path.name),
                )
            applied_now.append(version)
    return applied_now


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    applied = migrate(settings.database_url, settings.migrations_dir)
    log.info(
        "applied %d migration(s)%s", len(applied), f": {', '.join(applied)}" if applied else ""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
