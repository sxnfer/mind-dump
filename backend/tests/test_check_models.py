"""python -m mind_dump.check_models: one line per provider, exit 0 only if both work."""

import json

import anthropic
import httpx2
import pytest
from test_describe import error, message

from mind_dump import check_models
from mind_dump.config import Settings
from mind_dump.describe import ClaudeDescriber
from mind_dump.embeddings import OpenAIEmbedder, VoyageEmbedder, fake_vector


def http_for(answer: httpx2.Response | None = None) -> httpx2.Client:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if answer is not None:
            return answer
        texts = json.loads(request.content)["input"]
        data = [{"embedding": fake_vector(t), "index": i} for i, t in enumerate(texts)]
        return httpx2.Response(200, json={"data": data, "usage": {"total_tokens": 5}})

    return httpx2.Client(transport=httpx2.MockTransport(handle))


def voyage(answer: httpx2.Response | None = None) -> VoyageEmbedder:
    return VoyageEmbedder("vk-test", "voyage-4", http=http_for(answer))


def openai(answer: httpx2.Response | None = None) -> OpenAIEmbedder:
    return OpenAIEmbedder("sk-test", "text-embedding-3-small", http=http_for(answer))


def claude(answer: httpx2.Response) -> ClaudeDescriber:
    client = anthropic.Anthropic(
        api_key="sk-ant-test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(lambda r: answer)),
        max_retries=0,
    )
    return ClaudeDescriber(client, "claude-opus-5-5")


def test_both_working_is_ok() -> None:
    ok, lines = check_models.run(voyage(), claude(httpx2.Response(200, json=message())))
    assert ok
    assert lines == [
        "embeddings (Voyage AI): OK, voyage-4, 1024 dimensions (expected 1024)",
        "LLM (Claude): OK, claude-opus-5-5",
    ]


def test_bad_keys_give_the_exact_reason() -> None:
    ok, lines = check_models.run(
        voyage(httpx2.Response(401, json={"detail": "Provided API key is invalid."})),
        claude(error(401, "authentication_error", "invalid x-api-key")),
    )
    assert not ok
    assert "HTTP 401" in lines[0] and "VOYAGE_API_KEY" in lines[0]
    assert "HTTP 401" in lines[1] and "ANTHROPIC_API_KEY" in lines[1]


def test_one_working_is_not_enough() -> None:
    ok, lines = check_models.run(voyage(), None)
    assert not ok
    assert lines[0].startswith("embeddings (Voyage AI): OK")
    assert "ANTHROPIC_API_KEY is not set" in lines[1]


def test_openai_says_which_provider_and_model_it_checked() -> None:
    ok, lines = check_models.run(openai(), claude(httpx2.Response(200, json=message())))
    assert ok
    assert lines[0] == (
        "embeddings (OpenAI): OK, text-embedding-3-small, 1024 dimensions (expected 1024)"
    )
    bad = openai(httpx2.Response(401, json={"error": {"message": "Incorrect API key"}}))
    ok, lines = check_models.run(bad, None)
    assert not ok and "HTTP 401" in lines[0] and "OPENAI_API_KEY" in lines[0]


def test_the_command_with_no_keys_exits_1(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_models.main() == 1  # conftest removed any real keys: no network
    out = capsys.readouterr().out
    assert "embeddings (OpenAI): OPENAI_API_KEY is not set" in out
    assert "VOYAGE_API_KEY" not in out and "ANTHROPIC_API_KEY is not set" in out


def test_the_command_with_voyage_configured_names_its_key(
    database_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")  # not the configured provider's: ignored
    assert check_models.main() == 1
    out = capsys.readouterr().out
    assert "embeddings (Voyage AI): VOYAGE_API_KEY is not set" in out
    assert "OPENAI_API_KEY" not in out


def test_run_without_an_embedder_names_the_configured_key() -> None:
    cfg = Settings(database_url="x", migrations_dir=".", embedding_provider="voyage")  # type: ignore[arg-type]
    _, lines = check_models.run(None, None, cfg)
    assert lines[0].startswith("embeddings (Voyage AI): VOYAGE_API_KEY is not set")
