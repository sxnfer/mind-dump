"""The answers eval (M5) end to end on the committed corpus, with a fake model and a
fake judge: this tests the harness (loading, the real agent loop, every check, the
report), not the model. E12 of M5's acceptance list. No network, no key.
"""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from chat_helpers import settings

from mind_dump import eval as search_eval
from mind_dump import eval_answers as harness
from mind_dump.llm import ModelTurn, TurnRequest, text_turn, tool_turn

QUESTIONS = harness.load_questions()


class CorpusFake:
    """A model that searches with the question, then answers from what came back: it
    cites the passage holding the expected text (or the top hit) and states the first
    spelling of each fact. Two planted mistakes test the checks: Q2 also cites a chunk
    it was never shown, and Q4 leaves its fact out. The unanswerable questions get
    'the items don't cover this'."""

    model = "fake-llm"

    def __init__(self) -> None:
        self.by_text = {q.question: q for q in QUESTIONS}
        self.calls = 0

    def stream(self, request: TurnRequest) -> Generator[str, None, ModelTurn]:
        self.calls += 1
        last = request.messages[-1]["content"]
        if last[0]["type"] == "text":  # the question: search for it
            return tool_turn(("search", {"query": last[-1]["text"]}))
        question = self.by_text[request.messages[-3]["content"][-1]["text"]]
        hits = json.loads(last[0]["content"])["results"]
        if not question.answerable:
            text = "The items don't cover this."
        else:
            hit = next((h for h in hits if question.passage in h["text"]), hits[0])
            facts = " ".join(spellings[0] for spellings in question.facts)
            if question.id == 4:
                facts = "an unknown number of times"
            text = f"The answer is {facts}. [c:{hit['chunk_id']}]"
            if question.id == 2:
                text += " Also this. [c:999999]"  # never shown, and no such chunk (D38)
        yield text
        return text_turn(text)


class FakeJudge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def judge(self, sentence: str, passage: str) -> tuple[bool | None, str]:
        self.calls.append((sentence, passage))
        return "answer is" in sentence, "fake verdict"


def test_the_question_set_is_well_formed() -> None:
    docs = {d.slug: d for d in search_eval.load_corpus()}
    answerable = [q for q in QUESTIONS if q.answerable]
    assert len(answerable) == 10 and len(QUESTIONS) == 13
    assert len({q.id for q in QUESTIONS}) == 13
    for q in answerable:
        assert q.passage in docs[q.document].body, q
        assert q.facts and all(q.facts)
        # A fact is something the passage really says.
        assert any(
            any(s.lower() in docs[q.document].body.lower() for s in spellings)
            for spellings in q.facts
        ), q


@pytest.fixture(scope="module")
def report(database_url: str) -> harness.AnswersReport:
    return harness.run_answers_eval(database_url, CorpusFake(), settings(), judge=FakeJudge())


def test_the_eval_runs_end_to_end_and_reports_every_check(report: harness.AnswersReport) -> None:
    assert report.documents == 12 and report.unfindable == []
    assert len(report.results) == 13 and report.search == "keyword"
    assert report.model_calls == 26  # search, then answer, for each question
    by_id = {r.question.id: r for r in report.results}

    # Q8 asks about DQ-117: keyword search finds the passage first, so all checks pass.
    q8 = by_id[8]
    assert (q8.valid, q8.expected, q8.facts, q8.passed) == (True, True, True, True)
    assert q8.supported == [(q8.supported[0][0], True, "fake verdict")]

    q2 = by_id[2]  # cited a chunk it was never shown: removed, reported, failed
    assert not q2.valid and not q2.passed

    q4 = by_id[4]  # left the fact out
    assert q4.facts is False and q4.missing_facts == ["two"] and not q4.passed

    for qid in (101, 102, 103):  # the corpus can't answer these: not grounded, passed
        r = by_id[qid]
        assert not r.grounded and r.valid and r.passed and r.expected is None

    text = harness.format_report(report)
    assert "Unanswerable questions answered as not grounded: 3/3" in text
    assert "missing facts: two" in text
    lines = harness.answers_markdown(report, "abc1234", datetime(2026, 11, 25, tzinfo=UTC))
    assert lines.startswith(harness.ANSWERS_HEADING)
    assert "| 8 | yes | yes | yes | yes | 1/1 | yes |" in lines
    # The fake writes one plain sentence: prose, for every question.
    assert all(r.prose for r in report.results)
    assert "Written as flowing prose (no list, heading or markup): 13/13" in text


def test_claims_split_sentences_and_keep_markers_after_the_stop() -> None:
    text = "Ten to twenty percent. [c:4] Fire cancels credits [c:5][c:6]. No marker here."
    assert harness.claims(text) == [
        ("Ten to twenty percent.", [4]),
        ("Fire cancels credits.", [5, 6]),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "The pool takes ten to twenty percent of credits. [c:4] Fire can reverse storage, "
        "so the pool covers it. [c:5][c:6]\n\nFirst you register, then you verify. [c:7]",
        "A dash - in a sentence, 2024. Then -5 degrees and 1.5 tonnes. [c:4]",
    ],
    ids=["two paragraphs", "dashes and numbers mid-text"],
)
def test_prose_passes_flowing_sentences(text: str) -> None:
    assert harness.is_prose(text)


@pytest.mark.parametrize(
    "text",
    [
        "Two things:\n- the pool [c:4]\n- the fire [c:5]",
        "Two things:\n* the pool [c:4]",
        "Two things:\n• the pool [c:4]",
        "Steps:\n1. Register. [c:4]\n2. Verify.",
        "Steps:\n1) Register. [c:4]",
        "## Buffer pool\nTen to twenty percent. [c:4]",
        "It is **ten to twenty** percent. [c:4]",
        "It is __ten__ percent. [c:4]",
        "The code is `MD-2291`. [c:4]",
    ],
    ids=["dash", "star", "bullet", "numbered", "numbered paren", "heading", "bold",
         "underscores", "backtick"],
)  # fmt: skip
def test_prose_fails_lists_headings_and_markup(text: str) -> None:
    assert not harness.is_prose(text)


def test_a_fake_run_writes_no_results_file(database_url: str, tmp_path: Path) -> None:
    results = tmp_path / "results.md"
    lines: list[str] = []
    code = harness.main(
        ["--database-url", database_url, "--results", str(results)],
        out=lines.append,
        llm=CorpusFake(),
    )
    assert code == 0
    assert not results.exists()
    assert "no results file is written" in lines[-1]


def test_without_a_key_it_is_pending_and_invents_nothing(tmp_path: Path) -> None:
    results = tmp_path / "results.md"
    results.write_text("# Search eval results\n\nnumbers\n")
    lines: list[str] = []
    assert harness.main(["--results", str(results)], out=lines.append) == 0
    assert "pending" in lines[0]
    written = results.read_text()
    assert written.startswith("# Search eval results") and "Pending" in written
    assert not any(ch.isdigit() for ch in written.split(harness.ANSWERS_HEADING)[1].split("`")[0])
    # A later real section replaces it, and the search eval keeps it when it rewrites.
    harness.write_section(results, f"{harness.ANSWERS_HEADING}\n\nreal numbers\n")
    assert written.split(harness.ANSWERS_HEADING)[0] in results.read_text()
    assert "Pending" not in results.read_text()
    assert search_eval.keep_answers(results).strip().endswith("real numbers")
    # A run without a key never overwrites real results with "pending".
    harness.main(["--results", str(results)], out=lines.append)
    assert "real numbers" in results.read_text()


def test_it_refuses_any_database_not_meant_for_it() -> None:
    with pytest.raises(RuntimeError, match="Refusing"):
        harness.run_answers_eval(
            "postgresql://u:p@localhost:5432/mind_dump", CorpusFake(), settings()
        )
