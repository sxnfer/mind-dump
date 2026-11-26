"""Text from uploaded files (D7b): PDFs page by page with pypdf, images through a describer.

The result is derived data: it can always be rebuilt from the stored file (files/<sha256>),
like a link's versions from its landed pages (D28). The worker stores it in `file_texts`
(migration 009) in the same transaction as the chunks that point into it (D30).

A PDF's pages are joined with a blank line, so a page break is also a paragraph break,
and each page's place in the text is kept: chunks never cross a page, and each one knows
its page number, for citations.
"""

import re
from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO

from pypdf import PdfReader

from mind_dump.describe import Describer
from mind_dump.errors import PermanentError
from mind_dump.uploads import IMAGE_MEDIA_TYPES

PAGE_BREAK = "\n\n"
PDF_EXTRACTOR = f"pypdf {version('pypdf')}"
# Control characters other than tab and newline. Postgres text can't hold NUL, and the
# rest are noise that some PDFs carry.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")

Span = tuple[int, int]


@dataclass(frozen=True)
class FileText:
    text: str
    pages: list[Span] | None  # PDFs: (start, end) of page 1, 2, ... in text. Images: None
    extractor: str  # "pypdf 7.1.1", or the model that wrote an image's description


def _clean(page_text: str) -> str:
    text = _CONTROL.sub("", page_text.replace("\r\n", "\n").replace("\r", "\n"))
    return _TRAILING_SPACE.sub("\n", text).strip()


def join_pages(page_texts: list[str]) -> tuple[str, list[Span]]:
    """Join pages with PAGE_BREAK and remember where each one sits. An empty page gets an
    empty span (start == end), so page numbers stay true."""
    parts: list[str] = []
    spans: list[Span] = []
    pos = 0
    for page in page_texts:
        if parts:
            parts.append(PAGE_BREAK)
            pos += len(PAGE_BREAK)
        parts.append(page)
        spans.append((pos, pos + len(page)))
        pos += len(page)
    return "".join(parts), spans


def extract_pdf(data: bytes) -> FileText:
    """The PDF's text, page by page. Raises PermanentError when there is none to read:
    the same bytes give the same answer, so asking again can't help (D35)."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # Some PDFs are encrypted only against editing and open with an empty
            # password. Anything else needs the owner's password, which we don't ask for.
            try:
                opened = reader.decrypt("")
            except Exception:  # pypdf raises several types, a missing crypto library too
                opened = 0
            if not opened:
                raise PermanentError(
                    "The PDF is encrypted (password-protected). Save an unlocked copy and "
                    "upload that."
                )
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except PermanentError:
        raise
    except Exception as exc:  # a damaged file can fail in many ways inside pypdf
        raise PermanentError(
            f"Can't read the PDF; it looks damaged ({type(exc).__name__}: {exc})"
        ) from exc
    if not pages:
        raise PermanentError("The PDF has no pages")
    if not any(pages):
        raise PermanentError(
            f"The PDF has no text layer ({len(pages)} page(s)); it is probably scanned. "
            "Text recognition (OCR) is not supported yet (D7b)."
        )
    text, spans = join_pages(pages)
    return FileText(text=text, pages=spans, extractor=PDF_EXTRACTOR)


def describe_image(data: bytes, media_type: str, describer: Describer | None) -> FileText:
    if describer is None:
        raise PermanentError(
            "Images need a description from Claude, and ANTHROPIC_API_KEY is not set. "
            "Set it in .env (see .env.example), restart the worker, then "
            "POST /items/{id}/process."
        )
    text = _CONTROL.sub("", describer.describe(data, media_type)).strip()
    if not text:
        raise PermanentError("The image description is empty")
    return FileText(text=text, pages=None, extractor=f"{describer.model} (image description)")


def extract_file(data: bytes, media_type: str, describer: Describer | None) -> FileText:
    if media_type == "application/pdf":
        return extract_pdf(data)
    if media_type in IMAGE_MEDIA_TYPES:
        return describe_image(data, media_type, describer)
    raise PermanentError(f"Can't read files of type {media_type}")
