"""Tags (M4): the owner's labels on items, stored in migration 002's tables.

One rule makes two spellings the same tag, and both the tags API and the search filter
use it, so `?tag=Carbon%20Markets` finds an item tagged " carbon   markets":

    normalise_tag("  Carbon   Markets ") == "carbon markets"

    1. Trim spaces at both ends.
    2. Squeeze every run of whitespace inside into one space.
    3. Lowercase.
    4. The result must be 1 to 50 characters (the column's own check, migration 002).

Tag rows stay when no item uses them any more: they are the owner's vocabulary, and
deleting them would race with another request that is linking the same name.
"""

from psycopg import Connection
from psycopg.rows import DictRow

Conn = Connection[DictRow]

MAX_TAG_LENGTH = 50  # tags.name CHECK (migration 002)
MAX_TAGS_PER_ITEM = 20


def normalise_tag(raw: str) -> str:
    """The one spelling of a tag. Raises ValueError when nothing usable is left."""
    name = " ".join(raw.split()).lower()
    if not name:
        raise ValueError("A tag can't be empty")
    if len(name) > MAX_TAG_LENGTH:
        raise ValueError(f"A tag can be at most {MAX_TAG_LENGTH} characters: {name[:60]!r}")
    return name


def normalise_tags(raw: list[str]) -> list[str]:
    """Normalised, without repeats, sorted (the order tags are shown in)."""
    names = sorted({normalise_tag(r) for r in raw})
    if len(names) > MAX_TAGS_PER_ITEM:
        raise ValueError(f"An item can have at most {MAX_TAGS_PER_ITEM} tags")
    return names


def item_tags(conn: Conn, item_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id
        WHERE it.item_id = %s ORDER BY t.name
        """,
        (item_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def set_tags(conn: Conn, item_id: int, names: list[str]) -> list[str] | None:
    """Replace the item's tags with `names` (already normalised). None if no such item.

    One transaction. The item's row is locked first, so two PUTs on the same item run
    one after the other and the last one wins whole, never a mix of both.
    """
    with conn.transaction():
        if (
            conn.execute("SELECT 1 FROM items WHERE id = %s FOR UPDATE", (item_id,)).fetchone()
            is None
        ):
            return None
        # New names become tag rows; existing ones are reused (tags.name is unique).
        conn.execute(
            "INSERT INTO tags (name) SELECT unnest(%s::text[]) ON CONFLICT (name) DO NOTHING",
            (names,),
        )
        conn.execute(
            """
            DELETE FROM item_tags it USING tags t
            WHERE it.item_id = %s AND t.id = it.tag_id AND NOT (t.name = ANY(%s::text[]))
            """,
            (item_id, names),
        )
        conn.execute(
            """
            INSERT INTO item_tags (item_id, tag_id)
            SELECT %s, id FROM tags WHERE name = ANY(%s::text[])
            ON CONFLICT DO NOTHING
            """,
            (item_id, names),
        )
        return item_tags(conn, item_id)
