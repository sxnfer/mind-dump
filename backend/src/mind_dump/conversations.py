"""Conversations (D21): store turns, replay history, and run one message end to end.

    answer_events(...)  the whole of one message: read the history, run the agent
                        (agent.run_turn), save the turn, then yield ("final", answer).
                        Status, passages and delta events stream out as they
                        happen.

Saving rules:
- The owner's message and the answer are written together, in ONE transaction, only
  after the answer is complete. A failure (the model is down, a refusal, an answer cut
  off) or a client that disconnects mid-stream leaves nothing behind: no half-written
  message, no question without its answer. The client can simply send it again.
- History is replayed exactly as stored (api_messages, json type, key order kept) and
  only appended to: the next request starts with every earlier request unchanged.
- Two messages racing in one conversation: the first to save wins; the second finds a
  newer turn than the one it read and is refused (409), so the history never forks.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Json, Jsonb

from mind_dump import agent
from mind_dump.agent import Context, Scope, TurnResult
from mind_dump.llm import LLM
from mind_dump.search import Filters

Conn = Connection[DictRow]
TITLE_LENGTH = 80  # an untitled conversation is named after its first question


class ConversationChanged(Exception):
    """Another message was saved in this conversation while this one was answered."""


class ConversationGone(Exception):
    """The conversation was deleted while this message was answered."""


@dataclass(frozen=True)
class NewConversation:
    """A conversation that does not exist yet: POST /ask creates it with its first
    answer, so a failed first answer leaves no empty conversation behind."""

    title: str | None
    filters: Filters


def filters_json(f: Filters) -> dict:
    out: dict = {}
    if f.type:
        out["type"] = f.type
    if f.created_from:
        out["from"] = f.created_from.isoformat()
    if f.created_to:
        out["to"] = f.created_to.isoformat()
    if f.tag:
        out["tag"] = f.tag
    return out


def filters_from_json(data: dict) -> Filters:
    return Filters(
        type=data.get("type"),
        created_from=datetime.fromisoformat(data["from"]) if data.get("from") else None,
        created_to=datetime.fromisoformat(data["to"]) if data.get("to") else None,
        tag=data.get("tag"),
    )


def scope_of(row: DictRow) -> Scope | None:
    if row["scope_item_id"] is None:
        return None
    return Scope(
        item_id=row["scope_item_id"],
        title=row["scope_item_title"],
        start_char=row["scope_start_char"],
        end_char=row["scope_end_char"],
        page=row["scope_page"],
        text=row["scope_text"],
    )


def get_conversation(conn: Conn, conversation_id: int) -> DictRow | None:
    return conn.execute(
        """
        SELECT c.*, (c.scope_item_id IS NULL
                     OR EXISTS (SELECT 1 FROM items i WHERE i.id = c.scope_item_id))
                    AS item_exists,
               (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
        FROM conversations c WHERE c.id = %s
        """,
        (conversation_id,),
    ).fetchone()


def create_conversation(
    conn: Conn, title: str | None, scope: Scope | None, filters: Filters | None = None
) -> int:
    row = conn.execute(
        """
        INSERT INTO conversations (title, scope_item_id, scope_item_title, scope_start_char,
                                   scope_end_char, scope_page, scope_text, filters)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            title,
            scope.item_id if scope else None,
            scope.title if scope else None,
            scope.start_char if scope else None,
            scope.end_char if scope else None,
            scope.page if scope else None,
            scope.text if scope else None,
            Jsonb(filters_json(filters or Filters())),
        ),
    ).fetchone()
    return row["id"]


def history(conn: Conn, conversation_id: int) -> tuple[list[dict], dict[int, dict], int]:
    """(every stored API message in order, every passage handed over so far, last seq)."""
    rows = conn.execute(
        """
        SELECT seq, api_messages, sources FROM messages
        WHERE conversation_id = %s ORDER BY seq
        """,
        (conversation_id,),
    ).fetchall()
    messages: list[dict] = []
    sources: dict[int, dict] = {}
    for r in rows:
        messages += r["api_messages"]
        sources.update({int(k): v for k, v in r["sources"].items()})
    return messages, sources, rows[-1]["seq"] if rows else 0


def save_turn(
    conn: Conn,
    conversation_id: int | None,
    new: NewConversation | None,
    read_seq: int,
    text: str,
    result: TurnResult,
) -> tuple[int, int]:
    """Write the owner's message and the answer in ONE transaction. Returns
    (conversation id, the answer's message id)."""
    with conn.transaction():
        if new is not None:
            conversation_id = create_conversation(conn, new.title, None, new.filters)
        else:
            locked = conn.execute(
                "SELECT id FROM conversations WHERE id = %s FOR UPDATE", (conversation_id,)
            ).fetchone()
            if locked is None:
                raise ConversationGone(f"Conversation {conversation_id} was deleted")
            last = conn.execute(
                "SELECT coalesce(max(seq), 0) AS seq FROM messages WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()["seq"]
            if last != read_seq:
                raise ConversationChanged(
                    "Another message was saved in this conversation while this one was "
                    "being answered; nothing was saved. Read the conversation and ask again."
                )
        conn.execute(
            """
            INSERT INTO messages (conversation_id, seq, role, text, api_messages)
            VALUES (%s, %s, 'user', %s, %s)
            """,
            (conversation_id, read_seq + 1, text, Json([result.user_message])),
        )
        row = conn.execute(
            """
            INSERT INTO messages (conversation_id, seq, role, text, api_messages, citations,
                                  invalid_citations, sources, grounded, stop_reason,
                                  incomplete, model, usage)
            VALUES (%s, %s, 'assistant', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                conversation_id,
                read_seq + 2,
                result.text,
                Json(result.api_messages),
                Jsonb(result.citations),
                Jsonb(result.invalid_citations),
                Jsonb({str(k): v for k, v in result.sources.items()}),
                result.grounded,
                result.stop_reason,
                result.incomplete,
                result.model,
                Jsonb(usage_json(result.usage)),
            ),
        ).fetchone()
        conn.execute(
            """
            UPDATE conversations SET updated_at = now(),
                   title = coalesce(title, nullif(left(btrim(%s), %s), ''))
            WHERE id = %s
            """,
            (text, TITLE_LENGTH, conversation_id),
        )
        if result.usage_ids:
            conn.execute(
                "UPDATE model_usage SET conversation_id = %s WHERE id = ANY(%s)",
                (conversation_id, result.usage_ids),
            )
    assert conversation_id is not None  # noqa: S101 (set above in both branches)
    return conversation_id, row["id"]


def usage_json(usage: dict) -> dict:
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in usage.items()}


def live_citations(citations: list[dict], existing: set[int]) -> list[dict]:
    """A citation's chunk_id is shown only while that chunk still exists (D38): after a
    re-run or a delete it is None, and the snapshot (item, page, positions, quote) is
    what remains. The marker keeps the original id."""
    return [
        {**c, "chunk_id": c["chunk_id"] if c["chunk_id"] in existing else None} for c in citations
    ]


def existing_chunks(conn: Conn, citations: list[dict]) -> set[int]:
    ids = [c["chunk_id"] for c in citations if c.get("chunk_id") is not None]
    return agent.chunks_that_exist(conn, ids) if ids else set()


def answer_json(
    conversation_id: int, message_id: int, result: TurnResult, existing: set[int]
) -> dict:
    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "text": result.text,
        "citations": live_citations(result.citations, existing),
        "invalid_citations": result.invalid_citations,
        "grounded": result.grounded,
        "stop_reason": result.stop_reason,
        "incomplete": result.incomplete,
        "model": result.model,
        "usage": usage_json(result.usage),
    }


def answer_events(
    llm: LLM,
    ctx: Context,
    text: str,
    conversation_id: int | None = None,
    new: NewConversation | None = None,
    on_result: Callable[[TurnResult], None] | None = None,
) -> Generator[tuple[str, dict], None, None]:
    """One message, end to end: status, passages and delta events while it runs, then ("final",
    the answer as the API returns it). Raises if it fails; then nothing was saved."""
    if conversation_id is not None:
        with ctx.db() as conn:
            past, sources, read_seq = history(conn, conversation_id)
    else:
        past, sources, read_seq = [], {}, 0
    result = yield from agent.run_turn(llm, ctx, past, sources, text)
    with ctx.db() as conn:
        cid, mid = save_turn(conn, conversation_id, new, read_seq, text, result)
        existing = existing_chunks(conn, result.citations)
    if on_result is not None:
        on_result(result)
    yield ("final", answer_json(cid, mid, result, existing))


def message_json(row: DictRow, existing: set[int]) -> dict:
    out = {
        "id": row["id"],
        "seq": row["seq"],
        "role": row["role"],
        "text": row["text"],
        "created_at": row["created_at"],
    }
    if row["role"] == "assistant":
        out.update(
            citations=live_citations(row["citations"], existing),
            invalid_citations=row["invalid_citations"],
            grounded=row["grounded"],
            stop_reason=row["stop_reason"],
            incomplete=row["incomplete"],
            model=row["model"],
            usage=row["usage"],
        )
    return out


def conversation_messages(conn: Conn, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, seq, role, text, citations, invalid_citations, grounded, stop_reason,
               incomplete, model, usage, created_at
        FROM messages WHERE conversation_id = %s ORDER BY seq
        """,
        (conversation_id,),
    ).fetchall()
    cited = [c for r in rows if r["role"] == "assistant" for c in r["citations"]]
    existing = existing_chunks(conn, cited)
    return [message_json(r, existing) for r in rows]
