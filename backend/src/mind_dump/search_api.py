"""GET /search (M4, D37): the HTTP side of search.py.

It checks the input (422 for an empty query, a limit out of range, a bad date or tag),
turns it into Filters, and maps SearchUnavailable to 503. All searching is in search.py,
so the eval (mind_dump.eval) runs exactly the same code without HTTP.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from mind_dump.embeddings import Embedder
from mind_dump.items import Conn
from mind_dump.models import SearchHit, SearchItem, SearchResponse
from mind_dump.search import DEFAULT_LIMIT, MAX_LIMIT, Filters, SearchUnavailable, search
from mind_dump.tags import normalise_tag

router = APIRouter(tags=["search"])


def get_embedder(request: Request) -> Embedder | None:
    """Set at startup from VOYAGE_API_KEY (D8); None when it is not set."""
    return getattr(request.app.state, "embedder", None)


EmbedderDep = Annotated[Embedder | None, Depends(get_embedder)]


def _moment(value: str | None, name: str, end_of_day: bool) -> datetime | None:
    """A date or a time. A date alone means the start of that day (from) or its end (to),
    in UTC, so from=2026-11-01&to=2026-11-01 is the whole of 1 November."""
    if value is None:
        return None
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            if end_of_day:
                start = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
                return start - timedelta(microseconds=1)
            return datetime.combine(day, time.min, tzinfo=UTC)
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{name} must be a date (2026-11-01) or a time (2026-11-01T09:30:00Z)",
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Find passages by exact words, by meaning, or both (hybrid)",
    responses={
        422: {"description": "Empty query, limit out of range, or a bad date or tag"},
        503: {"description": "mode=vector, and the embedding model is missing or failing"},
    },
)
def search_chunks(
    conn: Conn,
    embedder: EmbedderDep,
    q: Annotated[str, Query(max_length=1000, description="What to look for")],
    mode: Annotated[
        Literal["hybrid", "keyword", "vector"],
        Query(
            description='keyword: exact words ("a phrase", -exclude, or); vector: meaning; '
            "hybrid: both, merged by rank (RRF). Hybrid falls back to keyword results "
            "when the embedding model is missing or failing, and says so."
        ),
    ] = "hybrid",
    type_: Annotated[
        Literal["note", "link", "file"] | None, Query(alias="type", description="Item type")
    ] = None,
    from_: Annotated[
        str | None,
        Query(alias="from", description="Saved on or after: 2026-11-01 or a time"),
    ] = None,
    to: Annotated[
        str | None, Query(description="Saved on or before: 2026-11-30 (the whole day)")
    ] = None,
    tag: Annotated[str | None, Query(description="Only items with this tag")] = None,
    item_id: Annotated[int | None, Query(ge=1, description="Only this item's passages")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SearchResponse:
    query = q.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="q can't be empty")
    created_from = _moment(from_, "from", end_of_day=False)
    created_to = _moment(to, "to", end_of_day=True)
    if created_from and created_to and created_from > created_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="from must be on or before to"
        )
    try:
        tag_name = normalise_tag(tag) if tag is not None else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    filters = Filters(
        type=type_,
        created_from=created_from,
        created_to=created_to,
        tag=tag_name,
        item_id=item_id,
    )
    try:
        result = search(conn, query, mode, filters, limit, embedder)
    except SearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    return SearchResponse(
        query=result.query,
        requested_mode=result.requested_mode,
        mode=result.mode,
        degraded=result.degraded,
        notice=result.notice,
        hits=[
            SearchHit(
                chunk_id=h.chunk_id,
                chunk_index=h.chunk_index,
                page=h.page,
                start_char=h.start_char,
                end_char=h.end_char,
                text=h.text,
                item=SearchItem(
                    id=h.item_id,
                    type=h.item_type,
                    title=h.title,
                    url=h.url,
                    filename=h.filename,
                    created_at=h.item_created_at,
                ),
                score=h.score,
                keyword_rank=h.keyword_rank,
                keyword_score=h.keyword_score,
                vector_rank=h.vector_rank,
                vector_distance=h.vector_distance,
            )
            for h in result.hits
        ],
    )
