"""The worker (D6): claims jobs and turns saved items into chunks with vectors. Run it with

    python -m mind_dump.worker          (in Compose: the `worker` service)

One job, step by step:
1. Claim it (jobs.claim): status running, attempts + 1, a lease. That commits at once.
2. A link is fetched, then landed, recorded and versioned by pages.process_fetch (D28,
   D32). Those steps commit on their own, so the fetch is kept even if a later step
   fails. A `rebuild` job skips the network and rebuilds the versions from landed files.
   An `embed` job skips this step: it only embeds what is there.
3. Read the source text, with no lock held: a note's body, a link's current version, or
   a file's text (D7b). A file's text is extracted from the stored bytes the first time
   (and on `rebuild`): a PDF page by page with pypdf, an image through a description
   from Claude. Later runs reuse the stored text: no second model call.
4. Plan the write, and do all network work for it NOW, before any transaction opens:
   - Same text hash as the current chunks (D28's text hash): keep the rows and make no
     embedding call, except for rows with no vector or a vector from another model,
     which are embedded in place (a key was added, or EMBEDDING_MODEL changed).
   - Otherwise: chunk the text (a PDF page by page, D36) and embed every chunk (D8).
   With no embedder configured (no key for EMBEDDING_PROVIDER), chunks are saved
   without vectors.
5. One transaction (D30): check we still own the job, lock the item, check its text hash
   is still the one we chunked and embedded (if not: a temporary error, and the job runs
   again), write the file's text, replace the chunk set (DELETE, then INSERT, vectors
   included) or update vectors in place, mark the job done, COMMIT. Work first, done
   last: a crash before COMMIT rolls back all of it, so the old chunks stay and the job
   runs again. No network inside: a slow model API never holds a lock.

Failures (D35):
- Temporary, so retried (1 minute, then 5 minutes, then dead): timeouts, connection
  errors, HTTP 429 and any 5xx, from a website or a model API. Also any unexpected error
  (a bug, a lost database connection): it gets the same 3 tries, then shows its reason.
- Permanent, so dead at once: any other 4xx (a model API's 401 names the key to check);
  a 2xx page of a type we can't read; a page larger than fetcher.MAX_BYTES; a page with
  no readable text; too many redirects or a non-http URL; a PDF that is encrypted,
  damaged or has no text layer; an image with no ANTHROPIC_API_KEY, too large for the
  API, or that Claude declines to describe.
- Either way, a fetch that got an HTTP answer is landed and recorded first (D28), and an
  item's earlier chunks and vectors stay until a later run succeeds.
"""

import logging
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import httpx2
import psycopg
from psycopg import Connection
from psycopg.rows import DictRow, dict_row

from mind_dump import jobs, pages
from mind_dump.chunking import Chunk, chunk_pages, chunk_text
from mind_dump.config import load_settings
from mind_dump.describe import Describer, create_describer
from mind_dump.embeddings import (
    Embedder,
    Vector,
    create_embedder,
    no_embedder_reason,
    to_pgvector,
)
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.extract import extract_text, text_sha256
from mind_dump.fetcher import PageTooLarge, fetch, make_client
from mind_dump.filetext import FileText, extract_file
from mind_dump.jobs import Job
from mind_dump.storage import Storage, create_storage
from mind_dump.uploads import file_key
from mind_dump.usage import UsageRecorder

__all__ = ["PermanentError", "TemporaryError", "Worker", "check_status"]

log = logging.getLogger("mind_dump.worker")

Conn = Connection[DictRow]
IDLE_SLEEP = 1.0  # seconds to wait when the queue is empty: quick to react, cheap for Postgres


@dataclass(frozen=True)
class Source:
    """The text a job works from, and its hash, read in step 3."""

    text: str
    sha: str
    file_text: FileText | None = None  # files: the text with its pages and extractor
    new_file_text: str | None = None  # set (to the file's sha256) when freshly extracted


@dataclass(frozen=True)
class Plan:
    """What step 5 writes. Every vector in it was made before the transaction opened."""

    keep: bool  # True: the text is unchanged, the rows stay
    updates: list[tuple[int, Vector]] = field(default_factory=list)  # keep: (chunk id, vector)
    chunks: list[Chunk] = field(default_factory=list)  # replace: the new set
    vectors: list[Vector] | None = None  # replace: one per chunk; None with no embedder


def _now() -> datetime:
    return datetime.now(UTC)


def _http_reason(code: int) -> str:
    try:
        return f"HTTP {code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


def check_status(code: int, url: str) -> None:
    """Raise if this HTTP status means the job can't go on (D35)."""
    if code == 429 or 500 <= code <= 599:
        raise TemporaryError(f"{_http_reason(code)} from {url}")
    if not 200 <= code <= 299:
        raise PermanentError(f"{_http_reason(code)} from {url}")


@dataclass
class Worker:
    """Everything the worker touches comes in from outside, so tests can swap it:
    a fake website (http), a fake clock, a short lease, fake model clients (embedder,
    describer; None means not configured), and a hook that runs just before COMMIT (the
    crash tests use it to kill the worker at the worst moment)."""

    conn: Conn  # autocommit: each step's `with conn.transaction()` is a real COMMIT
    storage: Storage
    http: httpx2.Client
    clock: Callable[[], datetime] = _now
    lease: timedelta = jobs.DEFAULT_LEASE
    extract: pages.Extractor = extract_text
    embedder: Embedder | None = None  # D8: None means chunks keep no vectors
    describer: Describer | None = None  # D7b: None means images can't be described
    no_embedder: str = "no embedding key set"  # why embedder is None, for the log
    before_commit: Callable[[Job], None] | None = field(default=None)

    def run(self, stop: threading.Event, idle: float = IDLE_SLEEP) -> None:
        """Work until `stop` is set. A job in progress is always finished first."""
        while not stop.is_set():
            if self.run_once() is None:
                stop.wait(idle)

    def run_once(self) -> Job | None:
        """Claim and process one job. Returns the job claimed, or None if none was due."""
        if not self.conn.autocommit:
            raise ValueError("The worker needs an autocommit connection")
        job = jobs.claim(self.conn, self.clock(), self.lease)
        if job is None:
            return None
        log.info("job %s: item %s, %s, try %s", job.id, job.item_id, job.kind, job.attempts)
        try:
            outcome = self._process(job)
            log.info("job %s: done, %s", job.id, outcome)
        except (jobs.LeaseLost, pages.LinkNotFound) as exc:
            log.warning("job %s: stopped, nothing written: %s", job.id, exc)
        except PermanentError as exc:
            self._finish(job, "dead", str(exc))
        except TemporaryError as exc:
            self._finish(job, "failed", str(exc))
        except Exception as exc:  # a bug or a lost connection: retry, then show the reason
            log.exception("job %s: unexpected error", job.id)
            self._finish(job, "failed", f"Unexpected error: {type(exc).__name__}: {exc}")
        return job

    def _finish(self, job: Job, how: str, error: str) -> None:
        try:
            if how == "dead":
                jobs.mark_dead(self.conn, job, self.clock(), error)
                status = "dead"
            else:
                status = jobs.mark_failed(self.conn, job, self.clock(), error)
        except jobs.LeaseLost as exc:
            log.warning("job %s: could not record the failure: %s", job.id, exc)
            return
        log.warning("job %s: %s: %s", job.id, status, error)

    def _process(self, job: Job) -> str:
        item = self.conn.execute(
            "SELECT type, url, sha256, media_type FROM items WHERE id = %s", (job.item_id,)
        ).fetchone()
        if item is None:
            raise jobs.LeaseLost(f"item {job.item_id} was deleted")
        if item["type"] == "link" and job.kind == "rebuild":
            pages.rebuild_versions(self.conn, self.storage, job.item_id, self.extract)
        elif item["type"] == "link" and job.kind == "process":
            self._fetch_link(job.item_id, item["url"])
        # Steps 3 and 4: all network work (describe, embed) happens here, outside any
        # transaction, so no row stays locked while a model API thinks.
        source = self._read_source(job, item)
        plan = self._plan(job.item_id, source, force=job.kind == "rebuild")
        return self._commit(job, item, source, plan)

    def _fetch_link(self, item_id: int, url: str) -> None:
        try:
            result = fetch(self.http, url, now=self.clock)
        except httpx2.TimeoutException as exc:
            raise TemporaryError(f"Timed out fetching {url}: {exc}") from exc
        except (httpx2.NetworkError, httpx2.RemoteProtocolError, httpx2.ProxyError) as exc:
            raise TemporaryError(f"Connection error fetching {url}: {exc}") from exc
        except PageTooLarge as exc:
            raise PermanentError(str(exc)) from exc
        except httpx2.HTTPError as exc:  # too many redirects, not http(s), bad encoding
            raise PermanentError(f"Can't fetch {url}: {type(exc).__name__}: {exc}") from exc
        # Land and record first, whatever the status (D28): a 404 is evidence too.
        pages.process_fetch(self.conn, self.storage, item_id, result, self.extract)
        check_status(result.http_status, url)
        if not pages.parseable(result.http_status, result.content_type):
            raise PermanentError(
                f"Can't read pages of type {result.content_type or 'unknown'} yet ({url})"
            )

    # ---------- step 3: the text, read with no lock held ----------

    def _read_source(self, job: Job, item: DictRow) -> Source:
        if item["type"] == "note":
            body = self._note_body(job.item_id)
            return Source(body, text_sha256(body))
        if item["type"] == "link":
            version = self._current_version(job.item_id)
            return Source(version["text"], text_sha256(version["text"]))
        return self._file_source(job, item)

    def _note_body(self, item_id: int) -> str:
        row = self.conn.execute("SELECT body FROM items WHERE id = %s", (item_id,)).fetchone()
        if row is None:
            raise jobs.LeaseLost(f"item {item_id} was deleted")
        return row["body"]

    def _current_version(self, item_id: int) -> DictRow:
        version = self.conn.execute(
            "SELECT id, text FROM item_versions WHERE item_id = %s AND valid_to IS NULL",
            (item_id,),
        ).fetchone()
        if version is None:
            raise PermanentError("The page has no readable text")
        return version

    def _file_source(self, job: Job, item: DictRow) -> Source:
        """A file's text: the stored one, or (first run, or a `rebuild`) extracted afresh
        from the stored bytes. Extracting may call Claude (images); the result is only
        written in the D30 transaction, together with its chunks."""
        stored = self.conn.execute(
            "SELECT text, file_sha256, page_starts, page_ends, extractor"
            " FROM file_texts WHERE item_id = %s",
            (job.item_id,),
        ).fetchone()
        if stored is not None and stored["file_sha256"] == item["sha256"] and job.kind != "rebuild":
            spans = None
            if stored["page_starts"] is not None:
                spans = list(zip(stored["page_starts"], stored["page_ends"], strict=True))
            text = FileText(stored["text"], spans, stored["extractor"])
            return Source(text.text, text_sha256(text.text), file_text=text)
        key = file_key(item["sha256"])
        try:
            data = self.storage.get(key)
        except FileNotFoundError as exc:
            raise PermanentError(f"The stored file {key} is missing; upload it again") from exc
        text = extract_file(data, item["media_type"], self.describer)
        return Source(
            text.text, text_sha256(text.text), file_text=text, new_file_text=item["sha256"]
        )

    # ---------- step 4: what to write, and the vectors for it ----------

    def _plan(self, item_id: int, source: Source, force: bool) -> Plan:
        current = self.conn.execute(
            """
            SELECT id, text, source_sha256, embedding IS NOT NULL AS embedded, embedding_model
            FROM chunks WHERE item_id = %s ORDER BY chunk_index
            """,
            (item_id,),
        ).fetchall()
        if not force and current and current[0]["source_sha256"] == source.sha:
            # Same text (D28's text hash): keep the rows and make no embedding call...
            behind = [] if self.embedder is None else [
                r for r in current
                if not r["embedded"] or r["embedding_model"] != self.embedder.model
            ]  # fmt: skip
            if not behind:
                return Plan(keep=True)
            # ...unless some rows have no vector, or one from another model: embed those
            # in place (a key was added, or EMBEDDING_MODEL changed).
            vectors = self._embed([r["text"] for r in behind])
            updates = [(r["id"], v) for r, v in zip(behind, vectors, strict=True)]
            return Plan(keep=True, updates=updates)
        if source.file_text is not None and source.file_text.pages is not None:
            pieces = chunk_pages(source.text, source.file_text.pages)
        else:
            pieces = chunk_text(source.text)
        vectors = self._embed([c.text for c in pieces]) if self.embedder else None
        return Plan(keep=False, chunks=pieces, vectors=vectors)

    def _embed(self, texts: list[str]) -> list[Vector]:
        if self.embedder is None:  # cannot happen: callers check first
            raise RuntimeError("no embedder")
        vectors = self.embedder.embed_documents(texts)
        if len(vectors) != len(texts):
            raise TemporaryError(f"Got {len(vectors)} vectors for {len(texts)} chunks")
        return vectors

    # ---------- step 5: one transaction (D30) ----------

    def _text_now(self, job: Job, item: DictRow, source: Source) -> tuple[str, int | None]:
        """The source text's hash as it is NOW, under the item's row lock, and (links)
        the current version's id."""
        locked = self.conn.execute(
            "SELECT type, body, sha256 FROM items WHERE id = %s FOR UPDATE", (job.item_id,)
        ).fetchone()
        if locked is None:
            raise jobs.LeaseLost(f"item {job.item_id} was deleted")
        if locked["type"] == "note":
            return text_sha256(locked["body"]), None
        if locked["type"] == "link":
            version = self._current_version(job.item_id)
            return text_sha256(version["text"]), version["id"]
        if source.new_file_text is not None:
            # Freshly extracted from these bytes; the bytes of a file item never change.
            same = locked["sha256"] == source.new_file_text
            return (source.sha if same else "file replaced"), None
        stored = self.conn.execute(
            "SELECT text_sha256 FROM file_texts WHERE item_id = %s", (job.item_id,)
        ).fetchone()
        return (stored["text_sha256"] if stored else "no text"), None

    def _commit(self, job: Job, item: DictRow, source: Source, plan: Plan) -> str:
        """D30: write everything and mark the job done in ONE transaction. No network here:
        the vectors were made before it opened (step 4)."""
        with self.conn.transaction():
            jobs.lock_owned(self.conn, job)
            sha_now, version_id = self._text_now(job, item, source)
            if sha_now != source.sha:
                # Someone changed the text after we read and embedded it. Writing now would
                # store vectors of the old text; the next try reads the new one.
                raise TemporaryError(
                    "The text changed while it was being embedded; the job will run again"
                )
            if source.new_file_text is not None and source.file_text is not None:
                self._store_file_text(job.item_id, source.file_text, source)
            if plan.keep:
                outcome = self._keep_chunks(job.item_id, version_id, source.sha, plan)
            else:
                outcome = self._replace_chunks(job.item_id, version_id, source.sha, plan)
            if self.before_commit is not None:
                self.before_commit(job)
            jobs.mark_done(self.conn, job, self.clock())
        return outcome

    def _store_file_text(self, item_id: int, text: FileText, source: Source) -> None:
        starts = [s for s, _ in text.pages] if text.pages is not None else None
        ends = [e for _, e in text.pages] if text.pages is not None else None
        self.conn.execute(
            """
            INSERT INTO file_texts (item_id, text, text_sha256, file_sha256,
                                    page_starts, page_ends, extractor)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (item_id) DO UPDATE SET
                text = EXCLUDED.text, text_sha256 = EXCLUDED.text_sha256,
                file_sha256 = EXCLUDED.file_sha256, page_starts = EXCLUDED.page_starts,
                page_ends = EXCLUDED.page_ends, extractor = EXCLUDED.extractor,
                created_at = now()
            """,
            (item_id, text.text, source.sha, source.new_file_text, starts, ends, text.extractor),
        )

    def _keep_chunks(self, item_id: int, version_id: int | None, sha: str, plan: Plan) -> str:
        # Same text: keep the rows. Only re-point them if the version row was replaced
        # (a rebuild of versions gives the same text a new row).
        self.conn.execute(
            """
            UPDATE chunks SET item_version_id = %s
            WHERE item_id = %s AND item_version_id IS DISTINCT FROM %s
            """,
            (version_id, item_id, version_id),
        )
        if not plan.updates:
            return "text unchanged, chunks kept"
        model = self.embedder.model if self.embedder else None
        for chunk_id, vector in plan.updates:
            cur = self.conn.execute(
                """
                UPDATE chunks SET embedding = %s::vector, embedding_model = %s
                WHERE id = %s AND source_sha256 = %s
                """,
                (to_pgvector(vector), model, chunk_id, sha),
            )
            if cur.rowcount != 1:  # the row was replaced since step 4
                raise TemporaryError("The chunks changed while they were being embedded")
        return f"text unchanged, {len(plan.updates)} chunk(s) embedded in place"

    def _replace_chunks(self, item_id: int, version_id: int | None, sha: str, plan: Plan) -> str:
        model = self.embedder.model if self.embedder and plan.vectors is not None else None
        vectors = plan.vectors or [None] * len(plan.chunks)
        self.conn.execute("DELETE FROM chunks WHERE item_id = %s", (item_id,))
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text, page,
                                    source_sha256, item_version_id, embedding, embedding_model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                """,
                [
                    (item_id, c.index, c.start_char, c.end_char, c.text, c.page, sha, version_id)
                    + ((to_pgvector(v), model) if v is not None else (None, None))
                    for c, v in zip(plan.chunks, vectors, strict=True)
                ],
            )
        if plan.vectors is None and plan.chunks:
            log.info(
                "item %s: %s, so %s chunk(s) saved without vectors; "
                "set the key, then run python -m mind_dump.embed_missing",
                item_id,
                self.no_embedder,
                len(plan.chunks),
            )
            return f"{len(plan.chunks)} chunk(s) written, no vectors (no embedder)"
        return f"{len(plan.chunks)} chunk(s) written and embedded"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        log.info("%s received: finishing the current job, then stopping", signal.strsignal(signum))
        stop.set()

    # Compose sends SIGTERM on `docker compose stop`; Ctrl-C sends SIGINT.
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with (
        psycopg.connect(
            settings.database_url,
            autocommit=True,
            row_factory=dict_row,
            application_name="mind-dump-worker",
        ) as conn,
        make_client() as http,
    ):
        # D39: every embedding and description call writes a model_usage row. Through this
        # autocommit connection, each row commits at once: the model clients are only
        # called in steps 3 and 4, before the D30 transaction opens.
        recorder = UsageRecorder.for_connection(conn, settings)
        embedder = create_embedder(settings, recorder)
        describer = create_describer(settings, recorder)
        log.info(
            "worker started; embeddings: %s; image descriptions: %s",
            f"{settings.embedding.label} {embedder.model}"
            if embedder
            else f"off ({no_embedder_reason(settings)}): chunks keep no vectors",
            describer.model if describer else "off (no ANTHROPIC_API_KEY): images end in error",
        )
        Worker(
            conn=conn,
            storage=create_storage(settings),
            http=http,
            embedder=embedder,
            describer=describer,
            no_embedder=no_embedder_reason(settings),
        ).run(stop)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
