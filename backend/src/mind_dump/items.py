"""Save, list, get and delete items (M1), read a link's page history (P1, D32),
and upload files (P2, D34).

Plain SQL with server-side parameters. Queries are string literals, never f-strings,
so no value can ever become part of the SQL text (ruff rule S608 enforces this).
"""

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from mind_dump import pages
from mind_dump.db import get_conn
from mind_dump.models import Item, ItemDetail, ItemPage, NewItem, NewLink, Version
from mind_dump.storage import Storage
from mind_dump.uploads import (
    ALLOWED_MEDIA_TYPES,
    MAX_UPLOAD_BYTES,
    file_key,
    safe_filename,
    sniff_media_type,
)
from mind_dump.urls import normalise_url

router = APIRouter(prefix="/items", tags=["items"])

Conn = Annotated[Connection[DictRow], Depends(get_conn)]


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


StorageDep = Annotated[Storage, Depends(get_storage)]


def _not_found(item_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Item,
    responses={409: {"description": "This link is already saved; Location points to it"}},
)
def create_item(new: NewItem, conn: Conn, response: Response) -> Item:
    # The business key (D31): the same page always gets the same url text.
    url = normalise_url(str(new.url)) if isinstance(new, NewLink) else None
    body = None if isinstance(new, NewLink) else new.body
    try:
        # A savepoint: if the INSERT fails, only it is undone, and the connection stays usable.
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO items (type, title, url, body) VALUES (%s, %s, %s, %s)
                RETURNING id, type, title, url, body, status, created_at,
                          sha256, media_type, size_bytes, filename
                """,
                (new.type, new.title, url, body),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # The database's unique index (migration 003) caught a duplicate link.
        existing = conn.execute(
            "SELECT id FROM items WHERE type = 'link' AND url = %s", (url,)
        ).fetchone()
        existing_id = existing["id"] if existing else None
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This link is already saved",
            headers={"Location": f"/items/{existing_id}"} if existing_id else None,
        ) from None
    if row is None:  # cannot happen: INSERT ... RETURNING always returns the row
        raise RuntimeError("INSERT returned no row")
    item = Item(**row)
    response.headers["Location"] = f"/items/{item.id}"
    return item


_TOO_LARGE = HTTPException(
    status.HTTP_413_CONTENT_TOO_LARGE,
    detail=f"Files can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
)


async def _read_capped(request: Request) -> tuple[bytes, str]:
    """The request body and its SHA-256, refused with 413 as soon as it passes the limit.

    Content-Length is checked first, so an honest large upload is refused before any
    byte is read. The count while reading is the real check: the header can lie or be
    missing (a chunked upload), and the stream never sees more than limit + one chunk.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise _TOO_LARGE
    chunks: list[bytes] = []
    size = 0
    digest = hashlib.sha256()
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise _TOO_LARGE
        digest.update(chunk)
        chunks.append(chunk)
    return b"".join(chunks), digest.hexdigest()


def _save_file(
    pool: ConnectionPool[Connection[DictRow]],
    storage: Storage,
    data: bytes,
    sha256: str,
    media_type: str,
    filename: str | None,
) -> dict:
    """Store the bytes, then insert the row. Returns the new row, or raises 409."""
    # File first, row second: no row can ever point at a missing file.
    # A duplicate also writes nothing new: same bytes, same hash, same key, and put() is
    # write-once (the same bytes again is a no-op). So when the INSERT below fails with
    # 409, the file in storage is not an orphan. It IS the existing item's file.
    storage.put(file_key(sha256), data)
    with pool.connection() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO items (type, title, sha256, media_type, size_bytes, filename)
                    VALUES ('file', %s, %s, %s, %s, %s)
                    RETURNING id, type, title, url, body, status, created_at,
                              sha256, media_type, size_bytes, filename
                    """,
                    (filename, sha256, media_type, len(data), filename),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            # The unique index (migration 006) caught the same bytes uploaded before.
            existing = conn.execute(
                "SELECT id FROM items WHERE type = 'file' AND sha256 = %s", (sha256,)
            ).fetchone()
            existing_id = existing["id"] if existing else None
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="This file is already saved",
                headers={"Location": f"/items/{existing_id}"} if existing_id else None,
            ) from None
    if row is None:  # cannot happen: INSERT ... RETURNING always returns the row
        raise RuntimeError("INSERT returned no row")
    return row


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=Item,
    summary="Upload a PDF, PNG or JPEG as the raw request body",
    description=(
        "Send the file's bytes as the body (`curl --data-binary @report.pdf`) and its name "
        'in `Content-Disposition: attachment; filename="report.pdf"`. The type is decided '
        "from the file's first bytes; the Content-Type header is ignored."
    ),
    responses={
        409: {"description": "These exact bytes are already saved; Location points to it"},
        413: {"description": f"Larger than {MAX_UPLOAD_BYTES} bytes"},
        415: {"description": "Not a PDF, PNG or JPEG (judged by the file's first bytes)"},
        422: {"description": "The body is empty"},
    },
)
async def upload_file(request: Request, storage: StorageDep, response: Response) -> Item:
    data, sha256 = await _read_capped(request)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The body is empty")
    media_type = sniff_media_type(data[:16])
    if media_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF, PNG and JPEG files can be uploaded: "
            + ", ".join(ALLOWED_MEDIA_TYPES),
        )
    filename = safe_filename(request.headers.get("content-disposition"))
    # The database and the disk block, so they run on a worker thread, and a connection
    # is borrowed only now: a slow upload never holds one while its bytes arrive.
    row = await run_in_threadpool(
        _save_file, request.app.state.pool, storage, data, sha256, media_type, filename
    )
    item = Item(**row)
    response.headers["Location"] = f"/items/{item.id}"
    return item


@router.get("", response_model=ItemPage)
def list_items(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[
        int | None, Query(ge=1, description="Return items older than this id")
    ] = None,
) -> ItemPage:
    # Fetch one extra row: if it exists, there is another page.
    rows = conn.execute(
        """
        SELECT id, type, title, url, body, status, created_at,
               sha256, media_type, size_bytes, filename
        FROM items
        WHERE (%(before)s::bigint IS NULL OR id < %(before)s)
        ORDER BY id DESC
        LIMIT %(limit)s
        """,
        {"before": before, "limit": limit + 1},
    ).fetchall()
    items = [Item(**row) for row in rows[:limit]]
    next_before = items[-1].id if len(rows) > limit else None
    return ItemPage(items=items, next_before=next_before)


def _parse_as_of(value: str | None) -> datetime | None:
    """A date means the end of that day, UTC: "what did it say on 1 November?"."""
    if value is None:
        return None
    try:
        if len(value) == 10:
            next_day = date.fromisoformat(value) + timedelta(days=1)
            return datetime.combine(next_day, time.min, tzinfo=UTC) - timedelta(microseconds=1)
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of must be a date (2026-11-01) or a time (2026-11-01T09:30:00Z)",
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@router.get("/{item_id}", response_model=ItemDetail)
def get_item(
    item_id: int,
    conn: Conn,
    as_of: Annotated[
        str | None,
        Query(
            description="Show the page text as it was then: a date (2026-11-01, meaning the "
            "end of that day, UTC) or a time (2026-11-01T09:30:00Z). Leave out for now."
        ),
    ] = None,
) -> ItemDetail:
    at = _parse_as_of(as_of)
    row = conn.execute(
        """
        SELECT id, type, title, url, body, status, created_at,
               sha256, media_type, size_bytes, filename
        FROM items WHERE id = %s
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise _not_found(item_id)
    version = pages.version_at(conn, item_id, at) if row["type"] == "link" else None
    return ItemDetail(**row, content=Version(**version) if version else None)


@router.get("/{item_id}/versions", response_model=list[Version])
def list_versions(item_id: int, conn: Conn) -> list[Version]:
    """Every text this page has shown, oldest first (D32)."""
    if conn.execute("SELECT 1 FROM items WHERE id = %s", (item_id,)).fetchone() is None:
        raise _not_found(item_id)
    return [Version(**row) for row in pages.list_versions(conn, item_id)]


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, conn: Conn, storage: StorageDep) -> Response:
    # Rows first (fetches and versions go with the item, ON DELETE CASCADE), then files.
    # In that order a failure can only leave unused files, never rows pointing at nothing.
    deleted = conn.execute(
        "DELETE FROM items WHERE id = %s RETURNING type, sha256", (item_id,)
    ).fetchone()
    if deleted is None:
        raise _not_found(item_id)
    conn.commit()
    storage.delete_prefix(f"raw/{item_id}")
    if deleted["type"] == "file":
        # Safe to delete: the unique index means no other item holds these bytes.
        storage.delete(file_key(deleted["sha256"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
