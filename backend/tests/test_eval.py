"""The search eval (M4) end to end on the committed corpus, against the test database.

Keyword mode is the real measurement CI runs. The fake embedder only proves that vector
and hybrid run through the same harness without error: its numbers mean nothing.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mind_dump import eval as harness
from mind_dump.embeddings import FakeEmbedder


def test_the_golden_set_is_well_formed() -> None:
    docs = {d.slug: d for d in harness.load_corpus()}
    questions = harness.load_golden()
    assert len(docs) == 12
    assert len(questions) == 20
    assert len({q.id for q in questions}) == 20
    assert {q.kind for q in questions} == {"words", "paraphrase", "identifier"}
    for q in questions:
        assert q.document in docs, q
        assert q.passage in docs[q.document].body, q  # an exact quote of its document


def test_keyword_eval_end_to_end(database_url: str) -> None:
    report = harness.run_eval(database_url, ["keyword"])
    assert report.documents == 12
    assert report.questions == 20
    assert report.chunks > report.documents  # several chunks per document
    # Every golden passage sits whole inside one chunk of its document, so every question
    # CAN be answered: a miss is search's fault, never the golden set's.
    assert report.unfindable == []
    keyword = report.mode("keyword")
    assert keyword is not None and len(keyword.answers) == 20
    assert 0.0 < keyword.recall_at_k <= 1.0
    assert 0.0 < keyword.mrr <= 1.0
    # A question that only shares an exact code with its passage is the case keyword
    # search exists for (D37): Q20 asks about alert DQ-117.
    q20 = next(a for a in keyword.answers if a.question.id == 20)
    assert q20.rank == 1


def test_vector_and_hybrid_run_with_the_fake_embedder(database_url: str) -> None:
    report = harness.run_eval(database_url, ["keyword", "vector", "hybrid"], FakeEmbedder())
    assert [m.mode for m in report.modes] == ["keyword", "vector", "hybrid"]
    for m in report.modes:
        assert len(m.answers) == 20
        assert 0.0 <= m.recall_at_k <= 1.0 and 0.0 <= m.mrr <= 1.0
    assert report.embedding_model == "fake-embedder"


def test_a_passage_split_across_chunks_is_reported(database_url: str) -> None:
    doc = harness.Document("only", "Only", "First paragraph.\n\nSecond paragraph.")
    split = harness.Question(1, "words", "q", "only", "paragraph.\n\nSecond")
    report = harness.run_eval(database_url, ["keyword"], docs=[doc], questions=[split])
    # Two short paragraphs pack into ONE chunk (D36), so this passage is findable...
    assert report.unfindable == []
    long_doc = harness.Document("long", "Long", ("a" * 900) + "\n\n" + ("b" * 900))
    across = harness.Question(1, "words", "q", "long", "a\n\nb")
    report = harness.run_eval(database_url, ["keyword"], docs=[long_doc], questions=[across])
    assert report.unfindable == [across]  # ...but one cut across two chunks never is


@pytest.mark.parametrize("name", ["mind_dump", "postgres", "mind_dump_evaluation"])
def test_it_refuses_any_database_not_meant_for_it(name: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing"):
        harness.run_eval(f"postgresql://u:p@localhost:5432/{name}", ["keyword"])


def test_the_command_prints_numbers_and_enforces_the_floor(
    database_url: str, tmp_path: Path
) -> None:
    lines: list[str] = []
    code = harness.main(
        ["--database-url", database_url, "--modes", "keyword", "--no-results",
         "--min-keyword-recall", "0.0"],
        out=lines.append,
    )  # fmt: skip
    text = "\n".join(lines)
    assert code == 0
    assert "recall@5" in text and "MRR@10" in text and "OK: keyword recall@5" in text

    lines.clear()
    code = harness.main(
        ["--database-url", database_url, "--modes", "keyword", "--no-results",
         "--min-keyword-recall", "1.01"],
        out=lines.append,
    )  # fmt: skip
    assert code == 1
    assert "FAIL" in lines[-1]

    results = tmp_path / "results.md"
    code = harness.main(
        ["--database-url", database_url, "--modes", "keyword", "--results", str(results)],
        out=lines.append,
    )
    assert code == 0
    written = results.read_text()
    assert "| keyword |" in written
    assert "| vector | pending |" in written and "VOYAGE_API_KEY" in written


def test_results_markdown_with_every_mode() -> None:
    q = harness.Question(1, "words", "What?", "doc", "passage")
    report = harness.Report(documents=1, chunks=2, questions=1, embedding_model="voyage-4")
    for mode, rank, doc_rank in (("keyword", None, 3), ("vector", 2, 1), ("hybrid", 1, 1)):
        report.modes.append(harness.ModeResult(mode, [harness.Answer(q, rank, doc_rank)]))
    text = harness.results_markdown(report, "abc1234", datetime(2026, 11, 25, tzinfo=UTC))
    assert "| keyword | 0.00 | 0.00 | 0/1 |" in text
    assert "| vector | 1.00 | 0.50 | 1/1 |" in text
    assert "| hybrid | 1.00 | 1.00 | 1/1 |" in text
    assert "pending" not in text
    assert "| 1 | words | What? | miss (other chunk at 3) | 2 | 1 |" in text


def test_a_miss_says_whether_the_document_came_back(database_url: str) -> None:
    doc = harness.Document("d", "D", ("Mangrove survival rose. " * 40) + "\n\n" + ("Other. " * 140))
    found = harness.Question(1, "words", "mangrove survival", "d", "Other. Other.")
    report = harness.run_eval(database_url, ["keyword"], docs=[doc], questions=[found])
    answer = report.mode("keyword").answers[0]
    assert (answer.rank, answer.doc_rank) == (None, 1)
