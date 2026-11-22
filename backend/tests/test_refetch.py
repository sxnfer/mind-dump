"""The refetch command: how pages get fetched until the worker arrives (P3)."""

import httpx2
import psycopg
import pytest
from psycopg.rows import DictRow

from mind_dump import refetch
from mind_dump.fetcher import make_client

URL = "https://registry.example.org/projects/1234"


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The command's HTTP client serves this dict instead of the web."""
    html = {"text": "<main><p>Credits issued: 120,000</p></main>"}

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, html=html["text"])

    monkeypatch.setattr(refetch, "make_client", lambda: make_client(httpx2.MockTransport(handle)))
    return html


@pytest.fixture
def link_id(conn: psycopg.Connection[DictRow]) -> int:
    row = conn.execute("INSERT INTO items (type, url) VALUES ('link', %s) RETURNING id", (URL,))
    return row.fetchone()["id"]


def test_refetch_all_reports_each_step(
    site: dict[str, str], link_id: int, capsys: pytest.CaptureFixture[str]
) -> None:
    assert refetch.main(["--all"]) == 0
    assert "file stored | NEW VERSION" in capsys.readouterr().out

    assert refetch.main([str(link_id)]) == 0
    assert "same bytes, no new file | text unchanged" in capsys.readouterr().out

    site["text"] = "<main><p>Credits issued: 145,000</p></main>"
    assert refetch.main([str(link_id)]) == 0
    assert "file stored | NEW VERSION" in capsys.readouterr().out

    assert refetch.main(["--all", "--rebuild"]) == 0
    assert "2 version(s), rebuilt from landed files" in capsys.readouterr().out


def test_an_unknown_id_fails_with_a_message(
    site: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert refetch.main(["999"]) == 1
    assert "item 999: not a saved link" in capsys.readouterr().out
