"""Chunking (D36): paragraphs packed to about 1,000 characters, no overlap, exact positions."""

import pytest

from mind_dump.chunking import LIMIT, Chunk, chunk_text


def check_positions(text: str, chunks: list[Chunk], limit: int = LIMIT) -> None:
    """Every chunk is the exact slice it claims, within the limit, in order, never overlapping."""
    assert [c.index for c in chunks] == list(range(len(chunks)))
    previous_end = 0
    for c in chunks:
        assert text[c.start_char : c.end_char] == c.text
        assert 0 < len(c.text) <= limit
        assert c.start_char >= previous_end  # no overlap, reading order
        assert c.text == c.text.strip()  # starts and ends on content, not whitespace
        previous_end = c.end_char


def paragraph(n: int, length: int) -> str:
    """A paragraph of exactly `length` characters, with no sentence end inside."""
    return (f"p{n}" + "-" * length)[:length]


def test_short_paragraphs_are_packed_until_the_next_would_pass_the_limit() -> None:
    paras = [paragraph(i, 300) for i in range(5)]
    text = "\n\n".join(paras)

    chunks = chunk_text(text)

    # 300 + 2 + 300 + 2 + 300 = 904 fits; a 4th paragraph would make 1206.
    assert [c.text for c in chunks] == ["\n\n".join(paras[:3]), "\n\n".join(paras[3:])]
    check_positions(text, chunks)


def test_a_paragraph_that_exactly_fills_the_limit_is_one_chunk() -> None:
    text = "a" * LIMIT
    assert [c.text for c in chunk_text(text)] == [text]


def test_several_blank_lines_and_spaces_count_as_one_gap() -> None:
    text = "  First.\n\n \n\t\n\nSecond.  \n"
    chunks = chunk_text(text)

    assert [c.text for c in chunks] == [text[2 : text.index("Second.") + len("Second.")]]
    check_positions(text, chunks)


def test_a_long_paragraph_is_cut_at_sentence_ends() -> None:
    sentence = "This sentence is exactly fifty characters long ok. "
    assert len(sentence) == 51  # fifty plus the space
    long_para = (sentence * 30).strip()  # about 1,530 characters, one paragraph
    text = "Intro.\n\n" + long_para + "\n\nOutro."

    chunks = chunk_text(text)

    assert chunks[0].text == "Intro."  # never mixed into the long paragraph's chunks
    assert chunks[-1].text == "Outro."
    middle = chunks[1:-1]
    assert len(middle) == 2
    for c in middle:
        assert c.text.endswith("ok.")  # every cut is at a sentence end
        assert c.text.startswith("This sentence")
    # Packed: 19 sentences fit in 1,000 (19 * 51 - 1 = 968); a 20th would make 1,019.
    assert len(middle[0].text) == 19 * 51 - 1
    check_positions(text, chunks)


def test_a_line_break_counts_as_a_sentence_end_in_a_long_paragraph() -> None:
    # The web extractor writes one block per line, with no blank lines (D7).
    lines = [f"Heading {i} without a full stop" + " filler" * 20 for i in range(10)]
    text = "\n".join(lines)
    assert len(text) > LIMIT

    chunks = chunk_text(text)

    assert len(chunks) > 1
    for c in chunks:
        # Every chunk starts and ends on a whole line: no block is cut in the middle.
        assert c.text.split("\n")[0] in lines
        assert c.text.split("\n")[-1] in lines
    check_positions(text, chunks)


def test_a_closing_quote_stays_with_its_sentence() -> None:
    first = 'He said "stop here." '
    text = (first + "y" * 990).strip()
    chunks = chunk_text(text)

    assert chunks[0].text == 'He said "stop here."'
    check_positions(text, chunks)


def test_a_sentence_longer_than_the_limit_is_cut_at_the_limit() -> None:
    text = "z" * 2500

    chunks = chunk_text(text)

    assert [(c.start_char, c.end_char) for c in chunks] == [(0, 1000), (1000, 2000), (2000, 2500)]
    check_positions(text, chunks)


def test_a_long_sentence_between_normal_ones() -> None:
    text = "Short start. " + "q" * 1500 + ". Short end."

    chunks = chunk_text(text)

    assert chunks[0].text == "Short start."
    assert chunks[1].text == "q" * 1000
    assert chunks[2].text == "q" * 500 + ". Short end."  # the remainder packs with what follows
    check_positions(text, chunks)


@pytest.mark.parametrize("text", ["", " ", "\n\n\n", " \t \n \n  "])
def test_empty_or_whitespace_text_gives_no_chunks(text: str) -> None:
    assert chunk_text(text) == []


def test_the_same_text_always_gives_the_same_chunks() -> None:
    text = "\n\n".join(paragraph(i, 170 + 37 * i) for i in range(12)) + "\n\n" + "w. " * 900
    first = chunk_text(text)

    assert chunk_text(text) == first
    assert chunk_text(str(text)) == first
    check_positions(text, first)


def test_positions_hold_for_a_realistic_mixed_text() -> None:
    text = (
        "Project 1234: Mangrove restoration\nStatus: Registered\n\n"
        + ("The project restores mangroves along the coast. It is verified yearly! " * 25)
        + "\n\nCredits issued: 120,000\n\n"
        + ("Why? Because the trees store carbon. " * 40)
        + "\n\n"
        + "x" * 1234
    )
    chunks = chunk_text(text)

    check_positions(text, chunks)
    # Together the chunks cover every non-space character of the text, once.
    assert "".join("".join(c.text.split()) for c in chunks) == "".join(text.split())


def test_the_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        chunk_text("text", limit=0)
