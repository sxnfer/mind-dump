"""The Claude describer (D7b, D8) through the real `anthropic` SDK on httpx2's mock
transport: the request it builds, how it reads stop_reason, and how SDK errors map to
D35's two kinds. Also the image size checks. No network.
"""

import json
import struct

import anthropic
import httpx2
import pytest

from mind_dump.config import Settings
from mind_dump.describe import (
    FALLBACK_BETA,
    MAX_IMAGE_BASE64_BYTES,
    ClaudeDescriber,
    check_image,
    create_describer,
    image_size,
)
from mind_dump.errors import PermanentError, TemporaryError

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + struct.pack(">II", 640, 480) + b"\x08\x02"


def message(stop_reason: str = "end_turn", text: str = "A chart.", **extra: object) -> dict:
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text:
        content.append({"type": "text", "text": text})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        **extra,
    }


def error(code: int, kind: str, text: str) -> httpx2.Response:
    return httpx2.Response(code, json={"type": "error", "error": {"type": kind, "message": text}})


class FakeClaude:
    def __init__(self, answer: httpx2.Response | Exception | None = None) -> None:
        self.answer = answer if answer is not None else httpx2.Response(200, json=message())
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def describer(fake: FakeClaude) -> ClaudeDescriber:
    client = anthropic.Anthropic(
        api_key="sk-ant-test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(fake.handle)),
        max_retries=0,  # the SDK's own retries would only slow the test down
    )
    return ClaudeDescriber(client, "claude-opus-5-5")


def test_the_request_sends_the_image_prompt_model_and_fallback() -> None:
    fake = FakeClaude()

    assert describer(fake).describe(PNG, "image/png") == "A chart."

    (request,) = fake.requests
    assert request.url.path == "/v1/messages"
    assert request.headers["authorization"] == "Bearer sk-ant-test"  # SDK 2.x sends a bearer
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"}
    assert "thinking" not in body  # always on for this model; only effort is set
    image, prompt = body["messages"][0]["content"]
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["type"] == "base64"
    assert "transcribe all text" in prompt["text"]


def test_a_refusal_is_permanent_and_names_the_category() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "Declined."}
    fake = FakeClaude(httpx2.Response(200, json=message("refusal", "", stop_details=details)))
    with pytest.raises(PermanentError, match=r"declined to describe this image \(cyber"):
        describer(fake).describe(PNG, "image/png")


def test_a_refusal_without_details_is_still_handled() -> None:
    fake = FakeClaude(httpx2.Response(200, json=message("refusal", "partial")))
    with pytest.raises(PermanentError, match="no reason given"):
        describer(fake).describe(PNG, "image/png")


def test_a_cut_off_description_is_not_saved() -> None:
    fake = FakeClaude(httpx2.Response(200, json=message("max_tokens", "Half a desc")))
    with pytest.raises(PermanentError, match="cut off"):
        describer(fake).describe(PNG, "image/png")


def test_an_empty_description_is_permanent() -> None:
    fake = FakeClaude(httpx2.Response(200, json=message("end_turn", "")))
    with pytest.raises(PermanentError, match="empty"):
        describer(fake).describe(PNG, "image/png")


@pytest.mark.parametrize(
    "answer",
    [
        error(429, "rate_limit_error", "slow down"),
        error(500, "api_error", "oops"),
        error(529, "overloaded_error", "overloaded"),
        httpx2.ConnectError("connection refused"),
        httpx2.ReadTimeout("timed out"),
    ],
    ids=["429", "500", "529", "connection error", "timeout"],
)
def test_temporary_failures(answer: httpx2.Response | Exception) -> None:
    with pytest.raises(TemporaryError, match="Claude API"):
        describer(FakeClaude(answer)).describe(PNG, "image/png")


@pytest.mark.parametrize(
    ("answer", "names"),
    [
        (error(401, "authentication_error", "invalid x-api-key"), "ANTHROPIC_API_KEY"),
        (error(403, "permission_error", "no access"), "ANTHROPIC_API_KEY"),
        (error(404, "not_found_error", "model: nope"), "LLM_MODEL"),
        (error(400, "invalid_request_error", "image too big"), "image too big"),
    ],
    ids=["401", "403", "404", "400"],
)
def test_permanent_failures_name_what_to_check(answer: httpx2.Response, names: str) -> None:
    with pytest.raises(PermanentError) as caught:
        describer(FakeClaude(answer)).describe(PNG, "image/png")
    assert names in str(caught.value)
    assert "sk-ant-test" not in str(caught.value)


def test_check_uses_the_same_request_shape() -> None:
    fake = FakeClaude()
    describer(fake).check()
    body = json.loads(fake.requests[0].content)
    assert body["fallbacks"] == "default" and body["model"] == "claude-opus-5-5"


def test_an_image_too_large_for_the_api_is_refused_before_any_call() -> None:
    fake = FakeClaude()
    big = PNG + b"\x00" * (MAX_IMAGE_BASE64_BYTES * 3 // 4 + 10)
    with pytest.raises(PermanentError, match="MB as base64"):
        describer(fake).describe(big, "image/png")
    assert fake.requests == []


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_image_sizes_are_read_from_png_and_jpeg_headers() -> None:
    assert image_size(PNG, "image/png") == (640, 480)
    assert image_size(jpeg(1920, 1080), "image/jpeg") == (1920, 1080)
    assert image_size(b"\xff\xd8garbage", "image/jpeg") is None
    check_image(jpeg(8000, 8000), "image/jpeg")  # exactly the limit is fine
    with pytest.raises(PermanentError, match="8001 x 10 pixels"):
        check_image(jpeg(8001, 10), "image/jpeg")


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\xf7\x00\x00" + b"\x00" * 8


def webp(chunk: bytes, payload: bytes) -> bytes:
    body = b"WEBP" + chunk + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossy(width: int, height: int) -> bytes:  # a VP8 key frame: tag, start code, size
    return webp(b"VP8 ", b"\x9d\x01\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height))


def webp_lossless(width: int, height: int) -> bytes:  # signature, then 14 + 14 bits, minus one
    return webp(b"VP8L", b"\x2f" + struct.pack("<I", (width - 1) | (height - 1) << 14))


def webp_extended(width: int, height: int) -> bytes:  # flags, reserved, 24 + 24 bits, minus one
    sizes = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return webp(b"VP8X", b"\x10\x00\x00\x00" + sizes)


def test_image_sizes_are_read_from_gif_and_webp_headers() -> None:
    assert image_size(gif(640, 480), "image/gif") == (640, 480)
    assert image_size(webp_lossy(1920, 1080), "image/webp") == (1920, 1080)
    assert image_size(webp_lossless(1280, 800), "image/webp") == (1280, 800)
    assert image_size(webp_extended(16383, 9000), "image/webp") == (16383, 9000)
    # The top two bits of a lossy size are a scale hint, not part of the size.
    assert image_size(webp_lossy(1920 | 0xC000, 1080), "image/webp") == (1920, 1080)
    assert image_size(b"GIF89a\x01", "image/gif") is None
    assert image_size(b"RIFF\x00\x00\x00\x00WEBPVP8Z" + b"\x00" * 20, "image/webp") is None


def test_the_pixel_limit_holds_for_gif_and_webp() -> None:
    check_image(gif(8000, 8000), "image/gif")
    check_image(webp_lossless(8000, 1), "image/webp")
    with pytest.raises(PermanentError, match="8001 x 600 pixels"):
        check_image(gif(8001, 600), "image/gif")
    with pytest.raises(PermanentError, match="400 x 9000 pixels"):
        check_image(webp_extended(400, 9000), "image/webp")
    with pytest.raises(PermanentError, match="8200 x 100 pixels"):
        check_image(webp_lossy(8200, 100), "image/webp")


def test_settings_choose_the_describer() -> None:
    base = Settings(database_url="postgresql://x", migrations_dir=".")  # type: ignore[arg-type]
    assert create_describer(base) is None
    configured = Settings(
        database_url="postgresql://x",
        migrations_dir=".",  # type: ignore[arg-type]
        anthropic_api_key="sk-ant-test",
        llm_model="claude-opus-5-5",
    )
    made = create_describer(configured)
    assert isinstance(made, ClaudeDescriber) and made.model == "claude-opus-5-5"
    assert "sk-ant-test" not in repr(configured)
