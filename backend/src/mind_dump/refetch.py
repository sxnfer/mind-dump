"""Fetch saved links now, land the pages and version their text (P1).

    python -m mind_dump.refetch 7 12        # these items
    python -m mind_dump.refetch --all       # every saved link
    python -m mind_dump.refetch --all --rebuild   # re-derive versions from landed files only

In Docker:  docker compose exec api python -m mind_dump.refetch --all

Until the worker and queue arrive (P3), this command is how pages get fetched. The worker
will call the same process_fetch, so nothing here is thrown away.
"""

import argparse
import sys

import httpx2
import psycopg
from psycopg.rows import dict_row

from mind_dump import pages
from mind_dump.config import load_settings
from mind_dump.fetcher import PageTooLarge, fetch, make_client
from mind_dump.storage import create_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mind_dump.refetch", description=__doc__)
    parser.add_argument("ids", nargs="*", type=int, help="item ids to fetch")
    parser.add_argument("--all", action="store_true", help="every saved link")
    parser.add_argument(
        "--rebuild", action="store_true", help="no network: rebuild versions from landed files"
    )
    args = parser.parse_args(argv)
    if not args.ids and not args.all:
        parser.error("give item ids, or --all")

    settings = load_settings()
    storage = create_storage(settings)
    failures = 0
    # autocommit: each step in pages.py is its own real transaction.
    with (
        psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn,
        make_client() as client,
    ):
        if args.all:
            rows = conn.execute("SELECT id, url FROM items WHERE type = 'link' ORDER BY id")
        else:
            rows = conn.execute(
                "SELECT id, url FROM items WHERE type = 'link' AND id = ANY(%s) ORDER BY id",
                (args.ids,),
            )
        links = rows.fetchall()
        missing = set(args.ids) - {link["id"] for link in links}
        for item_id in sorted(missing):
            print(f"item {item_id}: not a saved link")
            failures += 1

        for link in links:
            item_id = link["id"]
            if args.rebuild:
                count = pages.rebuild_versions(conn, storage, item_id)
                print(f"item {item_id}: {count} version(s), rebuilt from landed files")
                continue
            try:
                result = fetch(client, link["url"])
            except (httpx2.HTTPError, PageTooLarge) as exc:
                print(f"item {item_id}: fetch failed, nothing recorded: {exc}")
                failures += 1
                continue
            outcome = pages.process_fetch(conn, storage, item_id, result)
            stored = "file stored" if outcome.landed.stored_new_file else "same bytes, no new file"
            version = "NEW VERSION" if outcome.new_version else "text unchanged"
            status = f"HTTP {result.http_status} {result.final_url}"
            print(f"item {item_id}: {status} | {stored} | {version}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
