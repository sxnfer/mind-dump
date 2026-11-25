"""Measure answers (M5): are the citations real, do they point at the right passage, and
does each cited passage hold the claim it is attached to?

    uv run python -m mind_dump.eval_answers                (from backend/)
    uv run python -m mind_dump.eval_answers --no-results   (print only)

What it does, in order:
1. Connect to the eval's own database (default `mind_dump_eval`), with the same guard as
   the search eval: it empties its tables, so it refuses any database whose name doesn't
   end in _eval or _test.
2. Load the corpus (backend/eval/corpus/) through the real pipeline (eval.load_into):
   with the embedding key (OPENAI_API_KEY by default; VOYAGE_API_KEY with
   EMBEDDING_PROVIDER=voyage) the chunks are embedded and the agent's searches are hybrid;
   without it they are keyword only, and the report says so.
3. Check the questions (backend/eval/answers.json): every expected passage must sit whole
   inside one chunk of its document, or no citation could ever point at it.
4. Ask each question through the real agent loop, the same code POST /ask runs
   (conversations.answer_events), in a conversation of its own.
5. Check each answer:
       valid      every citation marker was a passage handed to the model (D38)
       expected   at least one citation is a chunk of the expected document that holds
                  the expected passage
       facts      the answer states every key fact (one spelling of each)
       supported  with a real model only: one judge call per citation asks whether the
                  cited passage supports the sentence it is attached to (yes or no, with
                  a one-line reason, as structured output)
   An unanswerable question passes when its answer is not grounded (no valid citation)
   and has no invalid citation either.
6. Print every question's checks, and write the "Answers" section of docs/results.md,
   only from a run with the real model (a fake run writes nothing). Without
   ANTHROPIC_API_KEY it says the eval is pending and invents no number.

Each question costs a few model calls (up to AGENT_MAX_TURNS + 1) plus one judge call
per citation. Every call is recorded in the eval database's model_usage, and the run's
total estimated cost is printed and written with the results.
"""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import anthropic
import psycopg
from psycopg.rows import dict_row

from mind_dump import conversations as convs
from mind_dump import eval as search_eval
from mind_dump.agent import MARKER, Context
from mind_dump.config import DEFAULT_LLM_MODEL, Settings, embedding_settings
from mind_dump.describe import FALLBACK_BETA, api_error, usage_of
from mind_dump.embeddings import Embedder, create_embedder
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.llm import LLM, ClaudeLLM
from mind_dump.migrate import migrate
from mind_dump.search import Filters
from mind_dump.usage import UsageRecorder, UsageSink

QUESTIONS_FILE = search_eval.EVAL_DIR / "answers.json"
ANSWERS_HEADING = search_eval.ANSWERS_HEADING


@dataclass(frozen=True)
class AnswerQuestion:
    id: int
    question: str
    document: str | None = None  # None: the corpus can't answer it
    passage: str | None = None
    facts: tuple[tuple[str, ...], ...] = ()

    @property
    def answerable(self) -> bool:
        return self.document is not None


def load_questions(path: Path = QUESTIONS_FILE) -> list[AnswerQuestion]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = [
        AnswerQuestion(
            q["id"], q["question"], q["document"], q["passage"],
            tuple(tuple(spellings) for spellings in q["facts"]),
        )
        for q in data["questions"]
    ]  # fmt: skip
    return questions + [AnswerQuestion(q["id"], q["question"]) for q in data["unanswerable"]]


# ---------- the judge ----------


class Judge(Protocol):
    def judge(self, sentence: str, passage: str) -> tuple[bool | None, str]: ...


JUDGE_PROMPT = """\
You check citations in an answer written from a personal knowledge base.

Sentence from the answer:
<sentence>
{sentence}
</sentence>

The passage it cites:
<passage>
{passage}
</passage>

Does the passage support what the sentence says? "Yes" only if everything the sentence \
states is said in the passage or follows directly from it. Reply with supported (true or \
false) and a reason of one line."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["supported", "reason"],
    "additionalProperties": False,
}


class ClaudeJudge:
    """One structured-output call per citation, at low effort (a yes or no on one
    passage needs little reasoning). Each call is recorded (purpose: judge)."""

    def __init__(self, client: anthropic.Anthropic, model: str, on_usage: UsageSink | None):
        self.model = model
        self._client = client
        self.on_usage = on_usage

    def judge(self, sentence: str, passage: str) -> tuple[bool | None, str]:
        try:
            message = self._client.beta.messages.create(
                model=self.model,
                max_tokens=4000,  # thinking counts toward it; the reply itself is one line
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=[
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(sentence=sentence, passage=passage),
                    }
                ],
            )
        except anthropic.APIError as exc:
            return None, f"judge call failed: {api_error(exc, self.model)}"
        if self.on_usage is not None:
            self.on_usage(usage_of(message, "judge"))
        if message.stop_reason != "end_turn":  # refusal or max_tokens: no verdict
            return None, f"no verdict (stop_reason {message.stop_reason})"
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            verdict = json.loads(text)
            return bool(verdict["supported"]), str(verdict["reason"])
        except (ValueError, KeyError) as exc:
            return None, f"unreadable verdict: {exc}"


# ---------- checking one answer ----------

_MARKERS_AFTER_STOP = re.compile(r"([.!?])((?:\s*\[c:\d+\])+)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def claims(text: str) -> list[tuple[str, list[int]]]:
    """Each sentence of an answer with the chunk ids cited in it. Markers written just
    after the full stop ("... pool. [c:12]") belong to the sentence before it, so they
    are first moved in front of the stop."""
    text = _MARKERS_AFTER_STOP.sub(r"\2\1", text.strip())
    out = []
    for sentence in _SENTENCE_END.split(text):
        ids = [int(m) for m in MARKER.findall(sentence)]
        if ids:
            plain = re.sub(r"\s+([.!?,;:])", r"\1", MARKER.sub("", sentence))
            out.append((plain.strip(), ids))
    return out


def states(text: str, spellings: Sequence[str]) -> bool:
    plain = MARKER.sub("", text).lower()
    return any(s.lower() in plain for s in spellings)


@dataclass
class Checked:
    question: AnswerQuestion
    text: str = ""
    grounded: bool = False
    incomplete: bool = False
    valid: bool = False  # no invalid citation
    expected: bool | None = None  # answerable only
    missing_facts: list[str] = field(default_factory=list)
    supported: list[tuple[str, bool | None, str]] | None = None  # (marker, verdict, reason)
    error: str | None = None

    @property
    def facts(self) -> bool | None:
        return not self.missing_facts if self.question.answerable else None

    @property
    def passed(self) -> bool:
        if self.error is not None or not self.valid:
            return False
        if not self.question.answerable:
            return not self.grounded
        judged = self.supported is None or all(v for _, v, _ in self.supported)
        return bool(self.expected and self.facts and judged)


def check(
    q: AnswerQuestion, answer: dict, item_ids: dict[str, int], judge: Judge | None
) -> Checked:
    c = Checked(
        question=q,
        text=answer["text"],
        grounded=answer["grounded"],
        incomplete=answer["incomplete"],
        valid=not answer["invalid_citations"],
    )
    if q.answerable:
        c.expected = any(
            cite["item_id"] == item_ids.get(q.document or "") and (q.passage or "") in cite["quote"]
            for cite in answer["citations"]
        )
        c.missing_facts = [s[0] for s in q.facts if not states(answer["text"], s)]
    if judge is not None and answer["citations"]:
        quotes = {cite["marker"]: cite["quote"] for cite in answer["citations"]}
        c.supported = []
        for sentence, ids in claims(answer["text"]):
            for chunk_id in ids:
                marker = f"[c:{chunk_id}]"
                if marker in quotes:
                    verdict, reason = judge.judge(sentence, quotes[marker])
                    c.supported.append((marker, verdict, reason))
    return c


# ---------- the run ----------


@dataclass
class AnswersReport:
    documents: int
    chunks: int
    search: str  # hybrid (with an embedding model) or keyword
    model: str
    judged: bool
    unfindable: list[AnswerQuestion] = field(default_factory=list)
    results: list[Checked] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)
    model_calls: int = 0

    def of(self, answerable: bool) -> list[Checked]:
        return [r for r in self.results if r.question.answerable == answerable]


def run_answers_eval(
    database_url: str,
    llm: LLM,
    settings: Settings,
    embedder: Embedder | None = None,
    judge: Judge | None = None,
    questions: Sequence[AnswerQuestion] | None = None,
    docs: Sequence[search_eval.Document] | None = None,
    make_judge: Callable[[UsageSink], Judge] | None = None,
) -> AnswersReport:
    """Load the corpus into a fresh copy of the schema, then ask and check each question.
    make_judge builds the judge with a usage sink on the eval database (real runs)."""
    search_eval.check_database_name(database_url)
    questions = list(questions if questions is not None else load_questions())
    docs = list(docs if docs is not None else search_eval.load_corpus())
    search_eval.ensure_database(database_url)
    migrate(database_url, search_eval.MIGRATIONS_DIR)
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {search_eval._TABLES} RESTART IDENTITY CASCADE")  # noqa: S608
        item_ids = search_eval.load_into(conn, docs, embedder)
        recorder = UsageRecorder.for_connection(conn, settings)
        if embedder is not None and hasattr(embedder, "on_usage"):
            embedder.on_usage = recorder  # the agent's query embeddings count in the cost
        if judge is None and make_judge is not None:
            judge = make_judge(recorder)
        answerable = [q for q in questions if q.answerable]
        as_search = [
            search_eval.Question(q.id, "answer", q.question, q.document or "", q.passage or "")
            for q in answerable
        ]
        missing = {q.id for q in search_eval.unfindable(conn, as_search, item_ids)}
        report = AnswersReport(
            documents=len(docs),
            chunks=conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"],
            search="hybrid" if embedder else "keyword",
            model=llm.model,
            judged=judge is not None,
            unfindable=[q for q in answerable if q.id in missing],
        )
        ctx = Context(
            db=lambda: nullcontext(conn),
            settings=settings,
            embedder=embedder,
            record=recorder,
        )
        for q in questions:
            new = convs.NewConversation(title=f"eval {q.id}", filters=Filters())
            try:
                events = convs.answer_events(llm, ctx, q.question, new=new)
                answer = next(data for kind, data in events if kind == "final")
            except (TemporaryError, PermanentError) as exc:
                report.results.append(Checked(q, error=f"{type(exc).__name__}: {exc}"))
                continue
            report.results.append(check(q, answer, item_ids, judge))
        totals = conn.execute(
            "SELECT count(*) AS n, coalesce(sum(cost_usd), 0) AS cost FROM model_usage"
        ).fetchone()
        report.model_calls, report.cost_usd = totals["n"], Decimal(totals["cost"])
    return report


# ---------- output ----------


def _mark(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "NO")


def _supported(c: Checked) -> str:
    if c.supported is None:
        return "-"
    yes = sum(1 for _, v, _ in c.supported if v)
    return f"{yes}/{len(c.supported)}"


def summary_lines(report: AnswersReport) -> list[str]:
    ans, una = report.of(True), report.of(False)

    def n(rs: list[Checked], test: Callable[[Checked], bool | None]) -> str:
        return f"{sum(1 for r in rs if test(r))}/{len(rs)}"

    lines = [
        f"- Answerable questions passing every check: {n(ans, lambda r: r.passed)}",
        f"- All citations valid: {n(ans + una, lambda r: r.valid and r.error is None)}",
        f"- A citation points at the expected passage: {n(ans, lambda r: r.expected)}",
        f"- States the key facts: {n(ans, lambda r: r.facts)}",
        f"- Unanswerable questions answered as not grounded: {n(una, lambda r: r.passed)}",
    ]
    if report.judged:
        verdicts = [v for r in report.results for _, v, _ in (r.supported or [])]
        yes = sum(1 for v in verdicts if v)
        lines.append(f"- Cited passages that support their sentence (judge): {yes}/{len(verdicts)}")
    else:
        lines.append("- Cited passages that support their sentence: not judged (no real model)")
    return lines


def format_report(report: AnswersReport) -> str:
    lines = [
        f"Corpus: {report.documents} documents, {report.chunks} chunks; search: "
        f"{report.search}; model: {report.model}; {report.model_calls} model calls, "
        f"estimated ${report.cost_usd:.4f}",
    ]
    if report.unfindable:
        lines.append("QUESTION SET ERROR: these passages are not inside one chunk:")
        lines += [f"  Q{q.id} ({q.document}): {q.passage!r}" for q in report.unfindable]
    lines += ["", *summary_lines(report), ""]
    lines.append(f"{'#':>4}  {'pass':<4}  valid  expected  facts  supported  question")
    for r in report.results:
        lines.append(
            f"{r.question.id:>4}  {'yes' if r.passed else 'NO':<4}  {_mark(r.valid):<5}  "
            f"{_mark(r.expected):<8}  {_mark(r.facts):<5}  {_supported(r):<9}  "
            f"{r.question.question}"
        )
        if r.error:
            lines.append(f"      error: {r.error}")
        elif not r.passed:
            if r.missing_facts:
                lines.append(f"      missing facts: {', '.join(r.missing_facts)}")
            for marker, verdict, reason in r.supported or []:
                if not verdict:
                    lines.append(f"      {marker} not supported: {reason}")
            lines.append(f"      answer: {r.text[:300]!r}")
    return "\n".join(lines)


def answers_markdown(report: AnswersReport, commit: str, when: datetime) -> str:
    lines = [
        ANSWERS_HEADING,
        "",
        "> Written by `python -m mind_dump.eval_answers`. Do not edit by hand: run it again.",
        "> Questions: `backend/eval/answers.json`.",
        "",
        f"- Date: {when:%Y-%m-%d %H:%M} UTC",
        f"- Commit: {commit}",
        f"- Model: {report.model}; search: {report.search}; "
        f"{report.model_calls} model calls, estimated cost ${report.cost_usd:.4f}",
        *summary_lines(report),
        "",
        "| # | Pass | Valid | Expected passage | Facts | Supported | Question |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report.results:
        lines.append(
            f"| {r.question.id} | {'yes' if r.passed else 'no'} | {_mark(r.valid)} | "
            f"{_mark(r.expected)} | {_mark(r.facts)} | {_supported(r)} | {r.question.question} |"
        )
    return "\n".join(lines) + "\n"


PENDING = f"""{ANSWERS_HEADING}

Pending: the answers eval needs a run with `ANTHROPIC_API_KEY` set
(`uv run python -m mind_dump.eval_answers` from `backend/`). No number here is estimated.
"""


def write_section(path: Path, section: str, only_if_missing: bool = False) -> bool:
    """Put the Answers section at the end of results.md, replacing an earlier one. The
    search eval keeps it when it rewrites the file (eval.keep_answers)."""
    old = path.read_text() if path.exists() else ""
    head, found, _ = old.partition(ANSWERS_HEADING)
    if found and only_if_missing:
        return False
    path.write_text(head.rstrip("\n") + "\n\n" + section if head.strip() else section)
    return True


# ---------- the command ----------


def main(
    argv: Sequence[str] | None = None,
    out: Callable[[str], None] = print,
    llm: LLM | None = None,
    judge: Judge | None = None,
) -> int:
    """llm and judge are for tests: a run with an injected model is a fake run and never
    writes the results file."""
    parser = argparse.ArgumentParser(prog="python -m mind_dump.eval_answers", description=__doc__)
    parser.add_argument("--database-url", help="default: the mind_dump_eval database")
    parser.add_argument("--results", type=Path, default=search_eval.RESULTS_FILE)
    parser.add_argument("--no-results", action="store_true", help="print only; write nothing")
    parser.add_argument("--no-judge", action="store_true", help="skip the judge calls")
    args = parser.parse_args(argv)
    dotenv = search_eval._dotenv()
    fake = llm is not None
    settings = Settings(
        database_url="unused",
        migrations_dir=search_eval.MIGRATIONS_DIR,
        llm_model=search_eval._setting("LLM_MODEL", dotenv) or DEFAULT_LLM_MODEL,
        llm_effort=search_eval._setting("LLM_EFFORT", dotenv) or "medium",
        **embedding_settings(lambda name: search_eval._setting(name, dotenv)),
    )
    make_judge = None
    if llm is None:
        key = search_eval._setting("ANTHROPIC_API_KEY", dotenv)
        if key is None:
            out("No ANTHROPIC_API_KEY: the answers eval is pending (nothing was asked).")
            if not args.no_results and write_section(args.results, PENDING, only_if_missing=True):
                out(f"Wrote a pending Answers section to {args.results}")
            return 0
        client = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)
        llm = ClaudeLLM(client, settings.llm_model, settings.llm_effort)
        if not args.no_judge:

            def make_judge(sink: UsageSink) -> Judge:
                return ClaudeJudge(client, settings.llm_model, sink)

    embedder: Embedder | None = None if fake else create_embedder(settings)

    url = args.database_url or search_eval.default_database_url(dotenv)
    out(f"Database: {search_eval.check_database_name(url)}; model: {llm.model}")
    report = run_answers_eval(url, llm, settings, embedder, judge, make_judge=make_judge)
    out(format_report(report))
    if fake:
        out("\nA fake model: these checks test the harness only, so no results file is written.")
    elif not args.no_results:
        write_section(
            args.results, answers_markdown(report, search_eval._commit(), datetime.now(UTC))
        )
        out(f"\nWrote {args.results}")
    return 1 if report.unfindable else 0


if __name__ == "__main__":
    sys.exit(main())
