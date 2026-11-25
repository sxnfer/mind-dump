"""What happens to a saved link's page: land it, record it, version its text.

Two steps, each one transaction (D30):
1. land_fetch:  store the raw body (D28) and record the fetch. Always happens, so a
                parser bug never costs the source or the evidence.
2. apply_fetch: extract text from the LANDED file, never the network (D7), and open a
                new version only when the text hash changes (D32).

rebuild_versions replays every landed file through the current extractor and replaces
the item's versions in one transaction. Use it after improving the extractor.

Call these with an autocommit connection, so each `with conn.transaction()` is a real
COMMIT (inside an open transaction it would only be a savepoint). The worker (P3) calls
process_fetch and rebuild_versions; `python -m mind_dump.refetch` can call them directly.
"""

import gzip
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import Connection
from psycopg.rows import DictRow

from mind_dump.extract import extract_text, text_sha256
from mind_dump.fetcher import FetchResult
from mind_dump.storage import Storage

Conn = Connection[DictRow]
Extractor = Callable[[bytes, str | None], str]
PARSEABLE_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class LinkNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Landed:
    fetch_id: int
    storage_key: str
    stored_new_file: bool  # False when the bytes matched the item's latest file


@dataclass(frozen=True)
class Outcome:
    landed: Landed
    new_version: bool


def _require_autocommit(conn: Conn) -> None:
    if not conn.autocommit:
        raise ValueError("Use an autocommit connection, so each step really commits")


def _lock_link(conn: Conn, item_id: int) -> None:
    """Lock the item row: two processes of the same item take turns, never interleave."""
    row = conn.execute(
        "SELECT id FROM items WHERE id = %s AND type = 'link' FOR UPDATE", (item_id,)
    ).fetchone()
    if row is None:
        raise LinkNotFound(f"No link with id {item_id}")


def parseable(http_status: int, content_type: str | None) -> bool:
    """Only 2xx pages of a type we can read are extracted (D28). A 404 never makes text."""
    if not 200 <= http_status < 300 or not content_type:
        return False
    return content_type.split(";")[0].strip().lower() in PARSEABLE_TYPES


def raw_key(item_id: int, fetched_at: datetime, raw_sha256: str) -> str:
    stamp = fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"raw/{item_id}/{stamp}-{raw_sha256[:8]}.html.gz"


def land_fetch(conn: Conn, storage: Storage, item_id: int, result: FetchResult) -> Landed:
    _require_autocommit(conn)
    raw_sha = hashlib.sha256(result.body).hexdigest()
    with conn.transaction():
        _lock_link(conn, item_id)
        latest = conn.execute(
            """
            SELECT sha256, storage_key FROM fetches
            WHERE item_id = %s ORDER BY fetched_at DESC, id DESC LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if latest is not None and latest["sha256"] == raw_sha:
            key, stored = latest["storage_key"], False  # same bytes: record, don't store
        else:
            key, stored = raw_key(item_id, result.fetched_at, raw_sha), True
            # mtime=0: the same body always gzips to the same bytes, so a retry is a no-op.
            storage.put(key, gzip.compress(result.body, mtime=0))
        row = conn.execute(
            """
            INSERT INTO fetches (item_id, fetched_at, requested_url, final_url, http_status,
                                 content_type, byte_size, sha256, storage_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                item_id,
                result.fetched_at,
                result.requested_url,
                result.final_url,
                result.http_status,
                result.content_type,
                len(result.body),
                raw_sha,
                key,
            ),
        ).fetchone()
    if row is None:  # cannot happen: INSERT ... RETURNING always returns the row
        raise RuntimeError("INSERT returned no row")
    return Landed(fetch_id=row["id"], storage_key=key, stored_new_file=stored)


def _text_of(storage: Storage, fetch: DictRow, extract: Extractor) -> str:
    if not parseable(fetch["http_status"], fetch["content_type"]):
        return ""
    return extract(gzip.decompress(storage.get(fetch["storage_key"])), fetch["content_type"])


def apply_fetch(
    conn: Conn, storage: Storage, fetch_id: int, extract: Extractor = extract_text
) -> bool:
    """Open a new version if this fetch's text differs from the current one. Safe to re-run."""
    _require_autocommit(conn)
    with conn.transaction():
        fetch = conn.execute(
            """
            SELECT item_id, fetched_at, http_status, content_type, storage_key
            FROM fetches WHERE id = %s
            """,
            (fetch_id,),
        ).fetchone()
        if fetch is None:
            raise LookupError(f"No fetch with id {fetch_id}")
        _lock_link(conn, fetch["item_id"])
        text = _text_of(storage, fetch, extract)
        if not text:  # an error page, a PDF for now, or a page with no readable text
            return False
        sha = text_sha256(text)
        current = conn.execute(
            """
            SELECT id, valid_from, text_sha256 FROM item_versions
            WHERE item_id = %s AND valid_to IS NULL
            """,
            (fetch["item_id"],),
        ).fetchone()
        if current is not None:
            if current["text_sha256"] == sha:
                return False  # same meaning: ads, banners or markup changed, nothing more
            if fetch["fetched_at"] <= current["valid_from"]:
                return False  # older than the history we have: rebuild_versions repairs it
            conn.execute(
                "UPDATE item_versions SET valid_to = %s WHERE id = %s",
                (fetch["fetched_at"], current["id"]),
            )
        conn.execute(
            """
            INSERT INTO item_versions (item_id, fetch_id, valid_from, text, text_sha256)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (fetch["item_id"], fetch_id, fetch["fetched_at"], text, sha),
        )
        return True


def process_fetch(
    conn: Conn,
    storage: Storage,
    item_id: int,
    result: FetchResult,
    extract: Extractor = extract_text,
) -> Outcome:
    landed = land_fetch(conn, storage, item_id, result)
    return Outcome(landed=landed, new_version=apply_fetch(conn, storage, landed.fetch_id, extract))


def rebuild_versions(
    conn: Conn, storage: Storage, item_id: int, extract: Extractor = extract_text
) -> int:
    """Re-derive the item's whole history from its landed files, with no network (D32).

    A version records that the PAGE changed, so an extractor change must not add versions:
    it rewrites them. Returns the number of versions the item now has.
    """
    _require_autocommit(conn)
    with conn.transaction():
        _lock_link(conn, item_id)
        fetches = conn.execute(
            """
            SELECT id, fetched_at, http_status, content_type, storage_key
            FROM fetches WHERE item_id = %s ORDER BY fetched_at, id
            """,
            (item_id,),
        ).fetchall()
        versions: list[dict] = []
        for fetch in fetches:
            text = _text_of(storage, fetch, extract)
            if not text:
                continue
            sha = text_sha256(text)
            if versions and versions[-1]["text_sha256"] == sha:
                continue
            if versions:
                versions[-1]["valid_to"] = fetch["fetched_at"]
            versions.append(
                {
                    "fetch_id": fetch["id"],
                    "valid_from": fetch["fetched_at"],
                    "valid_to": None,
                    "text": text,
                    "text_sha256": sha,
                }
            )
        conn.execute("DELETE FROM item_versions WHERE item_id = %s", (item_id,))
        for v in versions:
            conn.execute(
                """
                INSERT INTO item_versions
                    (item_id, fetch_id, valid_from, valid_to, text, text_sha256)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    item_id,
                    v["fetch_id"],
                    v["valid_from"],
                    v["valid_to"],
                    v["text"],
                    v["text_sha256"],
                ),
            )
    return len(versions)


# ---------- reading history ----------


def list_versions(conn: Conn, item_id: int) -> list[DictRow]:
    return conn.execute(
        """
        SELECT row_number() OVER (ORDER BY valid_from) AS number, valid_from, valid_to, text
        FROM item_versions WHERE item_id = %s
        ORDER BY valid_from
        """,
        (item_id,),
    ).fetchall()


def version_at(conn: Conn, item_id: int, at: datetime | None) -> DictRow | None:
    """The version that was current at `at`, or the current one when `at` is None."""
    return conn.execute(
        """
        SELECT * FROM (
            SELECT row_number() OVER (ORDER BY valid_from) AS number,
                   valid_from, valid_to, text
            FROM item_versions WHERE item_id = %(item_id)s
        ) v
        WHERE CASE WHEN %(at)s::timestamptz IS NULL THEN valid_to IS NULL
                   ELSE valid_from <= %(at)s AND (valid_to IS NULL OR valid_to > %(at)s)
              END
        """,
        {"item_id": item_id, "at": at},
    ).fetchone()
