"""Cut an item's text into chunks that search can find and cite (D36).

The rules:
1. Split the text on blank lines into paragraphs.
2. Join whole paragraphs into one chunk until the next would pass LIMIT characters.
3. A paragraph longer than LIMIT gets chunks of its own: it is cut at sentence ends, and
   its sentences are joined the same way. A line break also counts as a sentence end,
   because the web extractor (D7) writes one block per line: a heading or list item has
   no full stop, but it should never be cut in the middle.
4. A sentence longer than LIMIT is cut at LIMIT.
5. No overlap. Each chunk is the exact slice text[start:end], blank lines included, so a
   citation can point at the place, and the same text always gives the same chunks (D30).

A pure function: no database, no clock, no randomness.
"""

import re
from dataclasses import dataclass

LIMIT = 1000  # characters; about 250 tokens (D36)

# One or more blank lines (lines holding only spaces count as blank).
_PARAGRAPH_GAP = re.compile(r"\n[ \t\r\f\v]*\n\s*")
# The gap after a sentence: whitespace after . ! or ? (optionally closed by a quote or
# bracket), or any line break.
_SENTENCE_GAP = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+|\s*\n\s*")

Span = tuple[int, int]  # (start, end) in the source text, end exclusive


@dataclass(frozen=True)
class Chunk:
    index: int
    start_char: int
    end_char: int
    text: str


def _paragraphs(text: str) -> list[Span]:
    """The paragraphs between blank lines, without their leading or trailing whitespace."""
    spans: list[Span] = []
    pos = 0
    for match in _PARAGRAPH_GAP.finditer(text):
        spans.append((pos, match.start()))
        pos = match.end()
    spans.append((pos, len(text)))
    trimmed: list[Span] = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            trimmed.append((s, e))
    return trimmed


def _sentences(text: str, start: int, end: int) -> list[Span]:
    """Sentences of text[start:end]. A closing quote stays with its sentence."""
    spans: list[Span] = []
    pos = start
    for match in _SENTENCE_GAP.finditer(text, start, end):
        # Keep closing quotes/brackets in the sentence; only the whitespace is the gap.
        gap_start = match.start()
        while gap_start < match.end() and not text[gap_start].isspace():
            gap_start += 1
        spans.append((pos, gap_start))
        pos = match.end()
    spans.append((pos, end))
    return [(s, e) for s, e in spans if s < e and not text[s:e].isspace()]


def _cut_at_limit(spans: list[Span], limit: int) -> list[Span]:
    """Rule 4: a sentence longer than the limit becomes pieces of exactly `limit`."""
    out: list[Span] = []
    for s, e in spans:
        while e - s > limit:
            out.append((s, s + limit))
            s += limit
        out.append((s, e))
    return out


def _pack(spans: list[Span], limit: int) -> list[Span]:
    """Join neighbouring spans while the joined slice stays within the limit."""
    packed: list[Span] = []
    for s, e in spans:
        if packed and e - packed[-1][0] <= limit:
            packed[-1] = (packed[-1][0], e)
        else:
            packed.append((s, e))
    return packed


def chunk_text(text: str, limit: int = LIMIT) -> list[Chunk]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    spans: list[Span] = []
    short: list[Span] = []  # short paragraphs waiting to be packed together
    for start, end in _paragraphs(text):
        if end - start <= limit:
            short.append((start, end))
            continue
        # A long paragraph: finish the short ones before it, then cut it on its own.
        spans += _pack(short, limit)
        short = []
        sentences = _cut_at_limit(_sentences(text, start, end), limit)
        spans += _pack(sentences, limit)
    spans += _pack(short, limit)
    return [Chunk(i, s, e, text[s:e]) for i, (s, e) in enumerate(spans)]
