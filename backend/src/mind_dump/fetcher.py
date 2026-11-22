"""Download one saved page (D33).

Every HTTP status, 404 included, is a normal result that gets landed and recorded (D28).
Only network failures (DNS, refused, timeout) and oversized pages raise. P3 decides which
of those to retry.

Known gap, for M9: this follows any URL the owner saves, including private addresses
(SSRF). That is fine while each copy has one owner on their own Mac.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx2

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB: far above any article, far below a video
USER_AGENT = "MindDump/0.3"


class PageTooLarge(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str  # where the redirects ended up
    http_status: int
    content_type: str | None
    body: bytes
    fetched_at: datetime


def make_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    """`transport` lets tests serve recorded pages with no network."""
    return httpx2.Client(
        follow_redirects=True,
        max_redirects=5,
        timeout=httpx2.Timeout(10.0, connect=5.0),
        headers={"user-agent": USER_AGENT},
        transport=transport,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def fetch(client: httpx2.Client, url: str, now: Callable[[], datetime] = _now) -> FetchResult:
    fetched_at = now()
    with client.stream("GET", url) as response:
        body = bytearray()
        for chunk in response.iter_bytes():  # stop early instead of loading a huge body
            body += chunk
            if len(body) > MAX_BYTES:
                raise PageTooLarge(f"{url} is larger than {MAX_BYTES} bytes")
        return FetchResult(
            requested_url=url,
            final_url=str(response.url),
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            body=bytes(body),
            fetched_at=fetched_at,
        )
