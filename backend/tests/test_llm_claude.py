"""The agent's Claude client (llm.py) through the real `anthropic` SDK on httpx2's mock
transport, as test_describe.py does: the exact request it streams, how it reads the
streamed answer, and how SDK errors map to D35's two kinds. E10 of M5's acceptance list.
No network.
"""

import json

import anthropic
import httpx2
import pytest
from chat_helpers import Conn, context, drain, settings

from mind_dump.agent import SYSTEM_PROMPT, TOOLS, CutOff, Refused, run_turn
from mind_dump.describe import FALLBACK_BETA
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.llm import ClaudeLLM, TurnRequest, create_llm, echo_content

USAGE = {
    "input_tokens": 120,
    "output_tokens": 1,
    "cache_creation_input_tokens": 900,
    "cache_read_input_tokens": 3000,
}


def sse(blocks: list[dict], stop_reason: str = "end_turn", stop_details: dict | None = None,
        model: str = "claude-opus-5-5") -> str:  # fmt: skip
    """A Messages API event stream that builds `blocks` the way the API streams them."""
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": model,
                "content": [], "stop_reason": None, "stop_sequence": None, "usage": USAGE,
            },
        }
    ]  # fmt: skip
    for i, block in enumerate(blocks):
        kind = block["type"]
        if kind == "thinking":
            start, deltas = (
                {"type": "thinking", "thinking": "", "signature": ""},
                [{"type": "signature_delta", "signature": block["signature"]}],
            )
        elif kind == "text":
            half = len(block["text"]) // 2
            start, deltas = (
                {"type": "text", "text": ""},
                [
                    {"type": "text_delta", "text": block["text"][:half]},
                    {"type": "text_delta", "text": block["text"][half:]},
                ],
            )
        elif kind == "tool_use":
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
        else:
            start, deltas = block, []
        events.append({"type": "content_block_start", "index": i, "content_block": start})
        events += [{"type": "content_block_delta", "index": i, "delta": d} for d in deltas]
        events.append({"type": "content_block_stop", "index": i})
    delta: dict = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    events.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": 42}})
    events.append({"type": "message_stop"})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def ok(blocks: list[dict], **kw: object) -> httpx2.Response:
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=sse(blocks, **kw)
    )


def error(code: int, kind: str, text: str) -> httpx2.Response:
    return httpx2.Response(code, json={"type": "error", "error": {"type": kind, "message": text}})


class FakeClaude:
    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def body(self, n: int = -1) -> dict:
        return json.loads(self.requests[n].content)


def claude(fake: FakeClaude, effort: str = "medium") -> ClaudeLLM:
    client = anthropic.Anthropic(
        api_key="sk-ant-test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(fake.handle)),
        max_retries=0,
    )
    return ClaudeLLM(client, "claude-opus-5-5", effort)


def request(final: bool = False) -> TurnRequest:
    return TurnRequest(SYSTEM_PROMPT, TOOLS, [{"role": "user", "content": "Hi"}], final)


def test_the_request_model_tools_cache_effort_thinking_stream_and_fallback() -> None:
    fake = FakeClaude(ok([THINKING, {"type": "text", "text": "Hello there."}]))
    pieces, turn = drain(claude(fake, effort="high").stream(request()))

    (sent,) = fake.requests
    assert sent.url.path == "/v1/messages"
    assert FALLBACK_BETA in sent.headers["anthropic-beta"]
    body = fake.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["stream"] is True
    assert body["max_tokens"] == 16000
    assert body["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert body["cache_control"] == {"type": "ephemeral"}  # automatic, for the tail
    assert body["tools"] == TOOLS  # byte-stable constants, in a fixed order
    assert [t["name"] for t in body["tools"]] == ["get_item", "search"]
    assert all(t["eager_input_streaming"] is True for t in body["tools"])
    assert body["thinking"] == {"type": "adaptive"}  # never disabled, never a budget
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    assert "tool_choice" not in body  # never forced: any/tool are a 400 on this model
    assert "temperature" not in body and "top_p" not in body

    assert "".join(pieces) == "Hello there."
    assert turn.content == [THINKING, {"type": "text", "text": "Hello there."}]
    assert turn.stop_reason == "end_turn" and turn.model == "claude-opus-5-5"
    u = turn.usage
    assert (u.provider, u.model, u.purpose) == ("anthropic", "claude-opus-5-5", "answer")
    assert (u.input_tokens, u.output_tokens) == (120, 42)
    assert (u.cache_creation_input_tokens, u.cache_read_input_tokens) == (900, 3000)


def test_the_last_turn_switches_tools_off_without_forcing_a_tool() -> None:
    fake = FakeClaude(ok([{"type": "text", "text": "ok"}]))
    drain(claude(fake).stream(request(final=True)))
    body = fake.body()
    assert body["tool_choice"] == {"type": "none"}
    assert body["tools"] == TOOLS  # unchanged, so the cache and thinking blocks hold


def test_tool_calls_come_back_parsed() -> None:
    use = {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "pool"}}
    fake = FakeClaude(ok([THINKING, use], stop_reason="tool_use"))
    _, turn = drain(claude(fake).stream(request()))
    assert turn.content[1] == use and turn.stop_reason == "tool_use"


def test_a_fallback_model_is_recorded_and_its_turn_echoed_by_the_rules() -> None:
    blocks = [
        THINKING,
        {
            "type": "fallback",
            "from": {"model": "claude-opus-5-5"},
            "to": {"model": "claude-opus-5"},
        },
        {"type": "text", "text": "Answer from the fallback."},
    ]
    fake = FakeClaude(ok(blocks, model="claude-opus-5"))
    _, turn = drain(claude(fake).stream(request()))
    assert turn.model == turn.usage.model == "claude-opus-5"  # priced as the model that ran
    # Thinking before the last fallback block is left out; the fallback block stays.
    assert [b["type"] for b in turn.content] == ["fallback", "text"]
    assert turn.content[0]["from"] == {"model": "claude-opus-5-5"}


def test_echo_content_leaves_a_turn_without_fallback_alone() -> None:
    blocks = [THINKING, {"type": "tool_use", "id": "t", "name": "search", "input": {}}]
    assert echo_content(blocks) == blocks


def test_a_refusal_and_a_cut_off_end_the_turn(conn: Conn) -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "Declined."}
    fake = FakeClaude(
        ok([], stop_reason="refusal", stop_details=details),
        ok([{"type": "text", "text": "Half"}], stop_reason="max_tokens"),
    )
    llm = claude(fake)
    with pytest.raises(Refused, match=r"declined to answer this \(cyber, Declined"):
        drain(run_turn(llm, context(conn), [], {}, "q"))
    with pytest.raises(CutOff, match="cut off"):
        drain(run_turn(llm, context(conn), [], {}, "q"))


@pytest.mark.parametrize(
    ("answer", "kind", "names"),
    [
        (error(429, "rate_limit_error", "slow down"), TemporaryError, "429"),
        (error(529, "overloaded_error", "overloaded"), TemporaryError, "529"),
        (httpx2.ConnectError("connection refused"), TemporaryError, "Can't reach"),
        (error(401, "authentication_error", "bad key"), PermanentError, "ANTHROPIC_API_KEY"),
        (error(404, "not_found_error", "model: nope"), PermanentError, "LLM_MODEL"),
        (error(400, "invalid_request_error", "bad thinking"), PermanentError, "bad thinking"),
    ],
    ids=["429", "529", "connection", "401", "404", "400"],
)  # fmt: skip
def test_sdk_errors_map_to_d35(answer: object, kind: type, names: str) -> None:
    with pytest.raises(kind) as caught:
        drain(claude(FakeClaude(answer)).stream(request()))  # type: ignore[arg-type]
    assert names in str(caught.value) and "sk-ant-test" not in str(caught.value)


def test_settings_choose_the_model_client() -> None:
    assert create_llm(settings()) is None
    made = create_llm(settings(anthropic_api_key="sk-ant-test", llm_effort="low"))
    assert isinstance(made, ClaudeLLM)
    assert (made.model, made.effort) == ("claude-opus-5-5", "low")
