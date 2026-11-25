"""The API's shapes. FastAPI turns any mismatch into a 422 that names the field."""

from datetime import datetime
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
#   saved       no job: files, until PDF text arrives in stage 2
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
    kind: Literal["process", "rebuild"]
    status: Literal["queued", "running", "failed", "done", "dead"]
    attempts: int
    run_after: datetime
    created_at: datetime


class Chunk(BaseModel):
    """One passage of an item's text (D36). text == source_text[start_char:end_char]."""

    index: int  # 0, 1, 2, ... in reading order
    start_char: int
    end_char: int
    text: str
