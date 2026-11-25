"""The model behind the agent (D9): one small interface, Claude through the SDK, and a
scripted fake for tests.

    stream(request) -> a generator: it yields pieces of answer text as they arrive, and
                       RETURNS a ModelTurn (the whole response) when it ends

A generator, so the agent can pass each piece straight on to a streaming client (SSE)
with `turn = yield from llm.stream(request)`, and so a client that disconnects simply
stops it.

The agent only sees `LLM`, so tests run the real loop against a script, with no network.

ClaudeLLM, request by request (claude-api skill, for claude-opus-5-5):
- Streaming (`client.beta.messages.stream`), so a long answer never hits an HTTP
  timeout, and the app can show words as they come.
- Thinking: adaptive, the only setting this model takes (a disabled or budget config is
  a 400). Effort is the control, set explicitly from LLM_EFFORT: the model's default is
  medium, and effort decides how much it thinks, so what it costs.
- The system prompt and the tools are module constants (agent.py), byte for byte the
  same on every request, in a fixed order. A cache breakpoint on the system block
  caches tools + system (tools render first); top-level automatic caching moves a
  second breakpoint along the growing conversation. Below 512 tokens a prefix silently
  isn't cached; usage rows show cache reads when it is.
- No forced tool_choice (any/tool are a 400 on this model). On the last turn,
  tool_choice none: the model must answer with what it has.
- Server-side refusal fallback (beta, as describe.py): if the safety classifiers
  decline, the API re-runs the request on Anthropic's recommended fallback model in the
  same call. A response that still ends in `refusal` was refused by the whole chain.
- stop_reason is returned for the agent to check before it reads content.
- SDK errors become D35's two kinds (describe.api_error): temporary (connection,
  timeout, 429, 5xx) or permanent (401, 403, 404, other 4xx), never with the key.
- Tools have eager_input_streaming on (skill default for streamed requests with our own
  tools): their input streams unbuffered and unvalidated, so the agent validates every
  tool input itself, and JSON the SDK can't parse at all (a ValueError from the
  stream) re-sends the request, at most twice.
"""

import copy
import itertools
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from typing import Protocol

import anthropic

from mind_dump.config import Settings
from mind_dump.describe import FALLBACK_BETA, api_error, usage_of
from mind_dump.errors import TemporaryError
from mind_dump.usage import Usage

# A cost cap (D39) is a hard reason to bound one answer: 16,000 output tokens (thinking
# included) is at most $0.32 at claude-opus-5-5's $20 per million.
MAX_TOKENS = 16000
JSON_RETRIES = 2


@dataclass(frozen=True)
class TurnRequest:
    system: str
    tools: list[dict]
    messages: list[dict]  # the whole history, ending with a user message
    final: bool = False  # True: the model may not call tools (tool_choice none)


@dataclass(frozen=True)
class ModelTurn:
    """One model response. content is ready to append to the history as it is."""

    content: list[dict]
    stop_reason: str | None
    model: str  # the model that served it (a refusal fallback can change it)
    usage: Usage
    stop_details: dict | None = None


class LLM(Protocol):
    model: str

    def stream(self, request: TurnRequest) -> Generator[str, None, ModelTurn]: ...


def echo_content(blocks: list[dict]) -> list[dict]:
    """The blocks to append to the history, from a response's blocks.

    After a mid-answer refusal fallback, the API wants thinking, tool_use and unpaired
    server-tool blocks that come BEFORE the last `fallback` block left out, and the
    fallback block kept where it was (claude-api skill, refusal fallbacks). Everything
    else goes back exactly as it came. With no fallback block, nothing changes."""
    marks = [i for i, b in enumerate(blocks) if b.get("type") == "fallback"]
    if not marks:
        return blocks
    last = marks[-1]
    dropped = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(blocks) if i > last or b.get("type") not in dropped]


class ClaudeLLM:
    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        effort: str = "medium",
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client

    def params(self, request: TurnRequest) -> dict:
        """The request, exactly as sent. Separate so it can be read and tested."""
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "tools": request.tools,
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},  # automatic: the conversation's tail
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if request.final:
            params["tool_choice"] = {"type": "none"}
        return params

    def stream(self, request: TurnRequest) -> Generator[str, None, ModelTurn]:
        params = self.params(request)
        for attempt in itertools.count():
            try:
                with self._client.beta.messages.stream(**params) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield event.text
                    message = stream.get_final_message()
                break
            except ValueError as exc:
                # Tool input JSON the SDK could not parse at all (eager streaming). It
                # raised before the tool_use block completed, so there is no id to
                # answer: send the same request again. API errors are not ValueErrors.
                if attempt >= JSON_RETRIES:
                    raise TemporaryError(f"Claude sent tool input we can't read: {exc}") from exc
            except anthropic.APIError as exc:
                raise api_error(exc, self.model) from exc
        details = message.stop_details.to_dict() if message.stop_details else None
        return ModelTurn(
            content=echo_content(
                [b.to_dict(mode="json", exclude_none=True) for b in message.content]
            ),
            stop_reason=message.stop_reason,
            model=message.model,
            usage=usage_of(message, "answer"),
            stop_details=details,
        )


def create_llm(settings: Settings) -> LLM | None:
    """Claude when ANTHROPIC_API_KEY is set; None when it is not (the API answers 503).
    120 s a try and one quick SDK retry, as describe.py: D35 decides beyond that."""
    if settings.anthropic_api_key is None:
        return None
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=120.0, max_retries=1)
    return ClaudeLLM(client, settings.llm_model, settings.llm_effort)


# ---------- the scripted fake, for tests ----------

_ids = itertools.count(1)


def fake_usage(model: str = "fake-llm", input_tokens: int = 100, output_tokens: int = 20) -> Usage:
    return Usage("fake", model, "answer", input_tokens, output_tokens)


def _thinking() -> dict:
    # Real responses begin with a thinking block (empty text, a signature); the fake
    # does too, so tests see it replayed unchanged.
    return {"type": "thinking", "thinking": "", "signature": f"fake-sig-{next(_ids)}"}


def text_turn(
    text: str, stop_reason: str = "end_turn", model: str = "fake-llm", **extra: object
) -> ModelTurn:
    content = [_thinking()] + ([{"type": "text", "text": text}] if text else [])
    return ModelTurn(content, stop_reason, model, fake_usage(model), **extra)  # type: ignore[arg-type]


def tool_turn(*calls: tuple[str, dict], text: str | None = None) -> ModelTurn:
    """A response that asks for one or more tools: tool_turn(("search", {"query": "x"}))."""
    content = [_thinking()]
    if text:
        content.append({"type": "text", "text": text})
    for name, args in calls:
        content.append(
            {"type": "tool_use", "id": f"toolu_fake_{next(_ids)}", "name": name, "input": args}
        )
    return ModelTurn(content, "tool_use", "fake-llm", fake_usage())


@dataclass
class FailAfter:
    """A script step that streams `text`, then raises `error`: a failure mid-stream."""

    text: str
    error: Exception


Step = ModelTurn | Exception | FailAfter | Callable[[TurnRequest], ModelTurn]


@dataclass
class ScriptedLLM:
    """Plays its steps in order, one per call, and keeps a deep copy of every request,
    so a test can check exactly what the model was sent."""

    steps: list[Step]
    model: str = "fake-llm"
    requests: list[TurnRequest] = field(default_factory=list)

    def stream(self, request: TurnRequest) -> Generator[str, None, ModelTurn]:
        self.requests.append(copy.deepcopy(request))
        if not self.steps:
            raise AssertionError("ScriptedLLM: the script has no more steps")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, FailAfter):
            yield from _pieces(step.text)
            raise step.error
        turn = step(request) if callable(step) else step
        for block in turn.content:
            if block.get("type") == "text":
                yield from _pieces(block["text"])
        return turn


def _pieces(text: str) -> Iterator[str]:
    """Text in two pieces, like a stream's deltas."""
    half = len(text) // 2
    yield from (p for p in (text[:half], text[half:]) if p)
