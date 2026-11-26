"""Shared helpers for the M5 tests: items with chunks, a Context on the test database,
and Server-Sent Events parsing. No network anywhere: the model is a ScriptedLLM."""

import json
import re
from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from mind_dump.agent import Context, Scope
from mind_dump.config import Settings
from mind_dump.embeddings import Embedder
from mind_dump.search import Filters
from mind_dump.usage import UsageRecorder

Conn = psycopg.Connection[DictRow]


def settings(**changes: object) -> Settings:
    base = Settings(database_url="postgresql://unused", migrations_dir=".")  # type: ignore[arg-type]
    return replace(base, **changes)


def add_doc(
    conn: Conn,
    passages: list[str],
    title: str = "A document",
    type_: str = "note",
    pages: list[int] | None = None,
) -> tuple[int, list[int]]:
    """An item whose text is the passages joined by blank lines, one chunk each, with
    real positions (and pages, if given). Returns (item id, chunk ids in order)."""
    body = "\n\n".join(passages)
    if type_ == "note":
        item_id = conn.execute(
            "INSERT INTO items (type, title, body) VALUES ('note', %s, %s) RETURNING id",
            (title, body[:20000]),
        ).fetchone()["id"]
    else:
        n = conn.execute("SELECT count(*) AS n FROM items").fetchone()["n"]
        item_id = conn.execute(
            """
            INSERT INTO items (type, title, url, sha256, media_type, size_bytes)
            VALUES (%s, %s,
                    CASE WHEN %s = 'link' THEN 'https://example.org/' || %s END,
                    CASE WHEN %s = 'file' THEN lpad(to_hex(%s::int), 64, '0') END,
                    CASE WHEN %s = 'file' THEN 'application/pdf' END,
                    CASE WHEN %s = 'file' THEN 10 END)
            RETURNING id
            """,
            (type_, title, type_, n, type_, n, type_, type_),
        ).fetchone()["id"]
    return item_id, add_chunks(conn, item_id, passages, pages)


def add_chunks(
    conn: Conn, item_id: int, passages: list[str], pages: list[int] | None = None
) -> list[int]:
    ids, start = [], 0
    for i, text in enumerate(passages):
        ids.append(
            conn.execute(
                """
                INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text, page,
                                    source_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, repeat('a', 64))
                RETURNING id
                """,
                (item_id, i, start, start + len(text), text, pages[i] if pages else None),
            ).fetchone()["id"]
        )
        start += len(text) + 2
    return ids


def rechunk(conn: Conn, item_id: int) -> list[int]:
    """Re-process an item: the same text, the same positions, NEW chunk ids (D30)."""
    rows = conn.execute(
        "SELECT text, page FROM chunks WHERE item_id = %s ORDER BY chunk_index", (item_id,)
    ).fetchall()
    conn.execute("DELETE FROM chunks WHERE item_id = %s", (item_id,))
    pages = [r["page"] for r in rows] if rows and rows[0]["page"] is not None else None
    return add_chunks(conn, item_id, [r["text"] for r in rows], pages)


def context(
    conn: Conn,
    cfg: Settings | None = None,
    scope: Scope | None = None,
    embedder: Embedder | None = None,
    filters: Filters | None = None,
) -> Context:
    cfg = cfg or settings()
    return Context(
        db=lambda: nullcontext(conn),
        settings=cfg,
        embedder=embedder,
        scope=scope,
        filters=filters or Filters(),
        record=UsageRecorder.for_connection(conn, cfg),
    )


def drain(gen):  # noqa: ANN001, ANN201 (any generator)
    """Run a generator to the end: (the events it yielded, the value it returned)."""
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as done:
            return events, done.value


def tool_results(request_messages: list[dict]) -> list[dict]:
    """The tool_result blocks of the last message, with their content parsed if JSON."""
    out = []
    for block in request_messages[-1]["content"]:
        if block.get("type") == "tool_result":
            try:
                parsed = json.loads(block["content"])
            except ValueError:
                parsed = block["content"]
            out.append({**block, "parsed": parsed})
    return out


def sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def check_announced_first(events: list[tuple[str, dict]]) -> dict[int, int]:
    """Walk a stream in order and fail if the text cites a passage before a "passages"
    event announced it. Markers can be split across deltas, so the text so far is
    re-read after each one. Returns every announced chunk id with its item id."""
    marker = re.compile(r"\[c:(\d+)\]")
    announced: dict[int, int] = {}
    text = ""
    for kind, data in events:
        if kind == "passages":
            for p in data["passages"]:
                assert set(p) == {"chunk_id", "item_id"}, p
                announced[p["chunk_id"]] = p["item_id"]
        elif kind == "delta":
            text += data["text"]
            cited = {int(m) for m in marker.findall(text)}
            assert cited <= set(announced), f"cited before announced: {cited - set(announced)}"
    return announced


def count(conn: Conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608


def add_usage(conn: Conn, cost: str, days_ago: int = 0, model: str = "claude-opus-5-5",
              purpose: str = "answer", provider: str = "anthropic") -> None:  # fmt: skip
    conn.execute(
        """
        INSERT INTO model_usage (created_at, provider, model, purpose, input_tokens,
                                 output_tokens, cost_usd)
        VALUES (now() - make_interval(days => %s), %s, %s, %s, 10, 5, %s)
        """,
        (days_ago, provider, model, purpose, Decimal(cost)),
    )
