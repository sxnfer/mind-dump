"""Images become text through a description from Claude (D7b, D8).

An image has no text to extract, so a vision model writes a description, including any
text it can read in the image. That description is the item's text: it is chunked,
embedded and searched like a page. It is the model's words, not the source, so it is
stored and shown as a description.

The worker only sees `Describer`, so another model can be added later (D8). Settings
choose: ANTHROPIC_API_KEY set means Claude; unset means no describer, and an image item
ends `error` with a reason that says to set the key.

Claude is called through the official `anthropic` SDK (D8): typed errors and its own
short retries. Failures follow D35: timeouts, connection errors, 429 and 5xx are
temporary; any other 4xx is permanent, with a reason that names the setting to check.
"""

import base64
import struct
from collections.abc import Callable
from typing import Protocol

import anthropic

from mind_dump.config import Settings
from mind_dump.errors import PermanentError, TemporaryError

# The API's limits for one image (Vision docs, checked 2026-11-25): at most 10 MB once
# base64-encoded, and at most 8000 x 8000 pixels. Larger images are refused with a 400,
# so they are refused here first, with a reason the owner can act on.
MAX_IMAGE_BASE64_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 8000
MAX_TOKENS = 16000  # room for a dense screenshot's text; a description is usually far less
# Server-side refusal fallback (beta): if Claude's safety classifiers decline, the API
# runs the request again on its recommended fallback model, in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

PROMPT = (
    "This image was saved to a personal knowledge base. Write a description that makes it "
    "findable by search and useful as a source.\n"
    "1. First, transcribe all text you can read in the image, exactly as written, "
    "keeping its reading order and line breaks. Skip this step if there is no text.\n"
    "2. Then describe what the image shows: the subject, the setting, people or objects, "
    "and for a chart, table or diagram, what it shows and its key values.\n"
    "Write plain text in short paragraphs separated by blank lines. No preamble, and do "
    "not guess at things you cannot see."
)


class Describer(Protocol):
    model: str  # recorded with the text: which model wrote the description

    def describe(self, data: bytes, media_type: str) -> str: ...


def image_size(data: bytes, media_type: str) -> tuple[int, int] | None:
    """(width, height) from a PNG or JPEG header, or None if it can't be read.
    The standard library has no image reader; these two headers are simple."""
    if media_type == "image/png":
        if len(data) >= 24 and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return width, height
        return None
    if media_type == "image/jpeg":
        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            if marker == 0xFF:  # fill byte
                i += 1
                continue
            if marker in (0x01, *range(0xD0, 0xDA)):  # markers with no length
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2 : i + 4])
            # Start-of-frame markers hold the size: C0-CF except C4, C8 and CC.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if i + 9 > len(data):
                    return None
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return width, height
            i += 2 + length
        return None
    return None


def check_image(data: bytes, media_type: str) -> None:
    """Raise PermanentError for an image the API cannot take. Asking again won't shrink it."""
    encoded = 4 * ((len(data) + 2) // 3)
    if encoded > MAX_IMAGE_BASE64_BYTES:
        raise PermanentError(
            f"The image is too large for Claude: {encoded / 1024 / 1024:.1f} MB as base64, "
            f"the limit is {MAX_IMAGE_BASE64_BYTES // 1024 // 1024} MB (about 7.5 MB as a "
            "file). Save a smaller copy."
        )
    size = image_size(data, media_type)
    if size is not None and max(size) > MAX_IMAGE_PIXELS:
        raise PermanentError(
            f"The image is too large for Claude: {size[0]} x {size[1]} pixels, the limit is "
            f"{MAX_IMAGE_PIXELS} x {MAX_IMAGE_PIXELS}. Save a smaller copy."
        )


class ClaudeDescriber:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self.model = model
        self._client = client

    def _create(self, content: list[dict], max_tokens: int) -> object:
        """One Messages call, with every SDK error mapped to D35's two kinds."""
        try:
            return self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # Thinking is always on for this model; effort sets how much. A
                # description needs little reasoning, so the cheaper end.
                output_config={"effort": "low"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise TemporaryError(f"Can't reach the Claude API: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            raise PermanentError(
                f"The Claude API rejected the key (HTTP 401): check ANTHROPIC_API_KEY in .env "
                f"({exc.message})"
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise PermanentError(
                f"The Claude API refused this key (HTTP 403): check ANTHROPIC_API_KEY in .env "
                f"({exc.message})"
            ) from exc
        except anthropic.NotFoundError as exc:
            raise PermanentError(
                f"The Claude API has no model {self.model!r} (HTTP 404): check LLM_MODEL "
                f"({exc.message})"
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise TemporaryError(
                    f"The Claude API answered HTTP {exc.status_code}: {exc.message}"
                ) from exc
            raise PermanentError(
                f"The Claude API refused the request (HTTP {exc.status_code}): {exc.message}"
            ) from exc

    def describe(self, data: bytes, media_type: str) -> str:
        check_image(data, media_type)
        image = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }
        message = self._create([image, {"type": "text", "text": PROMPT}], MAX_TOKENS)
        return text_of(message)

    def check(self) -> None:
        """A tiny call with the same request shape as describe(): proves the key, the
        model name and the beta header all work. Raises like describe()."""
        self._create([{"type": "text", "text": "Reply with the word OK."}], 64)


def text_of(message: object) -> str:
    """The description from a finished response. Check stop_reason before content: a
    refusal or a cut-off answer must not be saved as if it were the description."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        explanation = getattr(details, "explanation", None) if details else None
        why = ", ".join(str(x) for x in (category, explanation) if x) or "no reason given"
        raise PermanentError(f"Claude declined to describe this image ({why})")
    if stop == "max_tokens":
        raise PermanentError(
            f"Claude's description was cut off at {MAX_TOKENS} tokens; nothing was saved"
        )
    if stop != "end_turn":
        raise PermanentError(f"Claude stopped for an unexpected reason: {stop!r}")
    text = "".join(
        block.text for block in getattr(message, "content", []) if block.type == "text"
    ).strip()
    if not text:
        raise PermanentError("Claude returned an empty description")
    return text


class FakeDescriber:
    """For tests: no network. Returns `text`, or calls `respond` for full control."""

    def __init__(
        self,
        text: str = "A photo of a whiteboard.",
        model: str = "fake-describer",
        respond: Callable[[bytes, str], str] | None = None,
    ) -> None:
        self.model = model
        self.text = text
        self.respond = respond
        self.calls = 0

    def describe(self, data: bytes, media_type: str) -> str:
        self.calls += 1
        check_image(data, media_type)
        return self.respond(data, media_type) if self.respond else self.text


def create_describer(settings: Settings) -> Describer | None:
    """Claude when ANTHROPIC_API_KEY is set; None when it is not. The key is passed in
    explicitly, so only the environment variable we document is ever used."""
    if settings.anthropic_api_key is None:
        return None
    # 120 s a try, and one quick SDK retry for a blip: at most about 4 minutes, inside the
    # job's 5-minute lease (jobs.DEFAULT_LEASE). D35's schedule handles longer outages.
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=120.0, max_retries=1)
    return ClaudeDescriber(client, settings.llm_model)
