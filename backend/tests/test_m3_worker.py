"""M3 stage 2 through the worker: files become text (D7b), every chunk gets a vector (D8).

No network anywhere: the website is a mock transport, the embedder and the describer are
fakes (or the real OpenAI and Voyage clients on a mock transport), and the clock is fake.
"""

import json
import struct
from collections.abc import Iterator
from datetime import timedelta
from io import BytesIO

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from pdfs import make_pdf
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from pypdf import PdfReader, PdfWriter
from test_worker import (
    LEASE,
    Crash,
    FakeClock,
    FakeSite,
    article,
    chunk_rows,
    crash_before_commit,
    latest_job,
    page,
    paragraphs,
    save_link,
    save_note,
)

from mind_dump import embed_missing, jobs
from mind_dump import search as search_mod
from mind_dump.describe import FakeDescriber
from mind_dump.embeddings import (
    DIMENSIONS,
    FakeEmbedder,
    OpenAIEmbedder,
    VoyageEmbedder,
    fake_vector,
)
from mind_dump.fetcher import make_client
from mind_dump.storage import LocalStorage
from mind_dump.uploads import file_key
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]

LONG_PAGE = "\n".join(
    f"Sentence {i} of page two: the mangrove project restored more coastline this year."
    for i in range(40)
)
PDF_PAGES = ["Page one: a short summary.", LONG_PAGE, "Page three: the conclusion."]
DESCRIPTION = (
    "Text in the image:\nQ3 credits issued: 145,000\n\n"
    "A bar chart of carbon credits issued per quarter, rising from Q1 to Q3."
)


def png(width: int = 640, height: int = 480) -> bytes:
    """A PNG signature and IHDR header: enough for the type check and the size check."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + ihdr + b"\x00" * 16


def encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(["secret plans"]))))
    writer.encrypt(user_password="hunter2", owner_password="owner", algorithm="RC4-128")  # noqa: S106
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def site() -> FakeSite:
    s = FakeSite()
    s.serve(page())
    return s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def describer() -> FakeDescriber:
    return FakeDescriber(DESCRIPTION)


@pytest.fixture
def worker(
    conn: Conn,
    storage: LocalStorage,
    site: FakeSite,
    clock: FakeClock,
    embedder: FakeEmbedder,
    describer: FakeDescriber,
) -> Iterator[Worker]:
    with make_client(httpx2.MockTransport(site.handle)) as http:
        yield Worker(
            conn=conn,
            storage=storage,
            http=http,
            clock=clock,
            lease=LEASE,
            embedder=embedder,
            describer=describer,
        )


def upload(client: TestClient, data: bytes, name: str = "file") -> int:
    response = client.post(
        "/items/upload",
        content=data,
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "queued"
    return response.json()["id"]


def vectors(conn: Conn, item_id: int) -> list[DictRow]:
    return conn.execute(
        """
        SELECT id, text, vector_dims(embedding) AS dims, embedding::text AS embedding,
               embedding_model
        FROM chunks WHERE item_id = %s ORDER BY chunk_index
        """,
        (item_id,),
    ).fetchall()


def as_floats(pgvector_text: str) -> list[float]:
    return json.loads(pgvector_text)


def assert_vector_of(row: DictRow, model: str) -> None:
    """The stored vector is the fake vector of the chunk's own text (float4 precision)."""
    expected = fake_vector(row["text"], model)
    stored = as_floats(row["embedding"])
    assert len(stored) == DIMENSIONS
    assert max(abs(a - b) for a, b in zip(stored, expected, strict=True)) < 1e-6


def count(conn: Conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608


# ---------- B1: PDFs ----------


def test_a_pdf_upload_ends_ready_with_pages_and_exact_positions(
    client: TestClient, worker: Worker
) -> None:
    item_id = upload(client, make_pdf(PDF_PAGES), "report.pdf")

    worker.run_once()

    assert client.get(f"/items/{item_id}").json()["status"] == "ready"
    stored = client.get(f"/items/{item_id}/text").json()
    assert stored["kind"] == "pages"
    assert stored["extractor"].startswith("pypdf ")
    text = stored["text"]
    spans = {p["number"]: (p["start_char"], p["end_char"]) for p in stored["pages"]}
    assert sorted(spans) == [1, 2, 3]

    chunks = client.get(f"/items/{item_id}/chunks").json()
    assert [c["page"] for c in chunks][0] == 1 and chunks[-1]["page"] == 3
    on_page_two = [c for c in chunks if c["page"] == 2]
    assert len(on_page_two) >= 2  # the long page is cut into several chunks
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert text[c["start_char"] : c["end_char"]] == c["text"]  # exact positions
        start, end = spans[c["page"]]
        assert start <= c["start_char"] < c["end_char"] <= end  # never crosses a page
        assert c["embedded"] and c["embedding_model"] == "fake-embedder"
    # Three short-ish pages would fit one chunk; only the page rule keeps 1 and 3 apart.
    assert "summary" in chunks[0]["text"] and "conclusion" not in chunks[0]["text"]


# ---------- B2: PDFs that can't give text ----------


BAD_PDFS = {
    "scanned (no text layer)": (lambda: make_pdf(["", ""]), "no text layer"),
    "encrypted": (encrypted_pdf, "encrypted"),
    "corrupt": (lambda: make_pdf(PDF_PAGES)[:300], "damaged"),
}


@pytest.mark.parametrize("kind", BAD_PDFS)
def test_a_pdf_without_readable_text_is_an_error_at_once(
    kind: str, client: TestClient, conn: Conn, worker: Worker, embedder: FakeEmbedder
) -> None:
    make, reason = BAD_PDFS[kind]
    item_id = upload(client, make(), "bad.pdf")

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)  # permanent: not retried
    assert reason in job["error"]
    item = client.get(f"/items/{item_id}").json()
    assert item["status"] == "error" and reason in item["error"]
    assert worker.run_once() is None  # never claimed again
    assert count(conn, "chunks") == 0 and count(conn, "file_texts") == 0
    assert embedder.calls == 0


def test_an_encrypted_pdf_with_an_empty_password_is_read(
    client: TestClient, worker: Worker
) -> None:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(["open to read, not to edit"]))))
    writer.encrypt(user_password="", owner_password="owner", algorithm="RC4-128")  # noqa: S106
    out = BytesIO()
    writer.write(out)
    item_id = upload(client, out.getvalue(), "locked.pdf")

    worker.run_once()

    assert client.get(f"/items/{item_id}").json()["status"] == "ready"
    assert client.get(f"/items/{item_id}/text").json()["text"] == "open to read, not to edit"


# ---------- B3: images ----------


def test_an_image_upload_ends_ready_with_chunks_from_its_description(
    client: TestClient, worker: Worker, describer: FakeDescriber
) -> None:
    item_id = upload(client, png(), "chart.png")

    worker.run_once()

    assert client.get(f"/items/{item_id}").json()["status"] == "ready"
    stored = client.get(f"/items/{item_id}/text").json()
    assert stored["kind"] == "description" and stored["pages"] is None
    assert stored["text"] == DESCRIPTION
    assert stored["extractor"] == "fake-describer (image description)"
    chunks = client.get(f"/items/{item_id}/chunks").json()
    assert [c["text"] for c in chunks] == [DESCRIPTION]
    assert chunks[0]["page"] is None and chunks[0]["embedded"]
    assert describer.calls == 1


def test_an_image_with_no_describer_is_an_error_naming_the_key(
    client: TestClient, conn: Conn, worker: Worker
) -> None:
    worker.describer = None  # no ANTHROPIC_API_KEY
    item_id = upload(client, png(), "photo.png")

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)
    assert "ANTHROPIC_API_KEY" in job["error"]
    assert "ANTHROPIC_API_KEY" in client.get(f"/items/{item_id}").json()["error"]


def test_an_image_too_large_for_the_api_is_an_error_before_any_call(
    client: TestClient, conn: Conn, worker: Worker
) -> None:
    item_id = upload(client, png(9000, 600), "huge.png")

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)
    assert "9000 x 600 pixels" in job["error"]


# ---------- B4: every new chunk gets a 1,024-dimension vector and the model name ----------


def test_every_kind_of_item_gets_vectors(client: TestClient, conn: Conn, worker: Worker) -> None:
    note = save_note(conn, paragraphs(2, "note"))
    link = save_link(conn)
    pdf = upload(client, make_pdf(PDF_PAGES), "report.pdf")
    image = upload(client, png(), "chart.png")

    for _ in range(4):
        worker.run_once()

    for item_id in (note, link, pdf, image):
        assert latest_job(conn, item_id)["status"] == "done"
        rows = vectors(conn, item_id)
        assert rows, item_id
        for row in rows:
            assert row["dims"] == DIMENSIONS
            assert row["embedding_model"] == "fake-embedder"
            assert_vector_of(row, "fake-embedder")


# ---------- B5: the text hash means no embedding call ----------


def test_a_refetch_where_only_furniture_changed_makes_no_embedding_call(
    conn: Conn, worker: Worker, site: FakeSite, clock: FakeClock, embedder: FakeEmbedder
) -> None:
    item_id = save_link(conn)
    worker.run_once()
    before = chunk_rows(conn, item_id)
    calls = embedder.calls
    assert calls == 1

    site.serve(page(html=article(banner="New cookie banner! Accept all?")))
    clock.advance(timedelta(days=1))
    jobs.enqueue(conn, item_id)
    worker.run_once()

    assert latest_job(conn, item_id)["status"] == "done"
    assert count(conn, "fetches") == 2  # a new file landed (D28)...
    assert embedder.calls == calls  # ...but zero embedding calls
    assert chunk_rows(conn, item_id) == before  # and the same rows, vectors and all


def test_rerunning_an_unchanged_note_pdf_or_image_makes_no_model_call(
    client: TestClient,
    conn: Conn,
    worker: Worker,
    embedder: FakeEmbedder,
    describer: FakeDescriber,
) -> None:
    note = save_note(conn, "Buy oat milk")
    pdf = upload(client, make_pdf(PDF_PAGES), "report.pdf")
    image = upload(client, png(), "chart.png")
    for _ in range(3):
        worker.run_once()
    before = {i: chunk_rows(conn, i) for i in (note, pdf, image)}
    embed_calls, describe_calls = embedder.calls, describer.calls

    for item_id in (note, pdf, image):
        assert client.post(f"/items/{item_id}/process").status_code == 202
        worker.run_once()
        assert latest_job(conn, item_id)["status"] == "done"

    assert embedder.calls == embed_calls
    assert describer.calls == describe_calls  # the stored description is reused
    assert {i: chunk_rows(conn, i) for i in (note, pdf, image)} == before


# ---------- B6: embedding failures, with the real clients on a mock transport ----------


class FakeProvider:
    """Answers like api.openai.com or api.voyageai.com (both send data[].index and
    data[].embedding), or with a failure. Each vector is the fake vector of the text
    under the requested model, so a test can tell which model made a stored vector."""

    def __init__(self) -> None:
        self.failure: httpx2.Response | Exception | None = None
        self.requests: list[dict] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        body = self.requests[-1]
        data = [
            {"object": "embedding", "embedding": fake_vector(t, body["model"]), "index": i}
            for i, t in enumerate(body["input"])
        ][::-1]  # out of order: the client reads by index
        n = len(body["input"])
        return httpx2.Response(
            200,
            json={"object": "list", "data": data, "model": body["model"],
                  "usage": {"prompt_tokens": n, "total_tokens": n}},
        )  # fmt: skip


# provider -> (client, default model, the label in messages, the key it names)
PROVIDERS = {
    "openai": (OpenAIEmbedder, "text-embedding-3-small", "OpenAI", "OPENAI_API_KEY"),
    "voyage": (VoyageEmbedder, "voyage-4", "Voyage AI", "VOYAGE_API_KEY"),
}


@pytest.fixture
def http_fake() -> Iterator[tuple[FakeProvider, httpx2.Client]]:
    fake = FakeProvider()
    with httpx2.Client(transport=httpx2.MockTransport(fake.handle)) as http:
        yield fake, http


@pytest.fixture(params=list(PROVIDERS))
def provider(
    request: pytest.FixtureRequest, worker: Worker, http_fake: tuple[FakeProvider, httpx2.Client]
) -> tuple[str, FakeProvider]:
    """The worker with a real client (OpenAI, then Voyage) on the fake API."""
    name = request.param
    cls, model, _, _ = PROVIDERS[name]
    fake, http = http_fake
    worker.embedder = cls("test-key", model, http=http)
    return name, fake


TEMPORARY = {
    "429": lambda: httpx2.Response(429, json={"error": {"message": "slow down"}}),
    "503": lambda: httpx2.Response(503, text="busy"),
    "timeout": lambda: httpx2.ReadTimeout("read timed out"),
}


def test_openai_embeds_a_note_through_the_worker(
    client: TestClient, conn: Conn, worker: Worker, http_fake: tuple[FakeProvider, httpx2.Client]
) -> None:
    fake, http = http_fake
    worker.embedder = OpenAIEmbedder("test-key", "text-embedding-3-small", http=http)
    item_id = save_note(conn, paragraphs(3, "note"))

    worker.run_once()

    assert latest_job(conn, item_id)["status"] == "done"
    assert client.get(f"/items/{item_id}").json()["status"] == "ready"
    rows = vectors(conn, item_id)
    assert len(rows) == 3
    for row in rows:
        assert row["dims"] == DIMENSIONS == 1024
        assert row["embedding_model"] == "text-embedding-3-small"
        assert_vector_of(row, "text-embedding-3-small")  # each vector is its own chunk's
    assert fake.requests[0]["dimensions"] == 1024 and "input_type" not in fake.requests[0]


@pytest.mark.parametrize("failure", TEMPORARY)
def test_temporary_embedding_failures_retry_and_keep_the_old_chunks(
    failure: str,
    conn: Conn,
    worker: Worker,
    provider: tuple[str, FakeProvider],
    clock: FakeClock,
) -> None:
    name, fake = provider
    _, model, label, _ = PROVIDERS[name]
    item_id = save_note(conn, paragraphs(3, "old"))
    worker.run_once()
    before = chunk_rows(conn, item_id)
    assert len(before) == 3 and all(r["embedding_model"] == model for r in before)

    conn.execute("UPDATE items SET body = %s WHERE id = %s", (paragraphs(2, "new"), item_id))
    jobs.enqueue(conn, item_id)
    fake.failure = TEMPORARY[failure]()
    for wait, status in (
        (timedelta(0), "failed"),
        (timedelta(minutes=1), "failed"),
        (timedelta(minutes=5), "dead"),
    ):
        clock.advance(wait)
        worker.run_once()
        job = latest_job(conn, item_id)
        assert job["status"] == status  # D35's schedule: 1 minute, 5 minutes, then dead
        assert chunk_rows(conn, item_id) == before  # old chunks and vectors untouched
    assert label in job["error"] and job["attempts"] == 3

    fake.failure = None  # the API is back: a re-run replaces the set, vectors and all
    jobs.enqueue(conn, item_id)
    worker.run_once()
    assert [r["text"][:3] for r in chunk_rows(conn, item_id)] == ["new", "new"]


def test_a_401_is_dead_at_once_naming_the_providers_key(
    conn: Conn, worker: Worker, provider: tuple[str, FakeProvider]
) -> None:
    name, fake = provider
    _, _, _, key = PROVIDERS[name]
    other = "VOYAGE_API_KEY" if key == "OPENAI_API_KEY" else "OPENAI_API_KEY"
    fake.failure = httpx2.Response(401, json={"error": {"message": "Incorrect API key."}})
    item_id = save_note(conn, "Buy oat milk")

    worker.run_once()

    job = latest_job(conn, item_id)
    assert (job["status"], job["attempts"]) == ("dead", 1)
    assert key in job["error"] and "401" in job["error"]
    assert other not in job["error"]
    assert chunk_rows(conn, item_id) == []
    assert len(fake.requests) == 1


def test_switching_provider_re_embeds_the_same_rows(
    conn: Conn,
    worker: Worker,
    http_fake: tuple[FakeProvider, httpx2.Client],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D8: a provider change is a model change. Chunks embedded by voyage-4 are never
    compared with an OpenAI query vector; embed_missing finds them; the worker embeds the
    same rows again in place."""
    fake, http = http_fake
    worker.embedder = VoyageEmbedder("test-key", "voyage-4", http=http)
    text = "Mangroves store carbon along the coast."
    item_id = save_note(conn, text)
    worker.run_once()
    ids = [r["id"] for r in vectors(conn, item_id)]
    assert {r["embedding_model"] for r in vectors(conn, item_id)} == {"voyage-4"}

    openai = OpenAIEmbedder("test-key", "text-embedding-3-small", http=http)
    result = search_mod.search(conn, text, "vector", embedder=openai)
    assert result.hits == []  # voyage-4 vectors are not compared with an OpenAI query

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-the-command")  # never sent anywhere
    assert embed_missing.main([]) == 0  # the default provider: OpenAI
    out = capsys.readouterr().out
    assert "1 chunk(s) in 1 item(s) have no vector from text-embedding-3-small" in out
    assert "queued 1 embed job(s)" in out
    worker.embedder = openai
    worker.run_once()

    assert latest_job(conn, item_id)["kind"] == "embed"
    assert latest_job(conn, item_id)["status"] == "done"
    rows = vectors(conn, item_id)
    assert [r["id"] for r in rows] == ids  # the same rows...
    for row in rows:  # ...with OpenAI vectors and the new model's name
        assert row["embedding_model"] == "text-embedding-3-small"
        assert_vector_of(row, "text-embedding-3-small")
    found = search_mod.search(conn, text, "vector", embedder=openai)
    assert [h.chunk_id for h in found.hits] == ids  # now compared, and found
    assert embed_missing.main([]) == 0
    assert "0 chunk(s) in 0 item(s)" in capsys.readouterr().out


# ---------- B7: no network inside the transaction ----------


def test_models_are_called_before_the_transaction_opens(
    client: TestClient, conn: Conn, worker: Worker
) -> None:
    states: list[TransactionStatus] = []
    worker.embedder = FakeEmbedder(on_call=lambda: states.append(conn.info.transaction_status))

    def describe(data: bytes, media_type: str) -> str:
        states.append(conn.info.transaction_status)
        return DESCRIPTION

    worker.describer = FakeDescriber(respond=describe)
    note = save_note(conn, "Buy oat milk")
    image = upload(client, png(), "chart.png")
    worker.run_once()
    worker.run_once()

    assert latest_job(conn, note)["status"] == "done"
    assert latest_job(conn, image)["status"] == "done"
    assert len(states) == 3  # note embed, image describe, image embed
    # IDLE: no transaction open on the worker's connection, so no lock is held.
    assert set(states) == {TransactionStatus.IDLE}


def test_text_changed_between_embedding_and_commit_is_retried_not_saved(
    conn: Conn, worker: Worker, clock: FakeClock
) -> None:
    item_id = save_note(conn, paragraphs(3, "old"))
    worker.run_once()
    before = chunk_rows(conn, item_id)

    edited = []

    def edit_once() -> None:
        if not edited:  # the note changes while its old text is being embedded
            conn.execute("UPDATE items SET body = %s WHERE id = %s", ("Brand new text.", item_id))
            edited.append(True)

    conn.execute("UPDATE items SET body = %s WHERE id = %s", (paragraphs(2, "mid"), item_id))
    jobs.enqueue(conn, item_id)
    worker.embedder = FakeEmbedder(on_call=edit_once)
    worker.run_once()

    job = latest_job(conn, item_id)
    assert job["status"] == "failed"  # temporary: it runs again
    assert "text changed" in job["error"]
    assert chunk_rows(conn, item_id) == before  # no vectors of the wrong text were saved

    clock.advance(timedelta(minutes=1))
    worker.run_once()
    assert latest_job(conn, item_id)["status"] == "done"
    rows = vectors(conn, item_id)
    assert [r["text"] for r in rows] == ["Brand new text."]
    assert_vector_of(rows[0], "fake-embedder")


# ---------- B8 and B9: no embedder, then the catch-up command ----------


def test_without_an_embedder_chunks_are_saved_and_catch_up_keeps_their_ids(
    client: TestClient,
    conn: Conn,
    worker: Worker,
    site: FakeSite,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker.embedder = None  # no OPENAI_API_KEY (the default provider)
    note = save_note(conn, paragraphs(2, "note"))
    link = save_link(conn)
    pdf = upload(client, make_pdf(PDF_PAGES), "report.pdf")
    for _ in range(3):
        worker.run_once()
    items = (note, link, pdf)
    before = {i: [r["id"] for r in vectors(conn, i)] for i in items}
    for item_id in items:
        assert latest_job(conn, item_id)["status"] == "done"
        assert all(r["embedding"] is None for r in vectors(conn, item_id))
        assert not any(c["embedded"] for c in client.get(f"/items/{item_id}/chunks").json())

    # Without the key, the command refuses to queue jobs that could not embed.
    assert embed_missing.main([]) == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY is not set" in out and "VOYAGE_API_KEY" not in out

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-the-command")  # never sent anywhere
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-embedder")
    assert embed_missing.main([]) == 0
    assert "queued 3 embed job(s)" in capsys.readouterr().out
    site.offline = True  # an embed job never fetches
    worker.embedder = FakeEmbedder()
    for _ in range(3):
        worker.run_once()

    for item_id in items:
        assert latest_job(conn, item_id)["kind"] == "embed"
        assert latest_job(conn, item_id)["status"] == "done"
        rows = vectors(conn, item_id)
        assert [r["id"] for r in rows] == before[item_id]  # the same rows...
        for row in rows:  # ...now with vectors
            assert_vector_of(row, "fake-embedder")
    assert embed_missing.main([]) == 0
    assert "0 chunk(s) in 0 item(s)" in capsys.readouterr().out


def test_a_new_embedding_model_re_embeds_existing_chunks(
    conn: Conn, worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = save_note(conn, paragraphs(3, "note"))
    worker.run_once()
    ids = [r["id"] for r in vectors(conn, item_id)]
    assert {r["embedding_model"] for r in vectors(conn, item_id)} == {"fake-embedder"}

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-the-command")
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-embedder-2")
    assert embed_missing.main(["--dry-run"]) == 0
    assert latest_job(conn, item_id)["kind"] == "process"  # dry run: nothing queued
    assert embed_missing.main([]) == 0
    worker.embedder = FakeEmbedder(model="fake-embedder-2")
    worker.run_once()

    rows = vectors(conn, item_id)
    assert [r["id"] for r in rows] == ids
    for row in rows:
        assert row["embedding_model"] == "fake-embedder-2"
        assert_vector_of(row, "fake-embedder-2")


def test_embed_missing_names_the_configured_providers_key(
    conn: Conn,
    worker: Worker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker.embedder = None
    save_note(conn, "Buy oat milk")
    worker.run_once()

    monkeypatch.setenv("VOYAGE_API_KEY", "dummy")  # not the configured provider's key
    assert embed_missing.main([]) == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY is not set" in out and "VOYAGE_API_KEY" not in out

    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")  # now the other provider's key
    assert embed_missing.main([]) == 1
    out = capsys.readouterr().out
    assert "VOYAGE_API_KEY is not set" in out and "OPENAI_API_KEY" not in out
    assert "no vector from voyage-4" in out


@pytest.mark.parametrize(("provider", "key"), [("openai", "OPENAI_API_KEY"),
                                               ("voyage", "VOYAGE_API_KEY")])  # fmt: skip
def test_the_no_embedder_log_line_names_the_providers_key(
    provider: str, key: str, conn: Conn, worker: Worker, caplog: pytest.LogCaptureFixture
) -> None:
    from mind_dump.config import Settings
    from mind_dump.embeddings import no_embedder_reason

    cfg = Settings(database_url="x", migrations_dir=".", embedding_provider=provider)  # type: ignore[arg-type]
    worker.embedder = None
    worker.no_embedder = no_embedder_reason(cfg)
    save_note(conn, "Buy oat milk")
    with caplog.at_level("INFO", logger="mind_dump.worker"):
        worker.run_once()
    line = next(r.getMessage() for r in caplog.records if "without vectors" in r.getMessage())
    assert key in line
    assert ("VOYAGE_API_KEY" if key == "OPENAI_API_KEY" else "OPENAI_API_KEY") not in line


# ---------- B10: stage 1's crash guarantee, with vectors and a PDF ----------


def test_a_crash_before_commit_keeps_the_old_file_text_chunks_and_vectors(
    client: TestClient, conn: Conn, worker: Worker
) -> None:
    item_id = upload(client, png(), "chart.png")
    worker.run_once()
    before = chunk_rows(conn, item_id)
    text_before = client.get(f"/items/{item_id}/text").json()

    worker.describer = FakeDescriber("A different description.")
    client.post(f"/items/{item_id}/process", params={"from_raw": "true"})
    seen: list[int] = []
    worker.before_commit = crash_before_commit(seen, conn)
    with pytest.raises(Crash):
        worker.run_once()

    assert seen == [1]  # the dying transaction had written the new chunk
    assert chunk_rows(conn, item_id) == before
    assert client.get(f"/items/{item_id}/text").json() == text_before  # text rolled back too
    assert latest_job(conn, item_id)["status"] == "running"


# ---------- B11: deleting a file item ----------


def test_deleting_a_file_item_removes_chunks_text_and_stored_file(
    client: TestClient, conn: Conn, worker: Worker, storage: LocalStorage
) -> None:
    data = make_pdf(PDF_PAGES)
    item_id = upload(client, data, "report.pdf")
    worker.run_once()
    sha = client.get(f"/items/{item_id}").json()["sha256"]
    path = storage.root / file_key(sha)
    assert path.exists() and count(conn, "chunks") > 0 and count(conn, "file_texts") == 1

    assert client.delete(f"/items/{item_id}").status_code == 204

    assert count(conn, "chunks") == 0
    assert count(conn, "file_texts") == 0
    assert not path.exists()


# ---------- the text endpoint ----------


def test_file_text_is_404_before_processing_and_422_for_a_note(
    client: TestClient, conn: Conn
) -> None:
    item_id = upload(client, make_pdf(PDF_PAGES), "report.pdf")
    note = save_note(conn, "Buy oat milk")

    assert client.get(f"/items/{item_id}/text").status_code == 404
    assert client.get(f"/items/{note}/text").status_code == 422
    assert client.get("/items/999/text").status_code == 404
