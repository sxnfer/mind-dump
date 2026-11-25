"""Measure search (M4): how often does the right passage come back?

    uv run python -m mind_dump.eval                     (from backend/)
    uv run python -m mind_dump.eval --modes keyword --no-results --min-keyword-recall 0.3

What it does, in order:
1. Connect to a database of its own (default `mind_dump_eval`, created if missing). It
   empties its tables, so it refuses any database whose name doesn't end in _eval or
   _test: your real items in `mind_dump` are never touched.
2. Load the corpus (backend/eval/corpus/*.md) through the REAL pipeline: each document is
   saved by the API's own create_item (an item and its job), then the worker's own code
   chunks it (D36) and, with an embedding model, embeds it (D8).
3. Check the golden set (backend/eval/golden.json): every expected passage must sit
   whole inside one chunk of its document. A passage cut across two chunks could never
   be found, and would count as a search failure that isn't one.
4. Ask each question through search.search(), the function GET /search uses, in each
   mode it can run: keyword always; vector and hybrid only with an embedding model.
5. Score. A question is a hit at rank r when the r-th result is a chunk of the expected
   document that contains the passage.
       recall@5  the share of questions with a hit in the top 5
       MRR@10    the mean of 1/r over all questions (0 when no hit in the top 10):
                 1.0 means the right passage always came first
6. Print the numbers and every miss, and write docs/results.md (unless --no-results).

The embedding model: --fake-embedder (hash vectors, for tests: the numbers mean nothing),
else VOYAGE_API_KEY from the environment or from the repo's .env. Keys are never printed.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import psycopg
from fastapi import Response
from psycopg.rows import DictRow, dict_row

from mind_dump import search as search_mod
from mind_dump.config import DEFAULT_EMBEDDING_MODEL
from mind_dump.embeddings import Embedder, FakeEmbedder, VoyageEmbedder
from mind_dump.items import create_item
from mind_dump.migrate import migrate
from mind_dump.models import NewNote
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent
EVAL_DIR = _BACKEND / "eval"
RESULTS_FILE = _REPO / "docs" / "results.md"
MIGRATIONS_DIR = _BACKEND / "migrations"
DEFAULT_DATABASE = "mind_dump_eval"
TOP_K = 5  # recall@5
DEPTH = 10  # MRR@10: how far down the list a hit still counts for MRR
MODES: tuple[search_mod.Mode, ...] = ("keyword", "vector", "hybrid")
_TABLES = (
    "items, tags, item_tags, fetches, item_versions, jobs, chunks, file_texts, "
    "conversations, messages, model_usage, ask_requests"
)


@dataclass(frozen=True)
class Document:
    slug: str  # the file name without .md; golden.json refers to it
    title: str
    body: str


@dataclass(frozen=True)
class Question:
    id: int
    kind: str  # words, paraphrase or identifier
    question: str
    document: str
    passage: str


@dataclass(frozen=True)
class Answer:
    question: Question
    rank: int | None  # 1-based rank of the first right chunk in the top DEPTH; None: miss
    # The first rank of ANY chunk of the right document: on a miss, it tells "found the
    # document, but another passage of it" apart from "never found the document".
    doc_rank: int | None = None


@dataclass
class ModeResult:
    mode: search_mod.Mode
    answers: list[Answer]

    @property
    def hits_at_k(self) -> int:
        return sum(1 for a in self.answers if a.rank is not None and a.rank <= TOP_K)

    @property
    def recall_at_k(self) -> float:
        return self.hits_at_k / len(self.answers) if self.answers else 0.0

    @property
    def mrr(self) -> float:
        if not self.answers:
            return 0.0
        return sum(1 / a.rank for a in self.answers if a.rank is not None) / len(self.answers)


@dataclass
class Report:
    documents: int
    chunks: int
    questions: int
    embedding_model: str | None
    unfindable: list[Question] = field(default_factory=list)  # passage not inside one chunk
    modes: list[ModeResult] = field(default_factory=list)

    def mode(self, name: str) -> ModeResult | None:
        return next((m for m in self.modes if m.mode == name), None)


def load_corpus(directory: Path = EVAL_DIR / "corpus") -> list[Document]:
    """Each file: '# Title', a blank line, then the text (paragraphs split by blank lines)."""
    docs = []
    for path in sorted(directory.glob("*.md")):
        first, _, body = path.read_text(encoding="utf-8").partition("\n")
        if not first.startswith("# "):
            raise ValueError(f"{path.name} must start with '# Title'")
        docs.append(Document(path.stem, first[2:].strip(), body.strip()))
    return docs


def load_golden(path: Path = EVAL_DIR / "golden.json") -> list[Question]:
    return [Question(**q) for q in json.loads(path.read_text(encoding="utf-8"))["questions"]]


# ---------- the database ----------


def check_database_name(url: str) -> str:
    """The eval empties its tables: only a database meant for it, or for the tests."""
    name = psycopg.conninfo.conninfo_to_dict(url).get("dbname") or ""
    if not (name.endswith("_eval") or name.endswith("_test")):
        raise RuntimeError(
            f"Refusing to run the eval against {name!r}: it empties the tables. "
            f"Use a database whose name ends in _eval or _test, e.g. {DEFAULT_DATABASE!r}"
        )
    return name


def ensure_database(url: str) -> None:
    """Create the database on first use, like the tests do (no CREATE in a transaction)."""
    name = check_database_name(url)
    admin_url = psycopg.conninfo.make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return
        admin.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 (name checked above)


def _refuse_network(request: httpx2.Request) -> httpx2.Response:
    raise RuntimeError(f"The eval corpus is notes only; nothing may fetch {request.url}")


def load_into(conn: Conn, docs: Sequence[Document], embedder: Embedder | None) -> dict[str, int]:
    """Save each document as a note through the API's own code, then let the worker's own
    code chunk and embed it. Returns slug -> item id."""
    ids = {}
    for doc in docs:
        item = create_item(NewNote(type="note", title=doc.title, body=doc.body), conn, Response())
        ids[doc.slug] = item.id

    def db_clock() -> datetime:
        # The database's clock, so the jobs it just queued (run_after = now()) are due even
        # if this machine's clock runs a little behind.
        return conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]

    with (
        tempfile.TemporaryDirectory() as tmp,
        httpx2.Client(transport=httpx2.MockTransport(_refuse_network)) as http,
    ):
        worker = Worker(
            conn=conn, storage=LocalStorage(Path(tmp)), http=http, clock=db_clock, embedder=embedder
        )
        while worker.run_once() is not None:
            pass
    unfinished = conn.execute(
        "SELECT item_id, status, error FROM jobs WHERE status <> 'done' ORDER BY item_id"
    ).fetchall()
    if unfinished:
        detail = "; ".join(f"item {r['item_id']}: {r['status']} {r['error']}" for r in unfinished)
        raise RuntimeError(f"The worker did not finish every document: {detail}")
    return ids


def unfindable(conn: Conn, questions: Sequence[Question], ids: dict[str, int]) -> list[Question]:
    """Golden passages that are not inside one chunk of their document."""
    missing = []
    for q in questions:
        if q.document not in ids:
            missing.append(q)
            continue
        row = conn.execute(
            "SELECT 1 FROM chunks WHERE item_id = %s AND strpos(text, %s) > 0",
            (ids[q.document], q.passage),
        ).fetchone()
        if row is None:
            missing.append(q)
    return missing


def ask(
    conn: Conn, q: Question, item_id: int, mode: search_mod.Mode, embedder: Embedder | None
) -> Answer:
    result = search_mod.search(conn, q.question, mode, limit=DEPTH, embedder=embedder)
    if result.mode != mode:  # never score a fallback as if it were the mode asked for
        raise RuntimeError(f"{mode} search fell back to {result.mode}: {result.notice}")
    doc_rank = None
    for rank, hit in enumerate(result.hits, 1):
        if hit.item_id != item_id:
            continue
        doc_rank = doc_rank or rank
        if q.passage in hit.text:
            return Answer(q, rank, doc_rank)
    return Answer(q, None, doc_rank)


def run_eval(
    database_url: str,
    modes: Sequence[search_mod.Mode] = ("keyword",),
    embedder: Embedder | None = None,
    docs: Sequence[Document] | None = None,
    questions: Sequence[Question] | None = None,
) -> Report:
    """Load the corpus into a fresh copy of the schema and score each mode."""
    check_database_name(database_url)
    if embedder is None and any(m != "keyword" for m in modes):
        raise ValueError("vector and hybrid modes need an embedding model")
    docs = list(docs if docs is not None else load_corpus())
    questions = list(questions if questions is not None else load_golden())
    ensure_database(database_url)
    migrate(database_url, MIGRATIONS_DIR)
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE")  # noqa: S608 (constant)
        ids = load_into(conn, docs, embedder)
        chunks = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        report = Report(
            documents=len(docs),
            chunks=chunks,
            questions=len(questions),
            embedding_model=embedder.model if embedder else None,
            unfindable=unfindable(conn, questions, ids),
        )
        for mode in modes:
            answers = [ask(conn, q, ids[q.document], mode, embedder) for q in questions]
            report.modes.append(ModeResult(mode, answers))
    return report


# ---------- output ----------


def _rank(answer: Answer | None) -> str:
    if answer is None:
        return "-"
    if answer.rank is not None:
        return str(answer.rank)
    if answer.doc_rank is not None:
        return f"miss (other chunk at {answer.doc_rank})"
    return "miss"


def format_report(report: Report) -> str:
    lines = [
        f"Corpus: {report.documents} documents, {report.chunks} chunks, "
        f"{report.questions} questions; embedding model: {report.embedding_model or 'none'}",
    ]
    if report.unfindable:
        lines.append("GOLDEN SET ERROR: these passages are not inside one chunk of their document:")
        lines += [f"  Q{q.id} ({q.document}): {q.passage!r}" for q in report.unfindable]
    lines.append("")
    lines.append(f"{'mode':<8}  recall@{TOP_K}  MRR@{DEPTH}  hits@{TOP_K}")
    for m in report.modes:
        lines.append(
            f"{m.mode:<8}  {m.recall_at_k:>8.2f}  {m.mrr:>6.2f}  {m.hits_at_k:>3}/{len(m.answers)}"
        )
    for m in report.modes:
        misses = [a for a in m.answers if a.rank is None or a.rank > TOP_K]
        lines.append("")
        lines.append(f"{m.mode}: {len(misses)} not in the top {TOP_K}")
        for a in misses:
            lines.append(
                f"  Q{a.question.id:<2} {a.question.kind:<10} {_rank(a):<24} {a.question.question}"
            )
    return "\n".join(lines)


def _commit() -> str:
    try:
        sha = subprocess.run(  # noqa: S603 (fixed arguments)
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 (git on PATH)
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(  # noqa: S603 (fixed arguments)
            ["git", "status", "--porcelain", "--", "backend"],  # noqa: S607 (git on PATH)
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha} (with uncommitted backend changes)" if dirty else sha


def results_markdown(report: Report, commit: str, when: datetime) -> str:
    rows = []
    for name in MODES:
        m = report.mode(name)
        if m is not None:
            rows.append(
                f"| {name} | {m.recall_at_k:.2f} | {m.mrr:.2f} | {m.hits_at_k}/{len(m.answers)} |"
            )
        else:
            rows.append(f"| {name} | pending | pending | needs a run with `VOYAGE_API_KEY` |")
    pending = [n for n in MODES if report.mode(n) is None]
    lines = [
        "# Search eval results",
        "",
        "> Written by `python -m mind_dump.eval` (M4). Do not edit by hand: run it again.",
        "> How it works: `backend/src/mind_dump/eval.py`. Corpus and questions: `backend/eval/`.",
        "",
        f"- Date: {when:%Y-%m-%d %H:%M} UTC",
        f"- Commit: {commit}",
        f"- Corpus: {report.documents} documents, {report.chunks} chunks, "
        f"{report.questions} questions",
        f"- Embedding model: {report.embedding_model or 'none (no VOYAGE_API_KEY)'}",
        f"- A hit: a chunk of the expected document that contains the expected passage. "
        f"recall@{TOP_K}: share of questions with a hit in the top {TOP_K}. "
        f"MRR@{DEPTH}: mean of 1/rank of the first hit (0 if none in the top {DEPTH}).",
        "",
        f"| Mode | recall@{TOP_K} | MRR@{DEPTH} | Hits in top {TOP_K} |",
        "| --- | --- | --- | --- |",
        *rows,
        "",
    ]
    if pending:
        lines += [
            f"The {' and '.join(pending)} rows are pending: this run had no embedding model, so "
            "only keyword search could be measured. Run the eval with `VOYAGE_API_KEY` set to "
            "fill them in. No number here is estimated.",
            "",
        ]
    if report.unfindable:
        lines += [
            "**Golden set error:** some passages are not inside one chunk: "
            + ", ".join(f"Q{q.id}" for q in report.unfindable),
            "",
        ]
    header = "| # | Kind | Question | " + " | ".join(m.mode for m in report.modes) + " |"
    lines += [
        "## Rank of the right passage, per question",
        "",
        f"A number is the rank of the first right chunk (top {DEPTH}). miss: not in the top "
        f"{DEPTH}; miss (other chunk at r): the right document came back at rank r, but "
        "with a chunk that doesn't hold the passage.",
        "",
        header,
        "| --- | --- | --- | " + " | ".join("---" for _ in report.modes) + " |",
    ]
    by_mode = [{a.question.id: a for a in m.answers} for m in report.modes]
    for q in sorted({a.question.id: a.question for m in report.modes for a in m.answers}.values(),
                    key=lambda q: q.id):  # fmt: skip
        ranks = " | ".join(_rank(answers.get(q.id)) for answers in by_mode)
        lines.append(f"| {q.id} | {q.kind} | {q.question} | {ranks} |")
    return "\n".join(lines) + "\n"


# ---------- the command ----------


def _dotenv() -> dict[str, str]:
    """The repo's .env as a dict (never printed): the Compose password, port and keys."""
    env: dict[str, str] = {}
    path = _REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def default_database_url(dotenv: dict[str, str]) -> str:
    """EVAL_DATABASE_URL; else DATABASE_URL with the database swapped for mind_dump_eval;
    else the Compose database from .env, as the tests build theirs."""
    if url := os.environ.get("EVAL_DATABASE_URL"):
        return url
    if url := os.environ.get("DATABASE_URL"):
        return psycopg.conninfo.make_conninfo(url, dbname=DEFAULT_DATABASE)
    password = dotenv.get("POSTGRES_PASSWORD", "")
    port = dotenv.get("DB_PORT") or "5432"
    return f"postgresql://mind_dump:{password}@localhost:{port}/{DEFAULT_DATABASE}"


def _setting(name: str, dotenv: dict[str, str]) -> str | None:
    value = (os.environ.get(name) or dotenv.get(name) or "").strip()
    return value or None


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m mind_dump.eval", description=__doc__)
    parser.add_argument("--database-url", help="default: the mind_dump_eval database")
    parser.add_argument(
        "--modes",
        help="comma-separated: keyword,vector,hybrid (default: all with a key, else keyword)",
    )
    parser.add_argument(
        "--fake-embedder", action="store_true", help="hash vectors, no network (for tests)"
    )
    parser.add_argument("--results", type=Path, default=RESULTS_FILE, help="where to write")
    parser.add_argument("--no-results", action="store_true", help="print only; write nothing")
    parser.add_argument(
        "--min-keyword-recall",
        type=float,
        help=f"exit 1 when keyword recall@{TOP_K} is below this (the CI floor)",
    )
    args = parser.parse_args(argv)
    dotenv = _dotenv()

    wanted = [m.strip() for m in args.modes.split(",")] if args.modes else None
    if wanted is not None and any(m not in MODES for m in wanted):
        parser.error(f"--modes takes {', '.join(MODES)}")
    embedder: Embedder | None = None
    needs_model = wanted is None or any(m != "keyword" for m in wanted)
    if args.fake_embedder:
        embedder = FakeEmbedder()
    elif needs_model and (key := _setting("VOYAGE_API_KEY", dotenv)):
        model = _setting("EMBEDDING_MODEL", dotenv) or DEFAULT_EMBEDDING_MODEL
        embedder = VoyageEmbedder(key, model)
    if wanted is None:
        wanted = list(MODES) if embedder else ["keyword"]
        if embedder is None:
            out("No VOYAGE_API_KEY: measuring keyword search only.")
    elif embedder is None and any(m != "keyword" for m in wanted):
        out("vector and hybrid need an embedding model: set VOYAGE_API_KEY.")
        return 2
    modes: list[search_mod.Mode] = [m for m in MODES if m in wanted]  # type: ignore[misc]

    url = args.database_url or default_database_url(dotenv)
    out(f"Database: {check_database_name(url)}; modes: {', '.join(modes)}")
    report = run_eval(url, modes, embedder)
    out(format_report(report))

    if args.fake_embedder:
        out("\n--fake-embedder: vector numbers are meaningless, so no results file is written.")
    elif not args.no_results:
        args.results.write_text(results_markdown(report, _commit(), datetime.now(UTC)))
        out(f"\nWrote {args.results}")

    if report.unfindable:
        return 1
    keyword = report.mode("keyword")
    if args.min_keyword_recall is not None and keyword is not None:
        if keyword.recall_at_k < args.min_keyword_recall:
            out(
                f"FAIL: keyword recall@{TOP_K} {keyword.recall_at_k:.2f} is below the floor "
                f"{args.min_keyword_recall:.2f}"
            )
            return 1
        out(f"OK: keyword recall@{TOP_K} {keyword.recall_at_k:.2f} >= {args.min_keyword_recall}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
