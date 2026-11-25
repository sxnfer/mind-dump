"""The Voyage AI client (D8) on httpx2's mock transport, and the fake embedder.

Checks the request it sends (URL, bearer header, model, input_type, batch splitting) and
how it maps answers and errors to D35's two kinds. No network.
"""

import json
import math

import httpx2
import pytest

from mind_dump.config import Settings
from mind_dump.embeddings import (
    DIMENSIONS,
    VOYAGE_URL,
    FakeEmbedder,
    VoyageEmbedder,
    create_embedder,
    fake_vector,
    to_pgvector,
)
from mind_dump.errors import PermanentError, TemporaryError


class Recorder:
    def __init__(self, answer: httpx2.Response | Exception | None = None) -> None:
        self.answer = answer
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        body = json.loads(request.content)
        # Answer in REVERSE order: the client must match vectors by index, not position.
        data = [
            {"object": "embedding", "embedding": fake_vector(t), "index": i}
            for i, t in enumerate(body["input"])
        ][::-1]
        usage = {"total_tokens": 10 * len(body["input"])}
        return httpx2.Response(200, json={"object": "list", "data": data, "usage": usage})

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def voyage(recorder: Recorder, batch_size: int = 128) -> VoyageEmbedder:
    http = httpx2.Client(transport=httpx2.MockTransport(recorder.handle))
    return VoyageEmbedder("vk-test", "voyage-4", http=http, batch_size=batch_size)


def test_the_request_has_the_url_key_model_and_input_type() -> None:
    rec = Recorder()

    voyage(rec).embed_documents(["a chunk"])
    voyage(rec).embed_query("a question")

    first, second = rec.requests
    assert str(first.url) == VOYAGE_URL == "https://api.voyageai.com/v1/embeddings"
    assert first.method == "POST"
    assert first.headers["authorization"] == "Bearer vk-test"
    assert rec.bodies() == [
        {"input": ["a chunk"], "model": "voyage-4", "input_type": "document",
         "output_dimension": DIMENSIONS},
        {"input": ["a question"], "model": "voyage-4", "input_type": "query",
         "output_dimension": DIMENSIONS},
    ]  # fmt: skip
    assert second.headers["authorization"] == "Bearer vk-test"


def test_texts_are_split_into_batches_and_vectors_come_back_in_order() -> None:
    rec = Recorder()
    texts = [f"chunk {i}" for i in range(7)]
    embedder = voyage(rec, batch_size=3)

    vectors = embedder.embed_documents(texts)

    assert [len(b["input"]) for b in rec.bodies()] == [3, 3, 1]
    assert [b["input"] for b in rec.bodies()][1] == ["chunk 3", "chunk 4", "chunk 5"]
    assert vectors == [fake_vector(t) for t in texts]  # by index, despite reversed answers
    assert embedder.total_tokens == 70  # usage summed across batches


def test_no_texts_means_no_request() -> None:
    rec = Recorder()
    assert voyage(rec).embed_documents([]) == []
    assert rec.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(429, json={"detail": "rate limited"}),
        httpx2.Response(500, text="oops"),
        httpx2.Response(503, text="busy"),
        httpx2.ConnectTimeout("connect timed out"),
        httpx2.ReadTimeout("read timed out"),
        httpx2.ConnectError("connection refused"),
    ],
    ids=["429", "500", "503", "connect timeout", "read timeout", "connection error"],
)
def test_temporary_failures(answer: httpx2.Response | Exception) -> None:
    with pytest.raises(TemporaryError, match="Voyage AI"):
        voyage(Recorder(answer)).embed_documents(["x"])


@pytest.mark.parametrize(
    ("code", "names"),
    [(401, "VOYAGE_API_KEY"), (403, "VOYAGE_API_KEY"), (400, "EMBEDDING_MODEL"), (404, "404")],
)
def test_permanent_failures_name_what_to_check(code: int, names: str) -> None:
    answer = httpx2.Response(code, json={"detail": "Voyage says no"})
    with pytest.raises(PermanentError) as caught:
        voyage(Recorder(answer)).embed_documents(["x"])
    assert names in str(caught.value)
    assert "Voyage says no" in str(caught.value)
    assert "vk-test" not in str(caught.value)  # the key itself never shows up


def test_a_wrong_dimension_count_is_permanent() -> None:
    answer = httpx2.Response(200, json={"data": [{"embedding": [0.1, 0.2], "index": 0}]})
    with pytest.raises(PermanentError, match="2 dimensions"):
        voyage(Recorder(answer)).embed_documents(["x"])


def test_a_missing_vector_is_temporary() -> None:
    answer = httpx2.Response(200, json={"data": [{"embedding": fake_vector("a"), "index": 0}]})
    with pytest.raises(TemporaryError, match="expected 0..1"):
        voyage(Recorder(answer)).embed_documents(["a", "b"])


def test_an_unreadable_answer_is_temporary() -> None:
    with pytest.raises(TemporaryError, match="can't read"):
        voyage(Recorder(httpx2.Response(200, text="<html>"))).embed_documents(["x"])


def test_the_fake_is_deterministic_unit_length_and_offline() -> None:
    fake = FakeEmbedder()
    a, b = fake.embed_documents(["same text", "other text"])
    assert a == fake.embed_documents(["same text"])[0]
    assert a != b
    assert len(a) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)
    assert fake_vector("same text", "model-a") != fake_vector("same text", "model-b")
    assert fake.calls == 2 and fake.texts == ["same text", "other text", "same text"]


def test_pgvector_text_form() -> None:
    assert to_pgvector([0.5, -1.0, 2]) == "[0.5,-1.0,2.0]"


def test_settings_choose_the_embedder() -> None:
    base = Settings(database_url="postgresql://x", migrations_dir=".")  # type: ignore[arg-type]
    assert create_embedder(base) is None
    configured = Settings(
        database_url="postgresql://x",
        migrations_dir=".",  # type: ignore[arg-type]
        voyage_api_key="vk-test",
        embedding_model="voyage-4-lite",
    )
    embedder = create_embedder(configured)
    assert isinstance(embedder, VoyageEmbedder) and embedder.model == "voyage-4-lite"
    assert "vk-test" not in repr(configured)  # a printed Settings never shows a key
