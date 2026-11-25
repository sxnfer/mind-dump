"""The migration runner (D4): order, run-once, all-or-nothing, and file-name rules."""

from pathlib import Path

import psycopg
import pytest

from mind_dump.config import load_settings
from mind_dump.migrate import discover, main, migrate


@pytest.fixture
def scratch_db(database_url: str) -> str:
    """A throwaway schema, so these tests never touch the real tables."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS migrate_test CASCADE")
        conn.execute("CREATE SCHEMA migrate_test")
    sep = "&" if "?" in database_url else "?"
    return f"{database_url}{sep}options=-csearch_path%3Dmigrate_test"


def write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql)


def test_applies_in_order_and_only_once(scratch_db: str, tmp_path: Path) -> None:
    write(tmp_path, "002_add_b.sql", "ALTER TABLE t ADD COLUMN b int;")
    write(tmp_path, "001_create_t.sql", "CREATE TABLE t (a int);")

    assert migrate(scratch_db, tmp_path) == ["001", "002"]
    assert migrate(scratch_db, tmp_path) == []  # second run: nothing new


def test_command_prints_a_summary_line_ci_can_check(
    scratch_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P4 (D40): CI runs the command twice on an empty database and reads this line.
    The first run must say "applied N migration(s)" for all N files, the second "applied 0"."""
    write(tmp_path, "001_create_t.sql", "CREATE TABLE t (a int);")
    write(tmp_path, "002_add_b.sql", "ALTER TABLE t ADD COLUMN b int;")
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))

    with caplog.at_level("INFO", logger="mind_dump.migrate"):
        assert main() == 0
        assert main() == 0
    summaries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("applied ")]
    assert summaries == ["applied 2 migration(s): 001, 002", "applied 0 migration(s)"]


def test_failed_migration_leaves_no_trace(scratch_db: str, tmp_path: Path) -> None:
    write(tmp_path, "001_ok.sql", "CREATE TABLE t (a int);")
    write(tmp_path, "002_broken.sql", "CREATE TABLE u (a int); SELECT * FROM no_such_table;")

    with pytest.raises(psycopg.errors.UndefinedTable):
        migrate(scratch_db, tmp_path)

    with psycopg.connect(scratch_db) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        u_exists = conn.execute("SELECT to_regclass('u') IS NOT NULL").fetchone()[0]
    assert versions == ["001"]  # 001 stays applied
    assert u_exists is False  # 002 rolled back completely, including table u


def test_rejects_bad_file_names(tmp_path: Path) -> None:
    write(tmp_path, "1_too_short.sql", "")

    with pytest.raises(ValueError, match="Bad migration file name"):
        discover(tmp_path)


def test_rejects_duplicate_versions(tmp_path: Path) -> None:
    write(tmp_path, "001_a.sql", "")
    write(tmp_path, "001_b.sql", "")

    with pytest.raises(ValueError, match="share version 001"):
        discover(tmp_path)


def test_009_queues_a_job_for_every_file_saved_before_it(database_url: str, tmp_path: Path) -> None:
    """Files saved in P2 had no job; migration 009 gives each one, as 007 did for notes."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS migrate_test CASCADE")
        conn.execute("CREATE SCHEMA migrate_test")
    # public stays on the path: the vector type lives where CREATE EXTENSION first put it.
    scratch = f"{database_url}?options=-csearch_path%3Dmigrate_test,public"
    real = load_settings().migrations_dir
    for path in sorted(real.glob("00[1-8]_*.sql")):
        (tmp_path / path.name).write_text(path.read_text())
    migrate(scratch, tmp_path)
    with psycopg.connect(scratch, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO items (type, sha256, media_type, size_bytes)"
            " VALUES ('file', repeat('a', 64), 'application/pdf', 10)"
        )
        conn.execute("INSERT INTO items (type, body) VALUES ('note', 'already has a job')")
        conn.execute("INSERT INTO jobs (item_id) SELECT id FROM items WHERE type = 'note'")

    (tmp_path / "009_x.sql").write_text((real / "009_file_text_and_embeddings.sql").read_text())
    assert migrate(scratch, tmp_path) == ["009"]

    with psycopg.connect(scratch) as conn:
        rows = conn.execute(
            "SELECT i.type, j.kind, j.status FROM jobs j JOIN items i ON i.id = j.item_id"
            " ORDER BY i.type"
        ).fetchall()
    assert rows == [("file", "process", "queued"), ("note", "process", "queued")]
