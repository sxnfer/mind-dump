"""The worker: chunks replaced in one transaction with "done" (D30), retries (D35), and
raw landing and the text hash through the queue (D28).

The website is httpx2's mock transport, the clock is fake and the embedder is a fake
(D8): no network, no waiting. Since M3 every chunk here also gets a vector, so these
stage 1 guarantees are proved with embeddings in place.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx2
import psycopg
import pytest
from psycopg.rows import DictRow

from mind_dump import jobs
from mind_dump.embeddings import FakeEmbedder
from mind_dump.fetcher import MAX_BYTES, make_client
from mind_dump.jobs import Job
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]
URL = "https://registry.example.org/projects/1234"
LEASE = timedelta(minutes=5)


class FakeClock:
    def __init__(self) -> None:
        # A little after the database's now(), so jobs queued with run_after = now() are due.
        self.now = datetime.now(UTC) + timedelta(minutes=1)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


def article(credits: str = "120,000", banner: str = "") -> str:
    """A page long enough for several chunks. The banner is furniture, outside <main>."""
    paragraphs = "".join(
        f"<p>Section {i}: the project restores mangroves along the coast. " + "More detail. " * 60
        for i in range(4)
    )
    return f"""<html><body><aside>{banner}</aside><main>
        <h1>Project 1234</h1><p>Credits issued: {credits}</p>{paragraphs}
    </main><footer>footer</footer></body></html>"""


@dataclass
class FakeSite:
    """What the site answers next: a list of responses or exceptions, the last one repeats."""

    answers: list[httpx2.Response | Exception] = field(default_factory=list)
    calls: int = 0
    offline: bool = False

    def serve(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.offline:
            raise AssertionError("the network was used, but this run must not touch it")
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def page(status: int = 200, html: str | None = None, **headers: str) -> httpx2.Response:
    return httpx2.Response(status, html=article() if html is None else html, headers=headers)


@pytest.fixture
def site() -> FakeSite:
    s = FakeSite()
    s.serve(page())
    return s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def worker(conn: Conn, storage: LocalStorage, site: FakeSite, clock: FakeClock) -> Iterator[Worker]:
    with make_client(httpx2.MockTransport(site.handle)) as http:
        yield Worker(
            conn=conn,
            storage=storage,
            http=http,
            clock=clock,
            lease=LEASE,
            embedder=FakeEmbedder(),
        )


def save_link(conn: Conn) -> int:
    """What POST /items does: the item and its job, in one transaction."""
    with conn.transaction():
        item_id = conn.execute(
            "INSERT INTO items (type, url) VALUES ('link', %s) RETURNING id", (URL,)
        ).fetchone()["id"]
        jobs.enqueue(conn, item_id)
    return item_id


def save_note(conn: Conn, body: str) -> int:
    with conn.transaction():
        item_id = conn.execute(
            "INSERT INTO items (type, body) VALUES ('note', %s) RETURNING id", (body,)
        ).fetchone()["id"]
        jobs.enqueue(conn, item_id)
    return item_id


def chunk_rows(conn: Conn, item_id: int) -> list[DictRow]:
    """Every column that matters, vectors included (as text), so `==` compares it all."""
    return conn.execute(
        """
        SELECT id, chunk_index, start_char, end_char, text, source_sha256, item_version_id,
               page, embedding::text AS embedding, embedding_model
        FROM chunks WHERE item_id = %s ORDER BY chunk_index
        """,
        (item_id,),
    ).fetchall()


def chunk_set(conn: Conn, item_id: int) -> list[tuple]:
    """What a chunk set says, without row ids: (index, start, end, text)."""
    return [
        (r["chunk_index"], r["start_char"], r["end_char"], r["text"])
        for r in chunk_rows(conn, item_id)
    ]


def latest_job(conn: Conn, item_id: int) -> DictRow:
    return conn.execute(
        "SELECT * FROM jobs WHERE item_id = %s ORDER BY id DESC LIMIT 1", (item_id,)
    ).fetchone()


def fetch_statuses(conn: Conn, item_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT http_status FROM fetches WHERE item_id = %s ORDER BY id", (item_id,)
    ).fetchall()
    return [r["http_status"] for r in rows]


def landed_files(storage: LocalStorage, item_id: int) -> list[str]:
    folder = storage.root / "raw" / str(item_id)
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def paragraphs(n: int, tag: str) -> str:
    """n paragraphs of 900 characters: each one fills its own chunk (900 + 2 + 900 > 1000)."""
    return "\n\n".join((f"{tag} paragraph {i} " + "z" * 900)[:900] for i in range(n))


class Crash(BaseException):
    """Stands in for the process dying: no `except Exception` in the worker catches it."""


def crash_before_commit(seen: list[int], conn: Conn) -> Callable[[Job], None]:
    def hook(job: Job) -> None:
        # Inside the dying transaction: the new chunks really were written.
        seen.append(conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"])
        raise Crash

    return hook


# ---------- notes and links end to end ----------


def test_a_note_is_chunked_and_its_job_done(conn: Conn, worker: Worker) -> None:
    item_id = save_note(conn, paragraphs(3, "note"))

    job = worker.run_once()

    assert job is not None and job.item_id == item_id
    assert latest_job(conn, item_id)["status"] == "done"
    rows = chunk_rows(conn, item_id)
    assert [r["chunk_index"] for r in rows] == [0, 1, 2]
    body = conn.execute("SELECT body FROM items WHERE id = %s", (item_id,)).fetchone()["body"]
    assert all(body[r["start_char"] : r["end_char"]] == r["text"] for r in rows)
    assert rows[0]["item_version_id"] is None  # notes have no versions
    assert worker.run_once() is None  # nothing else to do


def test_a_link_is_fetched_versioned_and_chunked(
    conn: Conn, worker: Worker, site: FakeSite
) -> None:
    item_id = save_link(conn)

    worker.run_once()

    assert site.calls == 1
    assert fetch_statuses(conn, item_id) == [200]
    version = conn.execute(
        "SELECT id, text FROM item_versions WHERE item_id = %s AND valid_to IS NULL", (item_id,)
    ).fetchone()
    rows = chunk_rows(conn, item_id)
    assert len(rows) > 1
    assert all(r["item_version_id"] == version["id"] for r in rows)
    assert all(version["text"][r["start_char"] : r["end_char"]] == r["text"] for r in rows)
    assert latest_job(conn, item_id)["status"] == "done"


# ---------- D30: crash before COMMIT, retries, fewer chunks ----------


def test_a_crash_before_commit_leaves_the_old_chunks_and_the_job_not_done(
    conn: Conn, worker: Worker
) -> None:
    item_id = save_note(conn, paragraphs(4, "old"))
    worker.run_once()
    before = chunk_rows(conn, item_id)
    assert len(before) == 4

    conn.execute("UPDATE items SET body = %s WHERE id = %s", (paragraphs(2, "new"), item_id))
    jobs.enqueue(conn, item_id)
    seen: list[int] = []
    worker.before_commit = crash_before_commit(seen, conn)
    with pytest.raises(Crash):
        worker.run_once()

    assert seen == [2]  # the dying transaction had replaced 4 chunks with 2...
    assert chunk_rows(conn, item_id) == before  # ...and none of it survived: same rows, same ids
    assert all(r["embedding"] is not None for r in before)  # vectors included
    job = latest_job(conn, item_id)
    assert job["status"] == "running"  # not done: its lease will run out and it runs again
    assert job["finished_at"] is None


def test_a_job_run_again_after_a_crash_gives_one_correct_set(
    conn: Conn, worker: Worker, clock: FakeClock
) -> None:
    body = paragraphs(5, "note")
    item_id = save_note(conn, body)
    seen: list[int] = []
    worker.before_commit = crash_before_commit(seen, conn)
    with pytest.raises(Crash):
        worker.run_once()
    assert chunk_rows(conn, item_id) == []  # nothing committed

    worker.before_commit = None
    clock.advance(LEASE)  # the dead worker's lease runs out
    job = worker.run_once()

    assert job is not None and job.attempts == 2  # the same job, its second try
    assert latest_job(conn, item_id)["status"] == "done"
    rows = chunk_rows(conn, item_id)
    assert [r["chunk_index"] for r in rows] == [0, 1, 2, 3, 4]  # one set, no duplicates


def test_running_the_same_job_twice_gives_the_same_chunks(conn: Conn, worker: Worker) -> None:
    item_id = save_note(conn, paragraphs(5, "note") + "\n\nA short ending.")
    worker.run_once()
    first = chunk_set(conn, item_id)
    first_ids = [r["id"] for r in chunk_rows(conn, item_id)]

    jobs.enqueue(conn, item_id, "rebuild")  # rebuild: rewrite even though the text is the same
    worker.run_once()

    assert chunk_set(conn, item_id) == first  # same count, indexes, positions and text
    assert [r["id"] for r in chunk_rows(conn, item_id)] != first_ids  # really rewritten
    total = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    assert total == len(first)  # no duplicates


def test_fewer_chunks_leave_no_orphans(conn: Conn, worker: Worker) -> None:
    item_id = save_note(conn, paragraphs(5, "long"))
    worker.run_once()
    assert [r["chunk_index"] for r in chunk_rows(conn, item_id)] == [0, 1, 2, 3, 4]

    conn.execute("UPDATE items SET body = %s WHERE id = %s", (paragraphs(3, "short"), item_id))
    jobs.enqueue(conn, item_id)
    worker.run_once()

    rows = chunk_rows(conn, item_id)
    assert [r["chunk_index"] for r in rows] == [0, 1, 2]
    assert all(r["text"].startswith("short") for r in rows)  # no stale "long" text left
    assert all(r["embedding"] is not None for r in rows)  # each new chunk has its vector
    stale = conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE item_id = %s AND chunk_index >= 3", (item_id,)
    ).fetchone()["n"]
    assert stale == 0


# ---------- D35: which failures are retried ----------


def test_a_404_is_not_retried(conn: Conn, worker: Worker, site: FakeSite) -> None:
    site.serve(page(404, "<h1>Not found</h1>"))
    item_id = save_link(conn)

    worker.run_once()

    job = latest_job(conn, item_id)
    assert job["status"] == "dead"
    assert job["attempts"] == 1
    assert "HTTP 404 Not Found" in job["error"]
    assert fetch_statuses(conn, item_id) == [404]  # the fetch is still recorded (D28)
    assert len(landed_files(worker.storage, item_id)) == 1  # and its body landed
    assert worker.run_once() is None  # never asked again
    assert site.calls == 1


TEMPORARY = {
    "503": lambda: page(503, "<h1>Busy</h1>"),
    "429": lambda: page(429, "<h1>Slow down</h1>"),
    "500": lambda: page(500, "<h1>Oops</h1>"),
    "timeout": lambda: httpx2.ReadTimeout("read timed out"),
    "connection error": lambda: httpx2.ConnectError("connection refused"),
}


@pytest.mark.parametrize("failure", TEMPORARY)
def test_temporary_failures_wait_1_then_5_minutes_then_die(
    failure: str, conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock
) -> None:
    site.serve(TEMPORARY[failure]())
    item_id = save_link(conn)

    worker.run_once()  # try 1
    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("failed", 1)
    assert job["run_after"] == clock.now + timedelta(minutes=1)
    assert worker.run_once() is None  # not due yet

    clock.advance(timedelta(minutes=1))
    worker.run_once()  # try 2
    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("failed", 2)
    assert job["run_after"] == clock.now + timedelta(minutes=5)

    clock.advance(timedelta(minutes=4, seconds=59))
    assert worker.run_once() is None  # still waiting
    clock.advance(timedelta(seconds=1))
    worker.run_once()  # try 3
    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 3)
    assert "gave up after 3 tries" in job["error"]
    assert site.calls == 3
    # Every HTTP answer was recorded; a timeout or refused connection has none to record.
    expected = [int(failure)] * 3 if failure.isdigit() else []
    assert fetch_statuses(conn, item_id) == expected


def test_a_503_then_a_200_ends_done_with_chunks(
    conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock
) -> None:
    site.serve(page(503, "<h1>Busy</h1>"), page())
    item_id = save_link(conn)

    worker.run_once()
    assert latest_job(conn, item_id)["status"] == "failed"
    clock.advance(timedelta(minutes=1))
    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"], job["error"]) == ("done", 2, None)
    assert fetch_statuses(conn, item_id) == [503, 200]
    assert len(chunk_rows(conn, item_id)) > 1


def test_a_page_we_cannot_read_is_permanent(conn: Conn, worker: Worker, site: FakeSite) -> None:
    site.serve(
        httpx2.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    item_id = save_link(conn)

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)
    assert "application/pdf" in job["error"]
    assert fetch_statuses(conn, item_id) == [200]  # landed and recorded all the same


def test_a_page_too_large_is_permanent(conn: Conn, worker: Worker, site: FakeSite) -> None:
    site.serve(httpx2.Response(200, content=b"x" * (MAX_BYTES + 1)))
    item_id = save_link(conn)

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)
    assert "larger than" in job["error"]


def test_an_unexpected_error_is_retried_then_shown(
    conn: Conn, worker: Worker, clock: FakeClock
) -> None:
    item_id = save_note(conn, "a note")

    def broken(job: Job) -> None:
        raise RuntimeError("a bug")

    worker.before_commit = broken
    for wait in (timedelta(0), timedelta(minutes=1), timedelta(minutes=5)):
        clock.advance(wait)
        worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 3)
    assert "RuntimeError: a bug" in job["error"]
    assert chunk_rows(conn, item_id) == []  # every try rolled back


# ---------- D28 through the worker ----------


def test_reprocess_from_raw_rebuilds_the_same_chunks_with_no_network(
    conn: Conn, worker: Worker, site: FakeSite
) -> None:
    item_id = save_link(conn)
    worker.run_once()
    before = chunk_set(conn, item_id)
    assert len(before) > 1

    site.offline = True  # the fake server is gone: any request fails the test
    jobs.enqueue(conn, item_id, "rebuild")
    worker.run_once()

    assert latest_job(conn, item_id)["status"] == "done"
    assert chunk_set(conn, item_id) == before
    assert site.calls == 1  # only the first fetch
    version_ids = {r["item_version_id"] for r in chunk_rows(conn, item_id)}
    current = conn.execute(
        "SELECT id FROM item_versions WHERE item_id = %s AND valid_to IS NULL", (item_id,)
    ).fetchone()["id"]
    assert version_ids == {current}  # pointing at the rebuilt version row


def test_a_refetch_that_gets_404_keeps_the_good_file_and_the_chunks(
    conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock
) -> None:
    item_id = save_link(conn)
    worker.run_once()
    before = chunk_rows(conn, item_id)
    (good_file,) = landed_files(worker.storage, item_id)
    good_bytes = (worker.storage.root / "raw" / str(item_id) / good_file).read_bytes()

    site.serve(page(404, "<h1>Gone</h1>"))
    clock.advance(timedelta(days=1))
    jobs.enqueue(conn, item_id)
    worker.run_once()

    assert latest_job(conn, item_id)["status"] == "dead"
    assert fetch_statuses(conn, item_id) == [200, 404]
    files = landed_files(worker.storage, item_id)
    assert len(files) == 2 and good_file in files  # the 404 landed beside it
    assert (worker.storage.root / "raw" / str(item_id) / good_file).read_bytes() == good_bytes
    assert chunk_rows(conn, item_id) == before  # same rows, same ids


def test_a_refetch_with_identical_bytes_stores_no_new_file(
    conn: Conn, worker: Worker, clock: FakeClock
) -> None:
    item_id = save_link(conn)
    worker.run_once()
    before = chunk_rows(conn, item_id)

    clock.advance(timedelta(days=1))
    jobs.enqueue(conn, item_id)
    worker.run_once()

    assert latest_job(conn, item_id)["status"] == "done"
    assert fetch_statuses(conn, item_id) == [200, 200]  # the fetch is recorded
    assert len(landed_files(worker.storage, item_id)) == 1  # but no second file
    assert chunk_rows(conn, item_id) == before


def test_a_refetch_where_only_furniture_changed_keeps_the_chunk_rows(
    conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock
) -> None:
    item_id = save_link(conn)
    worker.run_once()
    before = chunk_rows(conn, item_id)

    site.serve(page(html=article(banner="New cookie banner! Accept all?")))
    clock.advance(timedelta(days=1))
    jobs.enqueue(conn, item_id)
    calls = worker.embedder.calls
    worker.run_once()

    assert worker.embedder.calls == calls  # M3 done-when: no embedding call either
    assert latest_job(conn, item_id)["status"] == "done"
    assert len(landed_files(worker.storage, item_id)) == 2  # new bytes: a new file
    versions = conn.execute(
        "SELECT count(*) AS n FROM item_versions WHERE item_id = %s", (item_id,)
    ).fetchone()["n"]
    assert versions == 1  # same text: no new version
    assert chunk_rows(conn, item_id) == before  # same ids: the chunks were not rewritten


def test_a_real_change_rewrites_the_chunks_from_the_new_version(
    conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock
) -> None:
    item_id = save_link(conn)
    worker.run_once()

    site.serve(page(html=article(credits="145,000")))
    clock.advance(timedelta(days=1))
    jobs.enqueue(conn, item_id)
    worker.run_once()

    texts = " ".join(r["text"] for r in chunk_rows(conn, item_id))
    assert "145,000" in texts and "120,000" not in texts
    current = conn.execute(
        "SELECT id FROM item_versions WHERE item_id = %s AND valid_to IS NULL", (item_id,)
    ).fetchone()["id"]
    assert {r["item_version_id"] for r in chunk_rows(conn, item_id)} == {current}


def test_a_deleted_item_takes_its_job_and_chunks_with_it(conn: Conn, worker: Worker) -> None:
    item_id = save_note(conn, paragraphs(2, "note"))
    worker.run_once()

    conn.execute("DELETE FROM items WHERE id = %s", (item_id,))

    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 0
