"""Save, list, get and delete items (M1), and read a link's page history (P1, D32).

Plain SQL with server-side parameters. Queries are string literals, never f-strings,
so no value can ever become part of the SQL text (ruff rule S608 enforces this).
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from psycopg import Connection
from psycopg.rows import DictRow

from mind_dump import pages
from mind_dump.db import get_conn
from mind_dump.models import Item, ItemDetail, ItemPage, NewItem, NewLink, Version
from mind_dump.storage import Storage
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
                RETURNING id, type, title, url, body, status, created_at
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
        SELECT id, type, title, url, body, status, created_at FROM items
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
        "SELECT id, type, title, url, body, status, created_at FROM items WHERE id = %s",
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
    deleted = conn.execute("DELETE FROM items WHERE id = %s", (item_id,)).rowcount
    if deleted == 0:
        raise _not_found(item_id)
    conn.commit()
    storage.delete_prefix(f"raw/{item_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
