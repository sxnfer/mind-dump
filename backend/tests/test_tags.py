"""Tags (M4): set, replace, clear and read back; one normalising rule; the tag filter;
an item's tag links go with it."""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from mind_dump.tags import normalise_tag, normalise_tags

Conn = psycopg.Connection[DictRow]


def note(client: TestClient, body: str = "A note about registries.") -> int:
    return client.post("/items", json={"type": "note", "body": body}).json()["id"]


def put(client: TestClient, item_id: int, tags: list[str]):  # noqa: ANN201
    return client.put(f"/items/{item_id}/tags", json={"tags": tags})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("carbon", "carbon"),
        ("  Carbon   Markets ", "carbon markets"),
        ("MRV\tand\nleakage", "mrv and leakage"),
        ("Ünïcode", "ünïcode"),
    ],
)
def test_one_rule_normalises_every_tag(raw: str, expected: str) -> None:
    assert normalise_tag(raw) == expected


def test_repeats_collapse_and_tags_come_back_sorted() -> None:
    assert normalise_tags(["Queues", "carbon", " CARBON "]) == ["carbon", "queues"]


def test_set_replace_clear_and_read_back(client: TestClient) -> None:
    item = note(client)
    assert client.get(f"/items/{item}").json()["tags"] == []

    response = put(client, item, ["Carbon  Markets", "MRV", "carbon markets"])
    assert response.status_code == 200
    assert response.json() == {"item_id": item, "tags": ["carbon markets", "mrv"]}
    assert client.get(f"/items/{item}").json()["tags"] == ["carbon markets", "mrv"]
    assert client.get("/items").json()["items"][0]["tags"] == ["carbon markets", "mrv"]

    # The body is the whole new set: mrv goes, queues arrives, carbon markets stays.
    assert put(client, item, ["queues", "carbon markets"]).json()["tags"] == [
        "carbon markets",
        "queues",
    ]
    assert client.get(f"/items/{item}").json()["tags"] == ["carbon markets", "queues"]

    assert put(client, item, []).json()["tags"] == []
    assert client.get(f"/items/{item}").json()["tags"] == []


def test_two_items_share_one_tag_row(client: TestClient, conn: Conn) -> None:
    a, b = note(client), note(client, "Another note.")
    put(client, a, ["carbon"])
    put(client, b, ["Carbon"])
    assert conn.execute("SELECT count(*) AS n FROM tags").fetchone()["n"] == 1
    assert client.get(f"/items/{a}").json()["tags"] == ["carbon"]
    assert client.get(f"/items/{b}").json()["tags"] == ["carbon"]


@pytest.mark.parametrize(
    "tags",
    [["   "], [""], ["x" * 51], [f"tag{i}" for i in range(21)], "carbon", [1]],
)
def test_bad_tags_are_422(client: TestClient, tags: object) -> None:
    item = note(client)
    response = client.put(f"/items/{item}/tags", json={"tags": tags})
    assert response.status_code == 422
    assert client.get(f"/items/{item}").json()["tags"] == []  # nothing half-written


def test_tags_on_a_missing_item_are_404(client: TestClient) -> None:
    assert put(client, 999, ["carbon"]).status_code == 404


def test_the_tag_filter(client: TestClient, conn: Conn) -> None:
    tagged, other = note(client), note(client)
    for item in (tagged, other):
        conn.execute(
            """
            INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text, source_sha256)
            VALUES (%s, 0, 0, 24, 'A note about registries.', repeat('a', 64))
            """,
            (item,),
        )
    put(client, tagged, ["Carbon Markets"])
    response = client.get("/search", params={"q": "registries", "tag": "carbon   MARKETS"})
    assert {h["item"]["id"] for h in response.json()["hits"]} == {tagged}
    response = client.get("/search", params={"q": "registries"})
    assert {h["item"]["id"] for h in response.json()["hits"]} == {tagged, other}


def test_deleting_an_item_removes_its_tag_links(client: TestClient, conn: Conn) -> None:
    gone, kept = note(client), note(client)
    put(client, gone, ["carbon", "queues"])
    put(client, kept, ["carbon"])
    assert client.delete(f"/items/{gone}").status_code == 204
    links = conn.execute("SELECT item_id FROM item_tags ORDER BY item_id").fetchall()
    assert [r["item_id"] for r in links] == [kept]
    # The tag names stay: they are the owner's vocabulary, ready for the next item.
    names = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
    assert [r["name"] for r in names] == ["carbon", "queues"]
