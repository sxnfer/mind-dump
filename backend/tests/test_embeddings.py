"""The two embedding clients (D8), OpenAI (the default) and Voyage AI, on httpx2's mock
transport, the choice between them, and the fake embedder.

Checks the request each sends (URL, bearer header, model, the provider's own fields,
batch splitting) and how each maps answers and errors to D35's two kinds. No network.
"""

import json
import math
from decimal import Decimal

import httpx2
import pytest

from mind_dump.config import (
    DEFAULT_EMBEDDING_PRICES,
    EMBEDDING_PROVIDERS,
    Settings,
    load_settings,
)
from mind_dump.embeddings import (
    DIMENSIONS,
    OPENAI_URL,
    VOYAGE_URL,
    FakeEmbedder,
    OpenAIEmbedder,
    VoyageEmbedder,
    create_embedder,
    fake_vector,
    no_embedder_reason,
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
        usage = {"prompt_tokens": 10 * len(body["input"]), "total_tokens": 10 * len(body["input"])}
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
        embedding_provider="voyage",
        voyage_api_key="vk-test",
        embedding_model="voyage-4-lite",
    )
    embedder = create_embedder(configured)
    assert isinstance(embedder, VoyageEmbedder) and embedder.model == "voyage-4-lite"
    assert "vk-test" not in repr(configured)  # a printed Settings never shows a key


# ---------- OpenAI (D8, the default) ----------


def openai(recorder: Recorder, batch_size: int = 128, on_usage=None) -> OpenAIEmbedder:
    http = httpx2.Client(transport=httpx2.MockTransport(recorder.handle))
    return OpenAIEmbedder(
        "sk-test", "text-embedding-3-small", http=http, batch_size=batch_size, on_usage=on_usage
    )


def test_openai_request_has_the_url_key_model_input_and_dimensions() -> None:
    rec = Recorder()

    openai(rec).embed_documents(["a chunk"])
    openai(rec).embed_query("a question")

    first, second = rec.requests
    assert str(first.url) == OPENAI_URL == "https://api.openai.com/v1/embeddings"
    assert first.method == "POST"
    assert first.headers["authorization"] == "Bearer sk-test"
    assert first.headers["content-type"] == "application/json"
    # A query and a chunk go out the same way: OpenAI has no input_type.
    assert rec.bodies() == [
        {"model": "text-embedding-3-small", "input": ["a chunk"], "dimensions": 1024},
        {"model": "text-embedding-3-small", "input": ["a question"], "dimensions": 1024},
    ]
    assert all("input_type" not in b and isinstance(b["input"], list) for b in rec.bodies())
    assert second.headers["authorization"] == "Bearer sk-test"


def test_openai_300_texts_go_as_128_128_44_and_come_back_in_order() -> None:
    rec = Recorder()  # answers every batch with its data in REVERSE order
    texts = [f"chunk {i}" for i in range(300)]
    embedder = openai(rec)

    vectors = embedder.embed_documents(texts)

    assert [len(b["input"]) for b in rec.bodies()] == [128, 128, 44]
    assert rec.bodies()[2]["input"][0] == "chunk 256"
    assert vectors == [fake_vector(t) for t in texts]  # by index, not by position
    assert embedder.total_tokens == 3000


def test_openai_reports_usage_with_the_purpose_and_token_counts() -> None:
    seen = []
    embedder = openai(Recorder(), batch_size=2, on_usage=seen.append)

    embedder.embed_documents(["a", "b", "c"])
    assert len(embedder.embed_query("q")) == DIMENSIONS

    assert [(u.provider, u.model, u.purpose, u.input_tokens) for u in seen] == [
        ("openai", "text-embedding-3-small", "embed_documents", 20),
        ("openai", "text-embedding-3-small", "embed_documents", 10),
        ("openai", "text-embedding-3-small", "embed_query", 10),
    ]


def test_openai_usage_falls_back_to_prompt_tokens() -> None:
    seen = []
    data = [{"index": 0, "embedding": fake_vector("x")}]
    answer = httpx2.Response(200, json={"data": data, "usage": {"prompt_tokens": 9}})
    embedder = OpenAIEmbedder(
        "sk-test",
        "text-embedding-3-small",
        http=httpx2.Client(transport=httpx2.MockTransport(lambda r: answer)),
        on_usage=seen.append,
    )
    embedder.embed_query("x")
    assert [u.input_tokens for u in seen] == [9]


def openai_error(code: int, message: str, type_: str, err_code: str | None) -> httpx2.Response:
    return httpx2.Response(
        code, json={"error": {"message": message, "type": type_, "param": None, "code": err_code}}
    )


@pytest.mark.parametrize(
    "answer",
    [
        openai_error(429, "Rate limit reached", "requests", "rate_limit_exceeded"),
        httpx2.Response(429, text="slow down"),
        httpx2.Response(500, text="oops"),
        httpx2.Response(503, text="busy"),
        httpx2.ConnectTimeout("connect timed out"),
        httpx2.ReadTimeout("read timed out"),
        httpx2.ConnectError("connection refused"),
    ],
    ids=["429 rate limit", "429 no body", "500", "503", "connect timeout", "read timeout",
         "connection error"],
)  # fmt: skip
def test_openai_temporary_failures(answer: httpx2.Response | Exception) -> None:
    with pytest.raises(TemporaryError, match="OpenAI"):
        openai(Recorder(answer)).embed_documents(["x"])


@pytest.mark.parametrize("where", ["code", "type"])
def test_openai_429_with_no_credit_is_permanent_and_names_billing(where: str) -> None:
    answer = openai_error(
        429,
        "You exceeded your current quota, please check your plan and billing details.",
        "insufficient_quota" if where == "type" else "requests",
        "insufficient_quota" if where == "code" else None,
    )
    with pytest.raises(PermanentError) as caught:
        openai(Recorder(answer)).embed_documents(["x"])
    message = str(caught.value)
    assert "429" in message and "insufficient_quota" in message and "billing" in message


@pytest.mark.parametrize(
    ("code", "names"),
    [(401, "OPENAI_API_KEY"), (403, "OPENAI_API_KEY"), (400, "EMBEDDING_MODEL"),
     (404, "EMBEDDING_MODEL")],
)  # fmt: skip
def test_openai_permanent_failures_name_what_to_check(code: int, names: str) -> None:
    answer = openai_error(code, "OpenAI says no", "invalid_request_error", None)
    with pytest.raises(PermanentError) as caught:
        openai(Recorder(answer)).embed_documents(["x"])
    message = str(caught.value)
    assert names in message and "OpenAI says no" in message and f"HTTP {code}" in message
    assert "VOYAGE_API_KEY" not in message
    assert "sk-test" not in message  # the key itself never shows up


def test_openai_a_wrong_vector_length_is_permanent() -> None:
    answer = httpx2.Response(200, json={"data": [{"embedding": [0.1] * 1536, "index": 0}]})
    with pytest.raises(PermanentError, match="1536 dimensions"):
        openai(Recorder(answer)).embed_documents(["x"])


def test_openai_a_missing_vector_is_temporary() -> None:
    answer = httpx2.Response(200, json={"data": [{"embedding": fake_vector("a"), "index": 1}]})
    with pytest.raises(TemporaryError, match="expected 0..1"):
        openai(Recorder(answer)).embed_documents(["a", "b"])


# ---------- choosing the provider (F4) and its defaults (F5) ----------


def env_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings()


def test_openai_is_the_default_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = env_settings(monkeypatch, OPENAI_API_KEY="sk-test")
    assert cfg.embedding_provider == "openai"
    assert cfg.embedding_model == "text-embedding-3-small"
    embedder = create_embedder(cfg)
    assert isinstance(embedder, OpenAIEmbedder) and embedder.model == "text-embedding-3-small"
    assert "sk-test" not in repr(cfg)


def test_voyage_is_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = env_settings(monkeypatch, EMBEDDING_PROVIDER="voyage", VOYAGE_API_KEY="vk-test")
    assert cfg.embedding_model == "voyage-4"
    embedder = create_embedder(cfg)
    assert isinstance(embedder, VoyageEmbedder) and embedder.model == "voyage-4"


def test_an_unknown_provider_fails_at_startup_listing_the_valid_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError) as caught:
        env_settings(monkeypatch, EMBEDDING_PROVIDER="cohere")
    message = str(caught.value)
    assert "EMBEDDING_PROVIDER" in message and "openai" in message and "voyage" in message
    assert "cohere" in message


@pytest.mark.parametrize(
    ("env", "expect"),
    [
        ({"VOYAGE_API_KEY": "vk-test"}, None),  # openai configured: a Voyage key doesn't count
        ({"EMBEDDING_PROVIDER": "voyage", "OPENAI_API_KEY": "sk-test"}, None),  # and back
        ({"OPENAI_API_KEY": "sk-test", "VOYAGE_API_KEY": "vk-test"}, OpenAIEmbedder),
        ({"EMBEDDING_PROVIDER": "voyage", "OPENAI_API_KEY": "sk-test",
          "VOYAGE_API_KEY": "vk-test"}, VoyageEmbedder),
        ({"OPENAI_API_KEY": ""}, None),  # empty means unset (Compose passes ${NAME:-})
    ],
    ids=["openai, voyage key only", "voyage, openai key only", "openai, both keys",
         "voyage, both keys", "empty key"],
)  # fmt: skip
def test_only_the_configured_providers_key_counts(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expect: type | None
) -> None:
    embedder = create_embedder(env_settings(monkeypatch, **env))
    if expect is None:
        assert embedder is None
    else:
        assert type(embedder) is expect


def test_embedding_model_setting_overrides_the_providers_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = env_settings(monkeypatch, EMBEDDING_MODEL="text-embedding-3-large")
    assert cfg.embedding_model == "text-embedding-3-large"
    # Settings built in code take the provider's default too.
    voyage_cfg = Settings(
        database_url="x",
        migrations_dir=".",  # type: ignore[arg-type]
        embedding_provider="voyage",
    )
    assert voyage_cfg.embedding_model == "voyage-4"


def test_no_embedder_reason_names_the_configured_providers_key() -> None:
    base = Settings(database_url="x", migrations_dir=".")  # type: ignore[arg-type]
    assert no_embedder_reason(base) == "OPENAI_API_KEY is not set for EMBEDDING_PROVIDER=openai"
    voyage_cfg = Settings(
        database_url="x",
        migrations_dir=".",  # type: ignore[arg-type]
        embedding_provider="voyage",
    )
    assert "VOYAGE_API_KEY" in no_embedder_reason(voyage_cfg)


def test_each_providers_default_model_has_a_price() -> None:
    for provider in EMBEDDING_PROVIDERS.values():
        assert provider.default_model in DEFAULT_EMBEDDING_PRICES
    assert DEFAULT_EMBEDDING_PRICES == {
        "voyage-4": Decimal("0.06"),
        "text-embedding-3-small": Decimal("0.02"),
        "text-embedding-3-large": Decimal("0.13"),
    }
