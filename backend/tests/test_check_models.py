"""python -m mind_dump.check_models: one line per provider, exit 0 only if both work."""

import json

import anthropic
import httpx2
import pytest
from test_describe import error, message

from mind_dump import check_models
from mind_dump.describe import ClaudeDescriber
from mind_dump.embeddings import VoyageEmbedder, fake_vector


def voyage(answer: httpx2.Response | None = None) -> VoyageEmbedder:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if answer is not None:
            return answer
        texts = json.loads(request.content)["input"]
        data = [{"embedding": fake_vector(t), "index": i} for i, t in enumerate(texts)]
        return httpx2.Response(200, json={"data": data, "usage": {"total_tokens": 5}})

    http = httpx2.Client(transport=httpx2.MockTransport(handle))
    return VoyageEmbedder("vk-test", "voyage-4", http=http)


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


def test_the_command_with_no_keys_exits_1(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_models.main() == 1  # conftest removed any real keys: no network
    out = capsys.readouterr().out
    assert "VOYAGE_API_KEY is not set" in out and "ANTHROPIC_API_KEY is not set" in out
