"""Ask, Discuss and usage (M5): the HTTP side of agent.py and conversations.py.

    POST   /ask                          a question: starts a conversation, first answer
    POST   /conversations                start one (optionally about one item and passage)
    GET    /conversations                newest first
    GET    /conversations/{id}           its messages, with citations
    DELETE /conversations/{id}
    POST   /conversations/{id}/messages  the next message, answered
    GET    /usage                        estimated model spend: today, last 7 days (D39)

An answering endpoint returns JSON, or Server-Sent Events when asked (?stream=true or
Accept: text/event-stream): "status" (thinking, searching with the query, reading an
item), "delta" (answer text as it comes), then "final" with exactly the JSON the plain
endpoint returns, or "error" with the status and the reason. Nothing is saved unless
"final" is sent.

Checks, in this order, all before any model call:
404 no such conversation; 409 its item was deleted; 503 no ANTHROPIC_API_KEY;
429 over the daily cost cap or the rate limit (with Retry-After).
Then the model: 503 when it is temporarily unavailable (connection, 429, 5xx), 502 for
another API failure or an answer cut off at its length limit, 422 when Claude declines.
In every one of those cases nothing is saved: send the message again.

Answering endpoints borrow database connections from the pool only for each short step
(see agent.py), never while the model thinks, so they don't use the Conn dependency.
"""

import json
from collections.abc import Generator, Iterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from mind_dump import conversations as convs
from mind_dump import usage as usage_mod
from mind_dump.agent import Context, CutOff, Refused, Scope
from mind_dump.config import Settings
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.items import Conn
from mind_dump.llm import LLM
from mind_dump.models import (
    Answer,
    AskRequest,
    Conversation,
    ConversationDetail,
    ConversationPage,
    ConversationScope,
    NewConversation,
    NewMessage,
    UsageReport,
)
from mind_dump.search import Filters
from mind_dump.search_api import _moment
from mind_dump.tags import normalise_tag

router = APIRouter(tags=["chat"])

NO_KEY = (
    "Answers need Claude, and ANTHROPIC_API_KEY is not set. Add it to .env, then restart "
    "the API (docker compose up -d api)."
)

_ANSWER_RESPONSES: dict = {
    404: {"description": "No such conversation"},
    409: {"description": "The conversation's item was deleted, or it changed meanwhile"},
    422: {"description": "Bad input, or Claude declined to answer"},
    429: {"description": "Over DAILY_COST_CAP_USD or ASK_RATE_PER_MINUTE (Retry-After)"},
    502: {"description": "The Claude API failed, or the answer was cut off"},
    503: {"description": "No ANTHROPIC_API_KEY, or Claude is temporarily unavailable"},
}


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _llm(request: Request) -> LLM:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NO_KEY)
    return llm


def _context(request: Request, scope: Scope | None, filters: Filters) -> Context:
    state = request.app.state
    return Context(
        db=state.pool.connection,
        settings=state.settings,
        embedder=getattr(state, "embedder", None),
        scope=scope,
        filters=filters,
        record=state.usage,
    )


def _admit(request: Request, conversation_id: int | None) -> None:
    """D39: the cost cap, then the rate limit. Both before any model call."""
    settings = _settings(request)
    try:
        with request.app.state.pool.connection() as conn:
            usage_mod.check_cost_cap(conn, settings)
            usage_mod.admit_ask(conn, settings, conversation_id)
    except usage_mod.LimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after)},
        ) from None


def error_status(exc: Exception) -> tuple[int, str]:
    """What the client is told when answering fails. Nothing was saved in any case."""
    if isinstance(exc, Refused):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, f"{exc} Nothing was saved."
    if isinstance(exc, CutOff):
        return status.HTTP_502_BAD_GATEWAY, str(exc)
    if isinstance(exc, TemporaryError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, f"{exc}. Nothing was saved; try again."
    if isinstance(exc, PermanentError):
        return status.HTTP_502_BAD_GATEWAY, f"{exc}. Nothing was saved."
    if isinstance(exc, convs.ConversationChanged):
        return status.HTTP_409_CONFLICT, str(exc)
    if isinstance(exc, convs.ConversationGone):
        return status.HTTP_404_NOT_FOUND, str(exc)
    return (
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        f"Unexpected error: {type(exc).__name__}. Nothing was saved.",
    )


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _answer(final: dict) -> dict:
    return Answer.model_validate(final).model_dump(mode="json")


def _stream(events: Generator[tuple[str, dict], None, None]) -> Iterator[str]:
    """Events as Server-Sent Events. If the client disconnects, Starlette stops reading
    and the generator is closed at its current `yield`: before "final" that means before
    anything was saved (the save happens just before "final"), so nothing half-written
    is left."""
    try:
        for kind, data in events:
            yield _sse(kind, _answer(data) if kind == "final" else data)
    except Exception as exc:
        code, detail = error_status(exc)
        yield _sse("error", {"status": code, "detail": detail})


def _respond(
    request: Request, stream: bool, events: Generator[tuple[str, dict], None, None]
) -> Response | dict:
    wants_stream = stream or "text/event-stream" in request.headers.get("accept", "")
    if wants_stream:
        return StreamingResponse(
            _stream(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        final = next(data for kind, data in events if kind == "final")
    except Exception as exc:
        code, detail = error_status(exc)
        raise HTTPException(code, detail=detail) from None
    return _answer(final)


StreamFlag = Annotated[
    bool, Query(description="Answer as Server-Sent Events (or send Accept: text/event-stream)")
]


@router.post(
    "/ask",
    response_model=Answer,
    summary="Ask a question; starts a conversation and returns its first answer",
    responses=_ANSWER_RESPONSES,
)
def ask(body: AskRequest, request: Request, stream: StreamFlag = False) -> Response | dict:
    created_from = _moment(body.from_, "from", end_of_day=False)
    created_to = _moment(body.to, "to", end_of_day=True)
    if created_from and created_to and created_from > created_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="from must be on or before to"
        )
    try:
        tag = normalise_tag(body.tag) if body.tag is not None else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    filters = Filters(type=body.type, created_from=created_from, created_to=created_to, tag=tag)
    llm = _llm(request)
    _admit(request, None)
    new = convs.NewConversation(title=body.title, filters=filters)
    events = convs.answer_events(
        llm, _context(request, None, filters), body.question, conversation_id=None, new=new
    )
    return _respond(request, stream, events)


def _conversation_out(row: dict) -> dict:
    scope = None
    if row["scope_item_id"] is not None:
        scope = ConversationScope(
            item_id=row["scope_item_id"],
            item_exists=row["item_exists"],
            title=row["scope_item_title"],
            start_char=row["scope_start_char"],
            end_char=row["scope_end_char"],
            page=row["scope_page"],
            text=row["scope_text"],
        )
    return {
        "id": row["id"],
        "title": row["title"],
        "scope": scope,
        "filters": row["filters"],
        "message_count": row["message_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _not_found(conversation_id: int) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND, detail=f"Conversation {conversation_id} not found"
    )


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=Conversation,
    summary="Start a conversation: about all items, or (Discuss) about one item's passage",
    responses={
        404: {"description": "No such item"},
        422: {"description": "A chunk or position that isn't in that item"},
    },
)
def create_conversation(body: NewConversation, conn: Conn) -> dict:
    scope = None
    if body.item_id is None and (body.chunk_id or body.start_char is not None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="chunk_id and start_char need item_id",
        )
    if body.item_id is not None:
        item = conn.execute(
            "SELECT title, filename FROM items WHERE id = %s", (body.item_id,)
        ).fetchone()
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Item {body.item_id} not found")
        title = item["title"] or item["filename"]
        chunk = None
        if body.chunk_id is not None:
            chunk = conn.execute(
                """
                SELECT start_char, end_char, page, text FROM chunks
                WHERE id = %s AND item_id = %s
                """,
                (body.chunk_id, body.item_id),
            ).fetchone()
            if chunk is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Item {body.item_id} has no chunk {body.chunk_id} (now). A citation "
                    "keeps its positions: pass start_char and end_char instead.",
                )
        elif body.start_char is not None:
            # The passage by position: the chunk that holds start_char now.
            chunk = conn.execute(
                """
                SELECT start_char, end_char, page, text FROM chunks
                WHERE item_id = %s AND start_char <= %s AND end_char > %s
                ORDER BY chunk_index LIMIT 1
                """,
                (body.item_id, body.start_char, body.start_char),
            ).fetchone()
            if chunk is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Item {body.item_id} has no passage at character {body.start_char}",
                )
        scope = Scope(
            item_id=body.item_id,
            title=title,
            start_char=chunk["start_char"] if chunk else None,
            end_char=chunk["end_char"] if chunk else None,
            page=chunk["page"] if chunk else None,
            text=chunk["text"] if chunk else None,
        )
    title = body.title or (f"About: {scope.title}"[:200] if scope and scope.title else None)
    conversation_id = convs.create_conversation(conn, title, scope)
    return _conversation_out(convs.get_conversation(conn, conversation_id))


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    conn: Conn,
    before: Annotated[int | None, Query(ge=1, description="next_before from the last page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    rows = conn.execute(
        """
        SELECT c.*, (c.scope_item_id IS NULL
                     OR EXISTS (SELECT 1 FROM items i WHERE i.id = c.scope_item_id))
                    AS item_exists,
               (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
        FROM conversations c
        WHERE (%(before)s::bigint IS NULL OR c.id < %(before)s::bigint)
        ORDER BY c.id DESC LIMIT %(n)s
        """,
        {"before": before, "n": limit + 1},
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "conversations": [_conversation_out(r) for r in rows],
        "next_before": rows[-1]["id"] if more and rows else None,
    }


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, conn: Conn) -> dict:
    row = convs.get_conversation(conn, conversation_id)
    if row is None:
        raise _not_found(conversation_id)
    return {
        **_conversation_out(row),
        "messages": convs.conversation_messages(conn, conversation_id),
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, conn: Conn) -> Response:
    # Its messages go with it (ON DELETE CASCADE); its usage rows stay, the money was
    # spent (conversation_id is set to NULL).
    deleted = conn.execute(
        "DELETE FROM conversations WHERE id = %s RETURNING id", (conversation_id,)
    ).fetchone()
    if deleted is None:
        raise _not_found(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=Answer,
    summary="Send the next message in a conversation, and get the answer",
    responses=_ANSWER_RESPONSES,
)
def post_message(
    conversation_id: int, body: NewMessage, request: Request, stream: StreamFlag = False
) -> Response | dict:
    with request.app.state.pool.connection() as conn:
        row = convs.get_conversation(conn, conversation_id)
    if row is None:
        raise _not_found(conversation_id)
    if not row["item_exists"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"This conversation is about item {row['scope_item_id']}, which was "
            "deleted. Its earlier answers can still be read; start a new conversation "
            "to ask more.",
        )
    llm = _llm(request)
    _admit(request, conversation_id)
    ctx = _context(request, convs.scope_of(row), convs.filters_from_json(row["filters"]))
    events = convs.answer_events(llm, ctx, body.text, conversation_id=conversation_id)
    return _respond(request, stream, events)


@router.get(
    "/usage",
    response_model=UsageReport,
    summary="Estimated model spend: today (UTC) and the last 7 days, per model and purpose",
)
def get_usage(request: Request, conn: Conn) -> dict:
    return usage_mod.summary(conn, _settings(request))
