"""The migration runner (D4): order, run-once, all-or-nothing, and file-name rules."""

from pathlib import Path

import psycopg
import pytest

from mind_dump.migrate import discover, migrate


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
