"""PDF text page by page (D7b), and chunks that never cross a page (D36, rule 6)."""

import pytest
from pdfs import make_pdf

from mind_dump.chunking import chunk_pages, chunk_text
from mind_dump.errors import PermanentError
from mind_dump.filetext import PAGE_BREAK, extract_file, extract_pdf, join_pages


def test_pages_are_joined_with_a_blank_line_and_their_places_kept() -> None:
    text, spans = join_pages(["one", "", "three"])
    assert text == "one\n\n\n\nthree"
    assert spans == [(0, 3), (5, 5), (7, 12)]  # an empty page keeps its number
    assert [text[s:e] for s, e in spans] == ["one", "", "three"]


def test_a_pdf_gives_its_text_page_by_page() -> None:
    result = extract_pdf(make_pdf(["First page (intro)\nsecond line", "Second page."]))
    assert result.text == "First page (intro)\nsecond line" + PAGE_BREAK + "Second page."
    assert [result.text[s:e] for s, e in result.pages] == [
        "First page (intro)\nsecond line",
        "Second page.",
    ]
    assert result.extractor.startswith("pypdf ")


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (make_pdf(["", ""]), "no text layer"),
        (b"%PDF-1.7\nthis is not a pdf at all", "damaged"),
        (make_pdf(["fine"])[:200], "damaged"),
    ],
    ids=["scanned", "garbage", "truncated"],
)
def test_pdfs_without_text_are_permanent(data: bytes, reason: str) -> None:
    with pytest.raises(PermanentError, match=reason):
        extract_pdf(data)


def test_no_nul_characters_reach_the_database() -> None:
    result = extract_pdf(make_pdf(["tab\there"]))
    assert "\x00" not in result.text


def test_an_image_without_a_describer_names_the_key() -> None:
    with pytest.raises(PermanentError, match="ANTHROPIC_API_KEY"):
        extract_file(b"\x89PNG\r\n\x1a\n", "image/png", None)


def test_chunks_never_cross_a_page_and_keep_their_numbers() -> None:
    pages = ["Short page one.", "Short page two.", ("Long sentence here. " * 120).strip()]
    text, spans = join_pages(pages)

    chunks = chunk_pages(text, spans)

    assert len(chunk_text(text)) < len(chunks)  # without pages, 1 and 2 would merge
    assert [c.page for c in chunks][:2] == [1, 2]
    assert {c.page for c in chunks[2:]} == {3}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        start, end = spans[c.page - 1]
        assert start <= c.start_char < c.end_char <= end
        assert text[c.start_char : c.end_char] == c.text
