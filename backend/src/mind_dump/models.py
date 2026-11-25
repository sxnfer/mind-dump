"""The API's shapes. FastAPI turns any mismatch into a 422 that names the field."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, StringConstraints, UrlConstraints, field_validator

# Leading and trailing spaces are removed before length checks, so "   " is empty.
Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
WebUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]


class _NewItem(BaseModel):
    title: Title | None = None

    @field_validator("title")
    @classmethod
    def blank_title_is_none(cls, value: str | None) -> str | None:
        return value or None


class NewNote(_NewItem):
    type: Literal["note"]
    body: Body


class NewLink(_NewItem):
    type: Literal["link"]
    url: WebUrl  # http or https only; "not a url" → 422


# "type" picks the model, so errors talk about the right fields.
NewItem = Annotated[NewNote | NewLink, Field(discriminator="type")]


# What the app shows for an item, derived from its latest job (D6):
#   queued      saved; the worker hasn't started yet
#   processing  a worker is on it now
#   retrying    a try failed for a reason that may pass (D35); `error` says which
#   ready       chunked: its text is ready for search
#   error       gave up (dead); `error` says why. POST /items/{id}/process tries again
#   saved       no job (since M3 every item gets one on save; files included)
ProcessingStatus = Literal["saved", "queued", "processing", "retrying", "ready", "error"]


class Item(BaseModel):
    id: int
    type: Literal["note", "link", "file"]
    title: str | None
    url: str | None
    body: str | None
    status: ProcessingStatus
    error: str | None = None  # why it is retrying or in error; None otherwise
    created_at: datetime
    # Files only (D34); None for notes and links.
    sha256: str | None = None  # the file's identity: the same bytes give the same hash
    media_type: str | None = None  # decided from the file's first bytes, never the client
    size_bytes: int | None = None
    filename: str | None = None  # as the client sent it, reduced to the last path part
    tags: list[str] = []  # the owner's labels, normalised and sorted (PUT /items/{id}/tags)


class ItemPage(BaseModel):
    items: list[Item]
    # Pass as ?before=<next_before> to get the next (older) page. None means no more.
    next_before: int | None


class Version(BaseModel):
    """One stretch of a page's history (D32): the text it showed from valid_from until valid_to."""

    number: int  # 1 is the first text we saw
    valid_from: datetime
    valid_to: datetime | None  # None: still the current text
    text: str


class ItemDetail(Item):
    # The page text at ?as_of=, or now. None for notes, for a link not fetched yet,
    # and for a date before the first fetch.
    content: Version | None = None


class Job(BaseModel):
    """One request to process an item (D6). 202 from POST /items/{id}/process."""

    id: int
    item_id: int
    kind: Literal["process", "rebuild", "embed"]
    status: Literal["queued", "running", "failed", "done", "dead"]
    attempts: int
    run_after: datetime
    created_at: datetime


class Chunk(BaseModel):
    """One passage of an item's text (D36). text == source_text[start_char:end_char]."""

    chunk_id: int  # the id search hits and citations carry
    index: int  # 0, 1, 2, ... in reading order
    start_char: int
    end_char: int
    text: str
    page: int | None = None  # PDFs: the 1-based page it sits on (chunks never cross pages)
    embedded: bool = False  # has a vector (D8); False while no embedding key is set
    embedding_model: str | None = None  # which model made the vector


class Page(BaseModel):
    number: int  # 1-based
    start_char: int  # page n is text[start_char:end_char]
    end_char: int


class FileText(BaseModel):
    """A file's extracted text (D7b), the text its chunks point into. Derived data: a
    re-run with from_raw=true rebuilds it from the stored file."""

    item_id: int
    # pages: a PDF's own text, page by page. description: an image described by a model,
    # in the model's words, not the source's.
    kind: Literal["pages", "description"]
    text: str
    text_sha256: str
    extractor: str  # "pypdf 7.1.1", or "<model> (image description)"
    pages: list[Page] | None  # PDFs only
    created_at: datetime


class TagSet(BaseModel):
    """The whole set of an item's tags. PUT replaces it; [] clears it. Each tag is
    normalised (trimmed, inner spaces squeezed to one, lowercased) and repeats collapse."""

    tags: list[str] = Field(max_length=100)


class ItemTags(BaseModel):
    item_id: int
    tags: list[str]  # normalised and sorted


class SearchItem(BaseModel):
    """The item a passage comes from: enough to show it and to open it."""

    id: int
    type: Literal["note", "link", "file"]
    title: str | None
    url: str | None
    filename: str | None
    created_at: datetime


class SearchHit(BaseModel):
    """One passage that matched, where it sits, and how it was found (D37)."""

    chunk_id: int
    chunk_index: int  # 0, 1, 2, ... within the item
    page: int | None  # PDFs: 1-based; None otherwise
    start_char: int  # text == the item's text[start_char:end_char]
    end_char: int
    text: str
    item: SearchItem
    # How it was found. score is the RRF sum: 1 / (60 + rank) for each list it is in.
    score: float
    keyword_rank: int | None  # 1 is best; None: not in the keyword list
    keyword_score: float | None  # ts_rank, higher is better (all-words chunks come first)
    vector_rank: int | None  # 1 is best; None: not in the vector list
    vector_distance: float | None  # cosine distance, 0 to 2, lower is closer


class SearchResponse(BaseModel):
    query: str
    requested_mode: Literal["hybrid", "keyword", "vector"]
    mode: Literal["hybrid", "keyword", "vector"]  # what really ran
    degraded: bool  # True: the requested mode could not run in full; notice says why
    notice: str | None
    hits: list[SearchHit]


# ---------- M5: answers, conversations, usage (D9, D21, D38, D39) ----------

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class AskRequest(BaseModel):
    """POST /ask: a question, and filters our code applies to every search it makes."""

    question: Question
    title: Title | None = None
    type: Literal["note", "link", "file"] | None = None
    from_: str | None = Field(default=None, alias="from")  # 2026-11-01 or a full time
    to: str | None = None
    tag: str | None = None

    model_config = {"populate_by_name": True}


class NewConversation(BaseModel):
    """POST /conversations. With item_id: a Discuss chat about that item, usually about
    one passage of it: chunk_id (from a citation), or start_char (and end_char) when the
    chunk was replaced since (a citation keeps its positions)."""

    title: Title | None = None
    item_id: int | None = Field(default=None, ge=1)
    chunk_id: int | None = Field(default=None, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=1)

    @field_validator("title")
    @classmethod
    def blank_title_is_none(cls, value: str | None) -> str | None:
        return value or None


class NewMessage(BaseModel):
    text: Question


class Citation(BaseModel):
    """One passage an answer cites (D38): a snapshot taken when the passage was handed to
    the model, so it outlives the chunk. chunk_id is None once that chunk is gone (the
    item was re-processed or deleted); the marker keeps the original id."""

    marker: str  # as it appears in the text, e.g. [c:12]
    chunk_id: int | None
    item_id: int
    type: Literal["note", "link", "file"]
    title: str | None
    url: str | None
    page: int | None
    start_char: int
    end_char: int
    quote: str


class InvalidCitation(BaseModel):
    """A marker our code removed: the passage was never handed to the model in this
    conversation (not_retrieved), or there is no such chunk (no_such_chunk)."""

    marker: str
    chunk_id: int
    reason: Literal["not_retrieved", "no_such_chunk"]


class TurnUsage(BaseModel):
    model_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float  # an estimate from the price table in settings (D39)


class Answer(BaseModel):
    conversation_id: int
    message_id: int
    text: str  # with [c:<chunk id>] markers; each one is in citations
    citations: list[Citation]
    invalid_citations: list[InvalidCitation]
    grounded: bool  # False: no valid citation; the items may not hold the answer
    stop_reason: str | None
    incomplete: bool  # True: the tool-call limit was reached before a real answer
    model: str  # the model that wrote the answer
    usage: TurnUsage


class ConversationScope(BaseModel):
    item_id: int
    item_exists: bool  # False: the item was deleted; the chat is read-only
    title: str | None
    start_char: int | None
    end_char: int | None
    page: int | None
    text: str | None  # the passage, as it was when the chat began


class Conversation(BaseModel):
    id: int
    title: str | None
    scope: ConversationScope | None  # None: all items
    filters: dict
    message_count: int
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    id: int
    seq: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    # Answers only:
    citations: list[Citation] | None = None
    invalid_citations: list[InvalidCitation] | None = None
    grounded: bool | None = None
    stop_reason: str | None = None
    incomplete: bool | None = None
    model: str | None = None
    usage: TurnUsage | None = None


class ConversationDetail(Conversation):
    messages: list[Message]


class ConversationPage(BaseModel):
    conversations: list[Conversation]
    next_before: int | None


class UsageRow(BaseModel):
    provider: str
    model: str
    purpose: str  # answer, judge, describe, check, embed_documents, embed_query
    calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float


class UsageDayRow(UsageRow):
    day: date


class UsageToday(BaseModel):
    date: date
    cost_usd: float
    cap_usd: float  # DAILY_COST_CAP_USD
    remaining_usd: float
    by_model: list[UsageRow]


class UsageWeek(BaseModel):
    from_: date = Field(alias="from")
    to: date
    cost_usd: float
    by_day: list[UsageDayRow]

    model_config = {"populate_by_name": True}


class UsageReport(BaseModel):
    """GET /usage (D39): estimated costs, per UTC day, model and purpose."""

    today: UsageToday
    last_7_days: UsageWeek
