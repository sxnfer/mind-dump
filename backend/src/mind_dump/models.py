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


class Item(BaseModel):
    id: int
    type: Literal["note", "link"]
    title: str | None
    url: str | None
    body: str | None
    status: str
    created_at: datetime


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
