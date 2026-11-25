"""The worker (D6): claims jobs and turns saved items into chunks. Run it with

    python -m mind_dump.worker          (in Compose: the `worker` service)

One job, step by step:
1. Claim it (jobs.claim): status running, attempts + 1, a lease. That commits at once.
2. A link is fetched, then landed, recorded and versioned by pages.process_fetch (D28,
   D32). Those steps commit on their own, so the fetch is kept even if a later step
   fails. A `rebuild` job skips the network and rebuilds the versions from landed files.
3. One transaction (D30): check we still own the job, read the item's text, replace its
   chunks (DELETE, then INSERT), mark the job done, COMMIT. Work first, done last: a crash
   before COMMIT rolls back all of it, so the old chunks stay and the job runs again.
   If the text's SHA-256 equals the one the current chunks came from, the chunks are left
   as they are (D28's text hash); a `rebuild` job rewrites them anyway.
   Stage 2 adds "embed each new chunk" here, between INSERT and done (D8).

Failures (D35):
- Temporary, so retried (1 minute, then 5 minutes, then dead): timeouts, connection
  errors, HTTP 429 and any 5xx. Also any unexpected error (a bug, a lost database
  connection): it gets the same 3 tries, then shows its reason on the item.
- Permanent, so dead at once: any other 4xx; a 2xx page of a type we can't read (a PDF
  link, an image), because asking again returns the same type; a page larger than
  fetcher.MAX_BYTES, because asking again returns the same size; a page with no readable
  text; too many redirects or a non-http URL.
- Either way, a fetch that got an HTTP answer is landed and recorded first (D28), and an
  item's earlier chunks stay until a later run succeeds.
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
from mind_dump.chunking import chunk_text
from mind_dump.config import load_settings
from mind_dump.extract import extract_text, text_sha256
from mind_dump.fetcher import PageTooLarge, fetch, make_client
from mind_dump.jobs import Job
from mind_dump.storage import Storage, create_storage

log = logging.getLogger("mind_dump.worker")

Conn = Connection[DictRow]
IDLE_SLEEP = 1.0  # seconds to wait when the queue is empty: quick to react, cheap for Postgres


class TemporaryError(Exception):
    """Asking again may help (D35): the job is retried."""


class PermanentError(Exception):
    """Asking again gives the same answer (D35): the job is dead at once."""


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
    a fake website (http), a fake clock, a short lease, and a hook that runs just before
    COMMIT (the crash tests use it to kill the worker at the worst moment)."""

    conn: Conn  # autocommit: each step's `with conn.transaction()` is a real COMMIT
    storage: Storage
    http: httpx2.Client
    clock: Callable[[], datetime] = _now
    lease: timedelta = jobs.DEFAULT_LEASE
    extract: pages.Extractor = extract_text
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
            "SELECT type, url FROM items WHERE id = %s", (job.item_id,)
        ).fetchone()
        if item is None:
            raise jobs.LeaseLost(f"item {job.item_id} was deleted")
        if item["type"] == "file":
            raise PermanentError("Files are not processed yet (PDF text arrives in stage 2)")
        if item["type"] == "link":
            if job.kind == "rebuild":
                pages.rebuild_versions(self.conn, self.storage, job.item_id, self.extract)
            else:
                self._fetch_link(job.item_id, item["url"])
        return self._replace_chunks(job, force=job.kind == "rebuild")

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

    def _source_text(self, item_id: int) -> tuple[str, int | None]:
        """The text to chunk, read under the item's row lock: (text, item_version_id)."""
        item = self.conn.execute(
            "SELECT type, body FROM items WHERE id = %s FOR UPDATE", (item_id,)
        ).fetchone()
        if item is None:
            raise jobs.LeaseLost(f"item {item_id} was deleted")
        if item["type"] == "note":
            return item["body"], None
        version = self.conn.execute(
            "SELECT id, text FROM item_versions WHERE item_id = %s AND valid_to IS NULL",
            (item_id,),
        ).fetchone()
        if version is None:
            raise PermanentError("The page has no readable text")
        return version["text"], version["id"]

    def _replace_chunks(self, job: Job, force: bool) -> str:
        """D30: replace the chunk set and mark the job done in ONE transaction."""
        with self.conn.transaction():
            jobs.lock_owned(self.conn, job)
            text, version_id = self._source_text(job.item_id)
            sha = text_sha256(text)
            current = self.conn.execute(
                "SELECT source_sha256 FROM chunks WHERE item_id = %s LIMIT 1", (job.item_id,)
            ).fetchone()
            if not force and current is not None and current["source_sha256"] == sha:
                # Same text: keep the rows. Only re-point them if the version row was
                # replaced (a rebuild of versions gives the same text a new row).
                self.conn.execute(
                    """
                    UPDATE chunks SET item_version_id = %s
                    WHERE item_id = %s AND item_version_id IS DISTINCT FROM %s
                    """,
                    (version_id, job.item_id, version_id),
                )
                outcome = "text unchanged, chunks kept"
            else:
                pieces = chunk_text(text)
                self.conn.execute("DELETE FROM chunks WHERE item_id = %s", (job.item_id,))
                with self.conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text,
                                            source_sha256, item_version_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (job.item_id, c.index, c.start_char, c.end_char, c.text)
                            + (sha, version_id)
                            for c in pieces
                        ],
                    )
                # Stage 2 (D8): embed each new chunk here, inside this transaction.
                outcome = f"{len(pieces)} chunk(s) written"
            if self.before_commit is not None:
                self.before_commit(job)
            jobs.mark_done(self.conn, job, self.clock())
        return outcome


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
        log.info("worker started")
        Worker(conn=conn, storage=create_storage(settings), http=http).run(stop)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
