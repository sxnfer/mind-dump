"""Save, list, get and delete items (M1).

Plain SQL with server-side parameters. Queries are string literals, never f-strings,
so no value can ever become part of the SQL text (ruff rule S608 enforces this).
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg import Connection
from psycopg.rows import DictRow

from mind_dump.db import get_conn
from mind_dump.models import Item, ItemPage, NewItem, NewLink

router = APIRouter(prefix="/items", tags=["items"])

Conn = Annotated[Connection[DictRow], Depends(get_conn)]


def _not_found(item_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Item,
    responses={409: {"description": "This link is already saved; Location points to it"}},
)
def create_item(new: NewItem, conn: Conn, response: Response) -> Item:
    url = str(new.url) if isinstance(new, NewLink) else None
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


@router.get("/{item_id}", response_model=Item)
def get_item(item_id: int, conn: Conn) -> Item:
    row = conn.execute(
        "SELECT id, type, title, url, body, status, created_at FROM items WHERE id = %s",
        (item_id,),
    ).fetchone()
    if row is None:
        raise _not_found(item_id)
    return Item(**row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, conn: Conn) -> Response:
    deleted = conn.execute("DELETE FROM items WHERE id = %s", (item_id,)).rowcount
    if deleted == 0:
        raise _not_found(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
