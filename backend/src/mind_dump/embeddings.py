"""Turn text into vectors (D8): one small interface, two hosted APIs, and a fake.

    embed_documents(texts) -> one vector per text, in the same order (for chunks)
    embed_query(text)      -> one vector (for searches, M4)

The worker only sees `Embedder`, so the provider can change (or a local model be added)
without touching it. EMBEDDING_PROVIDER chooses: `openai` (the default, San's choice on
2026-11-25) or `voyage`. Only that provider's key counts: with it unset there is no
embedder, and the worker saves chunks with no vectors (keyword search still works).

Both are one HTTPS endpoint, so both are called with httpx2 (D33), not an SDK:
    POST https://api.openai.com/v1/embeddings
    {"model": "text-embedding-3-small", "input": [...], "dimensions": 1024}
    POST https://api.voyageai.com/v1/embeddings
    {"input": [...], "model": "voyage-4", "input_type": "document", "output_dimension": 1024}
The two differ only in the request body and in a few error answers, so they share one
base class (_HttpEmbedder): batching, the call, reading vectors by index, the dimension
check and usage reporting are written once.

Failures follow D35: timeouts, connection errors, 429 and 5xx are temporary (the job is
retried); 400, 401, 403 and other 4xx are permanent, with a reason that names the
setting to check. OpenAI also answers 429 when the account has no credit
(`insufficient_quota`); asking again can't help, so that one is permanent.

Each request is reported to `on_usage` (D39), with the tokens the provider says it
used, so it is priced and counted toward the day's cap.
"""

import hashlib
import math
import struct
from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

import httpx2

from mind_dump.config import EMBEDDING_PROVIDERS, Settings
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.usage import Usage, UsageSink

DIMENSIONS = 1024  # chunks.embedding is vector(1024) (migration 009)
OPENAI_URL = "https://api.openai.com/v1/embeddings"
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# Texts per request: ~128 x 250 tokens, far inside both providers' limits (OpenAI: 2,048
# inputs and 300,000 tokens a request, 8,192 tokens an input; Voyage: 32,000 an input).
BATCH_SIZE = 128
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


def no_embedder_reason(settings: Settings) -> str:
    """Why there is no embedder, naming the key to set: for messages to the owner."""
    return (
        f"{settings.embedding.key_setting} is not set "
        f"for EMBEDDING_PROVIDER={settings.embedding_provider}"
    )


def _reason(code: int) -> str:
    try:
        return f"HTTP {code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


def _body(response: httpx2.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _detail(response: httpx2.Response) -> str:
    """The provider's own words about a refused request, kept short. Voyage puts them in
    `detail`, OpenAI in `error.message`."""
    body = _body(response)
    error = body.get("error")
    detail = body.get("detail") or (error.get("message") if isinstance(error, dict) else error)
    return str(detail or response.text or "")[:300]


class _HttpEmbedder:
    """What both hosted embedders share. A subclass sets the class attributes and
    `_payload`, and may refine `_refusal` for answers only it gives."""

    provider: str  # openai or voyage: model_usage.provider and the settings below
    url: str
    model_codes: tuple[int, ...] = (400,)  # refusals that mean "check EMBEDDING_MODEL"

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
        self.total_tokens = 0  # usage reported by the provider, summed: the cost meter
        info = EMBEDDING_PROVIDERS[self.provider]
        self.label, self.key_setting = info.label, info.key_setting

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            vectors += self._embed(texts[start : start + self.batch_size], "document")
        return vectors

    def embed_query(self, text: str) -> Vector:
        return self._embed([text], "query")[0]

    def _payload(self, texts: list[str], input_type: str) -> dict:
        raise NotImplementedError

    def _embed(self, texts: list[str], input_type: str) -> list[Vector]:
        if not texts:
            return []
        try:
            response = self._http.post(
                self.url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(texts, input_type),  # sets Content-Type: application/json
            )
        except httpx2.TimeoutException as exc:
            raise TemporaryError(f"{self.label} timed out: {exc}") from exc
        except (httpx2.NetworkError, httpx2.RemoteProtocolError, httpx2.ProxyError) as exc:
            raise TemporaryError(f"Can't reach {self.label}: {exc}") from exc
        if not 200 <= response.status_code <= 299:
            raise self._refusal(response)
        purpose = "embed_query" if input_type == "query" else "embed_documents"
        return self._vectors(response, len(texts), purpose)

    def _refusal(self, response: httpx2.Response) -> TemporaryError | PermanentError:
        code = response.status_code
        what = f"{self.label} answered {_reason(code)}"
        if code == 429 or code >= 500:
            return TemporaryError(what)
        if code in (401, 403):
            return PermanentError(f"{what}: check {self.key_setting} in .env ({_detail(response)})")
        if code in self.model_codes:
            return PermanentError(
                f"{what}: {_detail(response)} (check EMBEDDING_MODEL={self.model})"
            )
        return PermanentError(f"{what}: {_detail(response)}")

    def _vectors(self, response: httpx2.Response, expected: int, purpose: str) -> list[Vector]:
        try:
            body = response.json()
            data = body["data"]
            by_index = {int(row["index"]): row["embedding"] for row in data}
            usage = body.get("usage") or {}
            # Voyage sends total_tokens; OpenAI sends both (and they are equal for
            # embeddings). prompt_tokens is the fallback.
            tokens = int(usage.get("total_tokens", usage.get("prompt_tokens", 0)))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TemporaryError(f"{self.label} sent an answer we can't read: {exc}") from exc
        # Paid for, whatever happens next: record it before the checks below can raise.
        self.total_tokens += tokens
        if self.on_usage is not None:
            self.on_usage(Usage(self.provider, self.model, purpose, input_tokens=tokens))
        # Read by index, never by position: the index says which input a vector belongs to.
        if sorted(by_index) != list(range(expected)):
            raise TemporaryError(
                f"{self.label} sent vectors for inputs {sorted(by_index)}, "
                f"expected 0..{expected - 1}"
            )
        vectors = [by_index[i] for i in range(expected)]
        for vector in vectors:
            if len(vector) != DIMENSIONS:
                raise PermanentError(
                    f"{self.label} model {self.model} returned {len(vector)} dimensions; "
                    f"the database holds {DIMENSIONS} (check EMBEDDING_MODEL)"
                )
        return vectors


class OpenAIEmbedder(_HttpEmbedder):
    """OpenAI's embeddings endpoint (D8, the default).

    `dimensions: 1024` shortens text-embedding-3 vectors (1,536 or 3,072 by default) to
    fit chunks.embedding vector(1024). OpenAI's docs don't say whether shortened vectors
    have unit length, and nothing here relies on it: search uses cosine distance, which
    doesn't depend on length. There is no query-versus-document setting: a search and a
    chunk are sent the same way."""

    provider = "openai"
    url = OPENAI_URL
    model_codes = (400, 404)  # 404: no such model (or no access to it)

    def _payload(self, texts: list[str], input_type: str) -> dict:
        return {"model": self.model, "input": texts, "dimensions": DIMENSIONS}

    def _refusal(self, response: httpx2.Response) -> TemporaryError | PermanentError:
        # 429 also means "no credit left": retrying can't fix that, so it is permanent.
        # Checked in error.code and error.type, since the docs read don't say which.
        error = _body(response).get("error")
        if response.status_code == 429 and isinstance(error, dict):
            if "insufficient_quota" in (error.get("code"), error.get("type")):
                return PermanentError(
                    f"{self.label} answered {_reason(429)}: the account has no credit "
                    f"left (insufficient_quota); check billing at platform.openai.com "
                    f"({_detail(response)})"
                )
        return super()._refusal(response)


class VoyageEmbedder(_HttpEmbedder):
    """Voyage AI's embeddings endpoint (D8 as first chosen; EMBEDDING_PROVIDER=voyage).
    Voyage embeds a search and a chunk slightly differently (`input_type`)."""

    provider = "voyage"
    url = VOYAGE_URL

    def _payload(self, texts: list[str], input_type: str) -> dict:
        return {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": DIMENSIONS,
        }


EMBEDDERS: dict[str, type[_HttpEmbedder]] = {"openai": OpenAIEmbedder, "voyage": VoyageEmbedder}


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


def create_embedder(
    settings: Settings,
    on_usage: UsageSink | None = None,
    http: httpx2.Client | None = None,
) -> Embedder | None:
    """The EMBEDDING_PROVIDER's embedder when its key is set; None (chunks keep no
    vectors) when it is not. Another provider's key is ignored. on_usage receives every
    request's token count (D39)."""
    key = settings.embedding_api_key
    if key is None:
        return None
    cls = EMBEDDERS[settings.embedding_provider]
    return cls(key, settings.embedding_model, http=http, on_usage=on_usage)
