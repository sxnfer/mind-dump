#!/usr/bin/env bash
# P4 (D40): migrations from an empty database, run twice.
#   Run 1 must apply every file in migrations/ (they all work on a new install).
#   Run 2 must apply none (the runner never re-applies what it recorded).
# It runs the real command, python -m mind_dump.migrate, and reads its summary line
# ("applied N migration(s)", pinned by tests/test_migrate.py).
#
# Usage, from backend/:  DATABASE_URL=postgresql://.../<new db name> scripts/migrations_twice.sh
# It CREATES that database first and fails if it already exists, so it never runs on
# a database that holds anything. Drop it afterwards if you ran it locally.
set -euo pipefail

db_name="${DATABASE_URL##*/}"
db_name="${db_name%%\?*}"
case "$db_name" in
  mind_dump | mind_dump_test | postgres | "")
    echo "::error::refusing to use database '$db_name': give a new, unused name" >&2
    exit 2 ;;
esac

uv run python - "$DATABASE_URL" "$db_name" <<'PY'
import sys

import psycopg
from psycopg import sql

url, name = sys.argv[1], sys.argv[2]
admin = psycopg.conninfo.make_conninfo(url, dbname="postgres")
with psycopg.connect(admin, autocommit=True) as conn:
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
print(f"created empty database {name}")
PY

expected=$(find "${MIGRATIONS_DIR:-migrations}" -maxdepth 1 -name '*.sql' | wc -l | tr -d ' ')
echo "migration files: $expected"

run() {
  uv run python -m mind_dump.migrate 2>&1 | tee /dev/stderr \
    | sed -n 's/.*mind_dump.migrate: applied \([0-9][0-9]*\) migration(s).*/\1/p' | tail -1
}

first=$(run)
echo "run 1 applied: ${first:-?} (want $expected)"
if [ "$first" != "$expected" ]; then
  echo "::error::run 1 on an empty database applied ${first:-no summary} of $expected migrations" >&2
  exit 1
fi

second=$(run)
echo "run 2 applied: ${second:-?} (want 0)"
if [ "$second" != 0 ]; then
  echo "::error::run 2 applied ${second:-no summary} migrations; the runner must apply none" >&2
  exit 1
fi
echo "migrations twice: PASS ($expected applied on an empty database, then 0)"
