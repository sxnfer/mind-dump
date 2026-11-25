"""Save, list, get and delete items (M1), read a link's page history (P1, D32),
upload files (P2, D34), follow each item's processing (P3, D6), and read a file's
extracted text (M3, D7b).

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

from mind_dump import jobs, pages
from mind_dump.db import get_conn
from mind_dump.models import (
    Chunk,
    FileText,
    Item,
    ItemDetail,
    ItemPage,
    Job,
    NewItem,
    NewLink,
    Page,
    Version,
)
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


# An item's processing state is its latest job's status, in words the app can show (D6).
_STATE = {
    None: "saved",  # no job (every item gets one since M3; kept so a gap can't crash)
    "queued": "queued",
    "running": "processing",
    "failed": "retrying",
    "done": "ready",
    "dead": "error",
}


def _item_fields(row: dict) -> dict:
    """A row with job_status and job_error becomes the Item's status and error."""
    fields = dict(row)
    state = _STATE[fields.pop("job_status", None)]
    job_error = fields.pop("job_error", None)
    shown_error = job_error if state in ("retrying", "error") else None
    return {**fields, "status": state, "error": shown_error}


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
                RETURNING id, type, title, url, body, created_at,
                          sha256, media_type, size_bytes, filename
                """,
                (new.type, new.title, url, body),
            ).fetchone()
            if row is None:  # cannot happen: INSERT ... RETURNING always returns the row
                raise RuntimeError("INSERT returned no row")
            # Same transaction as the item (D6): an item never exists without its job,
            # and saving does no fetching; the worker does that later.
            job = jobs.enqueue(conn, row["id"])
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
    item = Item(**_item_fields({**row, "job_status": job["status"] if job else None}))
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
    """Store the bytes, then insert the row and its job. Returns the new row (with the
    job's status), or raises 409."""
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
                    RETURNING id, type, title, url, body, created_at,
                              sha256, media_type, size_bytes, filename
                    """,
                    (filename, sha256, media_type, len(data), filename),
                ).fetchone()
                if row is None:  # cannot happen: INSERT ... RETURNING always returns the row
                    raise RuntimeError("INSERT returned no row")
                # Same transaction as the item, like notes and links (D6): the worker
                # extracts the text later (D7b).
                job = jobs.enqueue(conn, row["id"])
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
    return {**row, "job_status": job["status"] if job else None}


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
    item = Item(**_item_fields(row))  # queued: the worker extracts its text (D7b)
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
        SELECT i.id, i.type, i.title, i.url, i.body, i.created_at,
               i.sha256, i.media_type, i.size_bytes, i.filename,
               j.status AS job_status, j.error AS job_error
        FROM items i
        LEFT JOIN LATERAL (
            SELECT status, error FROM jobs WHERE item_id = i.id ORDER BY id DESC LIMIT 1
        ) j ON true
        WHERE (%(before)s::bigint IS NULL OR i.id < %(before)s)
        ORDER BY i.id DESC
        LIMIT %(limit)s
        """,
        {"before": before, "limit": limit + 1},
    ).fetchall()
    items = [Item(**_item_fields(row)) for row in rows[:limit]]
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
        SELECT i.id, i.type, i.title, i.url, i.body, i.created_at,
               i.sha256, i.media_type, i.size_bytes, i.filename,
               j.status AS job_status, j.error AS job_error
        FROM items i
        LEFT JOIN LATERAL (
            SELECT status, error FROM jobs WHERE item_id = i.id ORDER BY id DESC LIMIT 1
        ) j ON true
        WHERE i.id = %s
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise _not_found(item_id)
    version = pages.version_at(conn, item_id, at) if row["type"] == "link" else None
    return ItemDetail(**_item_fields(row), content=Version(**version) if version else None)


@router.get("/{item_id}/versions", response_model=list[Version])
def list_versions(item_id: int, conn: Conn) -> list[Version]:
    """Every text this page has shown, oldest first (D32)."""
    if conn.execute("SELECT 1 FROM items WHERE id = %s", (item_id,)).fetchone() is None:
        raise _not_found(item_id)
    return [Version(**row) for row in pages.list_versions(conn, item_id)]


@router.post(
    "/{item_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Job,
    summary="Queue a fresh run for an item (a manual re-run, or a re-fetch of a link)",
    responses={
        404: {"description": "No such item"},
        409: {"description": "The item already has a job waiting or running"},
    },
)
def process_item(
    item_id: int,
    conn: Conn,
    from_raw: Annotated[
        bool,
        Query(
            description="Rebuild from what is stored: a link's versions from its landed "
            "files (no network, D28), a file's text from its stored bytes (an image is "
            "described again, D7b), then rewrite the chunks (after an extractor or "
            "chunker change)"
        ),
    ] = False,
) -> Job:
    item = conn.execute("SELECT type FROM items WHERE id = %s", (item_id,)).fetchone()
    if item is None:
        raise _not_found(item_id)
    job = jobs.enqueue(conn, item_id, "rebuild" if from_raw else "process")
    if job is None:  # the unique index jobs_one_open_per_item said no
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This item already has a job waiting or running"
        )
    return Job(**job)


@router.get("/{item_id}/chunks", response_model=list[Chunk])
def list_chunks(item_id: int, conn: Conn) -> list[Chunk]:
    """The item's passages in reading order (D36), each with its page (PDFs) and whether
    it has a vector yet (D8). The vector itself is never sent. Empty until the item's
    first job is done."""
    if conn.execute("SELECT 1 FROM items WHERE id = %s", (item_id,)).fetchone() is None:
        raise _not_found(item_id)
    rows = conn.execute(
        """
        SELECT chunk_index AS index, start_char, end_char, text, page,
               embedding IS NOT NULL AS embedded, embedding_model
        FROM chunks WHERE item_id = %s ORDER BY chunk_index
        """,
        (item_id,),
    ).fetchall()
    return [Chunk(**row) for row in rows]


@router.get(
    "/{item_id}/text",
    response_model=FileText,
    summary="A file's extracted text: a PDF's pages, or an image's description (D7b)",
    responses={
        404: {"description": "No such item, or the file's text is not extracted yet"},
        422: {"description": "The item is not a file"},
    },
)
def get_file_text(item_id: int, conn: Conn) -> FileText:
    """The whole text the file's chunks point into: chunk.text == text[start_char:end_char]."""
    item = conn.execute("SELECT type FROM items WHERE id = %s", (item_id,)).fetchone()
    if item is None:
        raise _not_found(item_id)
    if item["type"] != "file":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only files have extracted text. A note's text is its body; a link's "
            "is in GET /items/{id} (content).",
        )
    row = conn.execute(
        """
        SELECT text, text_sha256, extractor, page_starts, page_ends, created_at
        FROM file_texts WHERE item_id = %s
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} has no extracted text yet; see its status",
        )
    pages = None
    if row["page_starts"] is not None:
        pages = [
            Page(number=n, start_char=s, end_char=e)
            for n, (s, e) in enumerate(zip(row["page_starts"], row["page_ends"], strict=True), 1)
        ]
    return FileText(
        item_id=item_id,
        text=row["text"],
        text_sha256=row["text_sha256"],
        extractor=row["extractor"],
        kind="pages" if pages is not None else "description",
        pages=pages,
        created_at=row["created_at"],
    )


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, conn: Conn, storage: StorageDep) -> Response:
    # Rows first (fetches, versions, jobs, chunks and a file's text go with the item,
    # ON DELETE CASCADE), then files.
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
