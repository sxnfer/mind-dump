"""Turn text into vectors (D8): one small interface, the Voyage AI client, and a fake.

    embed_documents(texts) -> one vector per text, in the same order (for chunks)
    embed_query(text)      -> one vector (for searches, M4)

The worker only sees `Embedder`, so a local model can be added later (D8) without touching
it. Settings choose: VOYAGE_API_KEY set means Voyage; unset means no embedder, and the
worker saves chunks with no vectors (keyword search still works).

Voyage is one HTTPS endpoint, so it is called with httpx2 (D33), not an SDK:
    POST https://api.voyageai.com/v1/embeddings
    {"input": [...], "model": "voyage-4", "input_type": "document", "output_dimension": 1024}
Failures follow D35: timeouts, connection errors, 429 and 5xx are temporary (the job is
retried); 400, 401, 403 and other 4xx are permanent, with a reason that names the
setting to check.

Each request is reported to `on_usage` (D39), with the tokens Voyage says it used, so
it is priced and counted toward the day's cap.
"""

import hashlib
import math
import struct
from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

import httpx2

from mind_dump.config import Settings
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.usage import Usage, UsageSink

DIMENSIONS = 1024  # chunks.embedding is vector(1024) (migration 009)
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 128  # texts per request: ~128 x 250 tokens, far inside Voyage's limits
TIMEOUT = httpx2.Timeout(30.0, connect=5.0)

Vector = list[float]


class Embedder(Protocol):
    model: str  # recorded on each chunk: a model change means re-embedding (D8)

    def embed_documents(self, texts: list[str]) -> list[Vector]: ...
    def embed_query(self, text: str) -> Vector: ...


def to_pgvector(vector: Vector) -> str:
    """pgvector's text form, '[0.1,0.2,...]'. The SQL casts it: %s::vector.
    Passing text keeps the pgvector Python package out of the dependencies."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _reason(code: int) -> str:
    try:
        return f"HTTP {code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


def _detail(response: httpx2.Response) -> str:
    """Voyage's own words about a refused request, kept short."""
    try:
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else None
    except ValueError:
        detail = None
    return str(detail or response.text or "")[:300]


class VoyageEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx2.Client | None = None,
        batch_size: int = BATCH_SIZE,
        on_usage: UsageSink | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.on_usage = on_usage  # D39: one model_usage row per request
        self._api_key = api_key
        self._http = http or httpx2.Client(timeout=TIMEOUT)
        self.total_tokens = 0  # usage reported by Voyage, summed: the cost meter

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            vectors += self._embed(texts[start : start + self.batch_size], "document")
        return vectors

    def embed_query(self, text: str) -> Vector:
        return self._embed([text], "query")[0]

    def _embed(self, texts: list[str], input_type: str) -> list[Vector]:
        if not texts:
            return []
        try:
            response = self._http.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self.model,
                    "input_type": input_type,
                    "output_dimension": DIMENSIONS,
                },
            )
        except httpx2.TimeoutException as exc:
            raise TemporaryError(f"Voyage AI timed out: {exc}") from exc
        except (httpx2.NetworkError, httpx2.RemoteProtocolError, httpx2.ProxyError) as exc:
            raise TemporaryError(f"Can't reach Voyage AI: {exc}") from exc
        self._check(response)
        purpose = "embed_query" if input_type == "query" else "embed_documents"
        return self._vectors(response, len(texts), purpose)

    def _check(self, response: httpx2.Response) -> None:
        code = response.status_code
        if 200 <= code <= 299:
            return
        what = f"Voyage AI answered {_reason(code)}"
        if code == 429 or code >= 500:
            raise TemporaryError(what)
        if code in (401, 403):
            raise PermanentError(f"{what}: check VOYAGE_API_KEY in .env ({_detail(response)})")
        if code == 400:
            raise PermanentError(
                f"{what}: {_detail(response)} (check EMBEDDING_MODEL={self.model})"
            )
        raise PermanentError(f"{what}: {_detail(response)}")

    def _vectors(self, response: httpx2.Response, expected: int, purpose: str) -> list[Vector]:
        try:
            body = response.json()
            data = body["data"]
            by_index = {int(row["index"]): row["embedding"] for row in data}
            tokens = int(body.get("usage", {}).get("total_tokens", 0))
        except (ValueError, KeyError, TypeError) as exc:
            raise TemporaryError(f"Voyage AI sent an answer we can't read: {exc}") from exc
        # Paid for, whatever happens next: record it before the checks below can raise.
        self.total_tokens += tokens
        if self.on_usage is not None:
            self.on_usage(Usage("voyage", self.model, purpose, input_tokens=tokens))
        # Read by index, never by position: the index says which input a vector belongs to.
        if sorted(by_index) != list(range(expected)):
            raise TemporaryError(
                f"Voyage AI sent vectors for inputs {sorted(by_index)}, expected 0..{expected - 1}"
            )
        vectors = [by_index[i] for i in range(expected)]
        for vector in vectors:
            if len(vector) != DIMENSIONS:
                raise PermanentError(
                    f"Voyage AI model {self.model} returned {len(vector)} dimensions; "
                    f"the database holds {DIMENSIONS} (check EMBEDDING_MODEL)"
                )
        return vectors


def fake_vector(text: str, model: str = "fake") -> Vector:
    """A unit vector made from the text's hash: the same text (and model) always gives
    the same vector, different texts give different ones."""
    raw = b"".join(
        hashlib.sha256(f"{model}\0{block}\0{text}".encode()).digest() for block in range(128)
    )
    numbers = [n / 2**31 for n in struct.unpack(f">{DIMENSIONS}i", raw[: DIMENSIONS * 4])]
    length = math.sqrt(sum(x * x for x in numbers))
    return [x / length for x in numbers]


class FakeEmbedder:
    """For tests: no network, deterministic, and it counts its calls.

    `on_call` runs before each batch, so a test can check where the call happens (outside
    the D30 transaction) or change the database at exactly that moment."""

    def __init__(
        self,
        model: str = "fake-embedder",
        on_call: Callable[[], None] | None = None,
        on_usage: UsageSink | None = None,
    ):
        self.model = model
        self.on_call = on_call
        self.on_usage = on_usage  # reports one word per token, so usage tests have numbers
        self.calls = 0
        self.texts: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        return self._embed(texts, "embed_documents")

    def embed_query(self, text: str) -> Vector:
        return self._embed([text], "embed_query")[0]

    def _embed(self, texts: list[str], purpose: str) -> list[Vector]:
        if not texts:
            return []
        if self.on_call is not None:
            self.on_call()
        self.calls += 1
        self.texts += texts
        if self.on_usage is not None:
            tokens = sum(len(t.split()) for t in texts)
            self.on_usage(Usage("fake", self.model, purpose, input_tokens=tokens))
        return [fake_vector(t, self.model) for t in texts]


def create_embedder(settings: Settings, on_usage: UsageSink | None = None) -> Embedder | None:
    """Voyage when VOYAGE_API_KEY is set; None (chunks keep no vectors) when it is not.
    on_usage receives every request's token count (D39)."""
    if settings.voyage_api_key is None:
        return None
    return VoyageEmbedder(settings.voyage_api_key, settings.embedding_model, on_usage=on_usage)
