"""P1 proof: "what did this page say on 1 November?" through the API (D32)."""

from datetime import UTC, datetime

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from mind_dump import pages
from mind_dump.fetcher import fetch, make_client
from mind_dump.storage import LocalStorage

URL = "https://registry.example.org/projects/1234"


def page(credits: str) -> str:
    return f"<html><body><nav>Menu</nav><main><p>Credits issued: {credits}</p></main></body></html>"


def land(
    conn: psycopg.Connection[DictRow],
    storage: LocalStorage,
    item_id: int,
    html: str,
    when: datetime,
) -> None:
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, html=html))
    with make_client(transport) as http:
        pages.process_fetch(conn, storage, item_id, fetch(http, URL, now=lambda: when))


@pytest.fixture
def item_id(client: TestClient, conn: psycopg.Connection[DictRow], storage: LocalStorage) -> int:
    """A saved link whose credits went from 120,000 to 145,000 on 3 November."""
    item = client.post("/items", json={"type": "link", "url": URL}).json()
    land(conn, storage, item["id"], page("120,000"), datetime(2026, 10, 13, 9, tzinfo=UTC))
    land(conn, storage, item["id"], page("145,000"), datetime(2026, 11, 3, 9, tzinfo=UTC))
    return item["id"]


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        ("2026-11-01", "Credits issued: 120,000"),  # before the edit: the old text
        ("2026-11-03T08:59:00Z", "Credits issued: 120,000"),
        ("2026-11-03", "Credits issued: 145,000"),  # a date means the end of that day
        ("2026-11-05", "Credits issued: 145,000"),
        (None, "Credits issued: 145,000"),  # no as_of: now
    ],
)
def test_as_of_returns_what_the_page_said_then(
    client: TestClient, item_id: int, as_of: str | None, expected: str
) -> None:
    params = {"as_of": as_of} if as_of else {}

    response = client.get(f"/items/{item_id}", params=params)

    assert response.status_code == 200
    assert response.json()["content"]["text"] == expected


def test_before_the_first_fetch_there_is_no_content(client: TestClient, item_id: int) -> None:
    response = client.get(f"/items/{item_id}", params={"as_of": "2026-10-01"})

    assert response.status_code == 200
    assert response.json()["content"] is None


def test_a_bad_as_of_is_422(client: TestClient, item_id: int) -> None:
    response = client.get(f"/items/{item_id}", params={"as_of": "last tuesday"})

    assert response.status_code == 422


def test_versions_lists_the_history_oldest_first(client: TestClient, item_id: int) -> None:
    response = client.get(f"/items/{item_id}/versions")

    assert response.status_code == 200
    v1, v2 = response.json()
    assert (v1["number"], v1["valid_to"]) == (1, "2026-11-03T09:00:00Z")
    assert (v2["number"], v2["valid_to"]) == (2, None)


def test_versions_of_an_unknown_item_is_404(client: TestClient) -> None:
    assert client.get("/items/999/versions").status_code == 404


def test_deleting_a_link_removes_its_fetches_versions_and_files(
    client: TestClient, conn: psycopg.Connection[DictRow], storage: LocalStorage, item_id: int
) -> None:
    assert (storage.root / "raw" / str(item_id)).exists()

    assert client.delete(f"/items/{item_id}").status_code == 204

    assert not (storage.root / "raw" / str(item_id)).exists()
    leftovers = conn.execute(
        "SELECT (SELECT count(*) FROM fetches) + (SELECT count(*) FROM item_versions) AS n"
    ).fetchone()
    assert leftovers["n"] == 0
