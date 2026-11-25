"""The worker as a real OS process (P3 proof, M3 done-when).

1. A worker process is killed hard (os._exit: no cleanup, no except, no finally) after it
   has written the new chunks but before COMMIT. Postgres rolls the open transaction
   back when the connection drops, so the job is not done and the old chunks are intact.
   Once the lease runs out, a second worker finishes the job: one set of chunks.
2. `python -m mind_dump.worker` processes a saved note and stops cleanly on SIGTERM.
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx2
import psycopg
from psycopg.rows import DictRow

from mind_dump import jobs
from mind_dump.embeddings import FakeEmbedder
from mind_dump.fetcher import make_client
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]

# Runs in a separate Python process. The hook fires inside the chunk transaction, after
# DELETE and INSERT and "done", just before COMMIT, and kills the process on the spot.
CRASHING_WORKER = """
import os, sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mind_dump.embeddings import FakeEmbedder
from mind_dump.fetcher import make_client
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

url, storage_dir = sys.argv[1], sys.argv[2]
with (
    psycopg.connect(url, autocommit=True, row_factory=dict_row,
                    application_name="crashing-worker") as conn,
    make_client() as http,
):
    def die(job):
        n = conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE item_id = %s", (job.item_id,)
        ).fetchone()["n"]
        print(f"uncommitted chunks: {n}", flush=True)
        os._exit(42)

    Worker(conn=conn, storage=LocalStorage(Path(storage_dir)), http=http,
           embedder=FakeEmbedder(), before_commit=die).run_once()
print("the hook never ran", flush=True)
sys.exit(1)
"""


def paragraphs(n: int, tag: str) -> str:
    return "\n\n".join((f"{tag} paragraph {i} " + "z" * 900)[:900] for i in range(n))


def chunk_rows(conn: Conn, item_id: int) -> list[DictRow]:
    return conn.execute(
        "SELECT id, chunk_index, start_char, end_char, text,"
        " embedding::text AS embedding, embedding_model FROM chunks"
        " WHERE item_id = %s ORDER BY chunk_index",
        (item_id,),
    ).fetchall()


def job_of(conn: Conn, item_id: int) -> DictRow:
    return conn.execute(
        "SELECT * FROM jobs WHERE item_id = %s ORDER BY id DESC LIMIT 1", (item_id,)
    ).fetchone()


def make_due(conn: Conn) -> None:
    """The child process uses the real clock, which may lag the database's a little."""
    conn.execute("UPDATE jobs SET run_after = now() - interval '1 hour' WHERE status = 'queued'")


def crashed_backends(conn: Conn) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name = 'crashing-worker'"
    ).fetchone()["n"]


def wait_until(check: Callable[[], bool], seconds: float = 15) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def test_a_worker_killed_before_commit_loses_nothing_and_the_job_completes_once(
    conn: Conn, database_url: str, storage: LocalStorage
) -> None:
    # An item with a good set of chunks from an earlier run.
    item_id = conn.execute(
        "INSERT INTO items (type, body) VALUES ('note', %s) RETURNING id", (paragraphs(4, "old"),)
    ).fetchone()["id"]
    jobs.enqueue(conn, item_id)
    with make_client(httpx2.MockTransport(lambda r: httpx2.Response(500))) as http:
        Worker(conn=conn, storage=storage, http=http, embedder=FakeEmbedder()).run_once()
    before = chunk_rows(conn, item_id)
    assert len(before) == 4
    assert all(r["embedding"] is not None for r in before)  # with vectors (D8)

    # The note changes, and a new run is queued.
    conn.execute("UPDATE items SET body = %s WHERE id = %s", (paragraphs(2, "new"), item_id))
    jobs.enqueue(conn, item_id)
    make_due(conn)

    child = subprocess.run(  # noqa: S603 (our own interpreter and code)
        [sys.executable, "-c", CRASHING_WORKER, database_url, str(storage.root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 42, child.stdout + child.stderr  # died inside the hook
    assert "uncommitted chunks: 2" in child.stdout  # the new chunks had been written

    # Postgres notices the dropped connection and rolls its transaction back.
    assert wait_until(lambda: crashed_backends(conn) == 0)
    job = job_of(conn, item_id)
    assert job["status"] == "running"  # not done
    assert job["attempts"] == 1
    assert chunk_rows(conn, item_id) == before  # the old chunks, exactly: ids, text, vectors

    # A second worker: nothing to claim while the dead worker's lease lasts...
    lease_end = job["locked_until"]
    with make_client(httpx2.MockTransport(lambda r: httpx2.Response(500))) as http:
        second = Worker(
            conn=conn,
            storage=storage,
            http=http,
            clock=lambda: lease_end - timedelta(seconds=1),
            embedder=FakeEmbedder(),
        )
        assert second.run_once() is None
        # ...and once it has run out, it takes the job again and finishes it.
        second.clock = lambda: lease_end
        taken = second.run_once()

    assert taken is not None and taken.id == job["id"] and taken.attempts == 2
    assert job_of(conn, item_id)["status"] == "done"
    after = chunk_rows(conn, item_id)
    assert [r["chunk_index"] for r in after] == [0, 1]  # exactly one set...
    assert all(r["text"].startswith("new") for r in after)  # ...of the new text
    assert all(r["embedding_model"] == "fake-embedder" for r in after)  # each with a vector
    total = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    assert total == 2


def test_the_worker_command_processes_a_note_and_stops_on_sigterm(
    conn: Conn, database_url: str, storage_dir: Path
) -> None:
    item_id = conn.execute(
        "INSERT INTO items (type, body) VALUES ('note', 'Buy oat milk') RETURNING id"
    ).fetchone()["id"]
    jobs.enqueue(conn, item_id)
    make_due(conn)

    env = {**os.environ, "DATABASE_URL": database_url, "STORAGE_DIR": str(storage_dir)}
    worker = subprocess.Popen(  # noqa: S603 (our own interpreter and module)
        [sys.executable, "-m", "mind_dump.worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        done = wait_until(lambda: job_of(conn, item_id)["status"] == "done")
        worker.send_signal(signal.SIGTERM)
        output, _ = worker.communicate(timeout=15)
    finally:
        if worker.poll() is None:
            worker.kill()
    assert done, output
    assert worker.returncode == 0, output  # a clean stop
    assert "worker stopped" in output
    assert [r["text"] for r in chunk_rows(conn, item_id)] == ["Buy oat milk"]
