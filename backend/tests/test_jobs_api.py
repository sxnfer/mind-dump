"""The API side of P3: saving queues a job in the same transaction, every item shows its
processing state, POST /items/{id}/process queues a re-run, GET /items/{id}/chunks."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from mind_dump import items as items_module
from mind_dump import jobs
from mind_dump.fetcher import make_client
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Site:
    def __init__(self) -> None:
        self.status = 200
        self.html = "<main><p>Credits issued: 120,000</p></main>"

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(self.status, html=self.html)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC) + timedelta(minutes=1)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def site() -> Site:
    return Site()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def worker(conn: Conn, storage: LocalStorage, site: Site, clock: Clock) -> Iterator[Worker]:
    with make_client(httpx2.MockTransport(site.handle)) as http:
        yield Worker(conn=conn, storage=storage, http=http, clock=clock)


def save(client: TestClient, **payload: object) -> dict:
    response = client.post("/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def jobs_of(conn: Conn, item_id: int) -> list[DictRow]:
    return conn.execute(
        "SELECT kind, status, attempts FROM jobs WHERE item_id = %s ORDER BY id", (item_id,)
    ).fetchall()


# ---------- saving queues exactly one job, and fetches nothing ----------


@pytest.mark.parametrize(
    "payload",
    [{"type": "note", "body": "Buy oat milk"}, {"type": "link", "url": "https://example.com/a"}],
)
def test_saving_returns_at_once_queued_with_one_job(
    client: TestClient, conn: Conn, payload: dict
) -> None:
    item = save(client, **payload)

    assert item["status"] == "queued"
    assert item["error"] is None
    assert jobs_of(conn, item["id"]) == [{"kind": "process", "status": "queued", "attempts": 0}]
    # Saving did no work: nothing was fetched, nothing chunked.
    assert conn.execute("SELECT count(*) AS n FROM fetches").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 0


def test_an_item_is_never_saved_without_its_job(
    client: TestClient, conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_enqueue(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the queue is broken")

    monkeypatch.setattr(items_module.jobs, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="the queue is broken"):
        client.post("/items", json={"type": "note", "body": "Buy oat milk"})

    # The item's INSERT rolled back with the failed job: one transaction.
    assert conn.execute("SELECT count(*) AS n FROM items").fetchone()["n"] == 0


def test_a_duplicate_link_queues_no_second_job(client: TestClient, conn: Conn) -> None:
    item = save(client, type="link", url="https://example.com/a")
    again = client.post("/items", json={"type": "link", "url": "https://example.com/a"})
    assert again.status_code == 409
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
    assert len(jobs_of(conn, item["id"])) == 1


def test_an_uploaded_file_is_queued_with_one_job(client: TestClient, conn: Conn) -> None:
    response = client.post("/items/upload", content=PNG)
    assert response.status_code == 201
    item = response.json()

    assert item["status"] == "queued"  # M3: files are processed too (was "saved")
    assert client.get(f"/items/{item['id']}").json()["status"] == "queued"
    assert jobs_of(conn, item["id"]) == [{"kind": "process", "status": "queued", "attempts": 0}]
    # Saving did no work: nothing extracted, nothing chunked.
    assert conn.execute("SELECT count(*) AS n FROM file_texts").fetchone()["n"] == 0


def test_a_file_is_never_saved_without_its_job(
    client: TestClient, conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_enqueue(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the queue is broken")

    monkeypatch.setattr(items_module.jobs, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="the queue is broken"):
        client.post("/items/upload", content=PNG)

    assert conn.execute("SELECT count(*) AS n FROM items").fetchone()["n"] == 0


# ---------- the processing state follows the latest job ----------


def test_after_the_worker_runs_the_item_is_ready_with_chunks(
    client: TestClient, worker: Worker
) -> None:
    note = save(client, type="note", body="First paragraph.\n\nSecond paragraph.")
    link = save(client, type="link", url="https://registry.example.org/projects/1234")

    worker.run_once()
    worker.run_once()

    assert client.get(f"/items/{note['id']}").json()["status"] == "ready"
    listed = {i["id"]: i["status"] for i in client.get("/items").json()["items"]}
    assert listed == {note["id"]: "ready", link["id"]: "ready"}

    response = client.get(f"/items/{note['id']}/chunks")
    assert response.status_code == 200
    # chunk_id is the row's id: the one search hits and citations carry.
    chunk_id = worker.conn.execute(
        "SELECT id FROM chunks WHERE item_id = %s", (note["id"],)
    ).fetchone()["id"]
    assert response.json() == [
        {
            "chunk_id": chunk_id,
            "index": 0,
            "start_char": 0,
            "end_char": 35,
            "text": "First paragraph.\n\nSecond paragraph.",
            "page": None,  # notes have no pages
            "embedded": False,  # this worker has no embedder (no OPENAI_API_KEY)
            "embedding_model": None,
        }
    ]
    link_chunks = client.get(f"/items/{link['id']}/chunks").json()
    assert [c["text"] for c in link_chunks] == ["Credits issued: 120,000"]


def test_a_dead_job_shows_as_error_with_the_reason(
    client: TestClient, worker: Worker, site: Site
) -> None:
    site.status = 404
    link = save(client, type="link", url="https://example.com/gone")

    worker.run_once()

    item = client.get(f"/items/{link['id']}").json()
    assert item["status"] == "error"
    assert "HTTP 404 Not Found from https://example.com/gone" in item["error"]
    listed = client.get("/items").json()["items"][0]
    assert (listed["status"], listed["error"]) == ("error", item["error"])


def test_a_failed_try_shows_as_retrying_with_the_reason(
    client: TestClient, worker: Worker, site: Site
) -> None:
    site.status = 503
    link = save(client, type="link", url="https://example.com/busy")

    worker.run_once()

    item = client.get(f"/items/{link['id']}").json()
    assert item["status"] == "retrying"
    assert "HTTP 503" in item["error"]


def test_a_claimed_job_shows_as_processing(client: TestClient, conn: Conn, clock: Clock) -> None:
    note = save(client, type="note", body="x")
    jobs.claim(conn, clock.now)

    assert client.get(f"/items/{note['id']}").json()["status"] == "processing"


# ---------- POST /items/{id}/process ----------


def test_process_queues_a_fresh_run(client: TestClient, conn: Conn, worker: Worker) -> None:
    note = save(client, type="note", body="Buy oat milk")
    worker.run_once()

    response = client.post(f"/items/{note['id']}/process")

    assert response.status_code == 202
    job = response.json()
    assert (job["item_id"], job["kind"], job["status"], job["attempts"]) == (
        note["id"],
        "process",
        "queued",
        0,
    )
    assert client.get(f"/items/{note['id']}").json()["status"] == "queued"
    assert [j["status"] for j in jobs_of(conn, note["id"])] == ["done", "queued"]


def test_process_from_raw_queues_a_rebuild(client: TestClient, worker: Worker) -> None:
    link = save(client, type="link", url="https://example.com/a")
    worker.run_once()

    response = client.post(f"/items/{link['id']}/process", params={"from_raw": "true"})

    assert response.status_code == 202
    assert response.json()["kind"] == "rebuild"


def test_process_is_409_while_a_job_is_unfinished(client: TestClient) -> None:
    note = save(client, type="note", body="Buy oat milk")  # its first job is still queued

    response = client.post(f"/items/{note['id']}/process")

    assert response.status_code == 409
    assert "already has a job" in response.json()["detail"]


def test_process_an_unknown_item_is_404(client: TestClient) -> None:
    assert client.post("/items/999/process").status_code == 404


def test_process_a_file_queues_a_run(client: TestClient, conn: Conn) -> None:
    file_id = client.post("/items/upload", content=PNG).json()["id"]
    conn.execute("UPDATE jobs SET status = 'dead', error = 'x', finished_at = now()")

    response = client.post(f"/items/{file_id}/process", params={"from_raw": "true"})

    assert response.status_code == 202  # M3: files are processed (was 422)
    assert response.json()["kind"] == "rebuild"


# ---------- chunks and delete ----------


def test_chunks_of_an_unprocessed_item_are_empty(client: TestClient) -> None:
    note = save(client, type="note", body="Buy oat milk")
    assert client.get(f"/items/{note['id']}/chunks").json() == []


def test_chunks_of_an_unknown_item_is_404(client: TestClient) -> None:
    assert client.get("/items/999/chunks").status_code == 404


def test_deleting_an_item_removes_its_jobs_and_chunks(
    client: TestClient, conn: Conn, worker: Worker
) -> None:
    note = save(client, type="note", body="Buy oat milk")
    worker.run_once()
    client.post(f"/items/{note['id']}/process")
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 2
    assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 1

    assert client.delete(f"/items/{note['id']}").status_code == 204

    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 0
    assert client.get(f"/items/{note['id']}/chunks").status_code == 404


def test_the_api_version_is_bumped(client: TestClient) -> None:
    assert client.get("/openapi.json").json()["info"]["version"] == "0.8.0"
