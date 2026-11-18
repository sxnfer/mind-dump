"""POST/GET/DELETE /items: one test per happy path and per bad input (M1 done-when)."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


def save(client: TestClient, **payload: object) -> dict:
    response = client.post("/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- POST /items: happy paths ----------


def test_save_note_returns_201_with_location(client: TestClient) -> None:
    response = client.post("/items", json={"type": "note", "body": "Buy oat milk"})

    assert response.status_code == 201
    item = response.json()
    assert response.headers["location"] == f"/items/{item['id']}"
    assert item["type"] == "note"
    assert item["body"] == "Buy oat milk"
    assert item["url"] is None
    assert item["status"] == "saved"


def test_save_link_with_title(client: TestClient) -> None:
    item = save(client, type="link", url="https://example.com/vector-search", title="Vectors")

    assert item["url"] == "https://example.com/vector-search"
    assert item["title"] == "Vectors"
    assert item["body"] is None


def test_whitespace_is_trimmed_and_blank_title_becomes_null(client: TestClient) -> None:
    item = save(client, type="note", body="  hello  ", title="   ")

    assert item["body"] == "hello"
    assert item["title"] is None


# ---------- POST /items: bad input → 422 ----------


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({"type": "note", "body": ""}, "body"),
        ({"type": "note", "body": "    "}, "body"),
        ({"type": "note"}, "body"),
        ({"type": "link", "url": "not a url"}, "url"),
        ({"type": "link", "url": "ftp://example.com/file"}, "url"),
        ({"type": "link"}, "url"),
        ({"type": "note", "body": "x", "title": "t" * 201}, "title"),
        ({"type": "note", "body": "x" * 20_001}, "body"),
        ({"type": "link", "url": "https://example.com/" + "a" * 2048}, "url"),
    ],
    ids=[
        "empty-note",
        "blank-note",
        "missing-body",
        "bad-url",
        "non-http-url",
        "missing-url",
        "title-too-long",
        "note-too-long",
        "url-too-long",
    ],
)
def test_bad_input_is_rejected_with_422_naming_the_field(
    client: TestClient, payload: dict, bad_field: str
) -> None:
    response = client.post("/items", json=payload)

    assert response.status_code == 422
    fields = {error["loc"][-1] for error in response.json()["detail"]}
    assert bad_field in fields


def test_unknown_type_is_rejected(client: TestClient) -> None:
    response = client.post("/items", json={"type": "video", "url": "https://example.com"})

    assert response.status_code == 422


def test_rejected_input_saves_nothing(client: TestClient) -> None:
    client.post("/items", json={"type": "note", "body": ""})

    assert client.get("/items").json()["items"] == []


# ---------- POST /items: duplicates → 409 ----------


def test_same_link_twice_is_409_pointing_at_the_first(client: TestClient) -> None:
    first = save(client, type="link", url="https://example.com/article")

    response = client.post("/items", json={"type": "link", "url": "https://example.com/article"})

    assert response.status_code == 409
    assert response.json() == {"detail": "This link is already saved"}
    assert response.headers["location"] == f"/items/{first['id']}"
    assert len(client.get("/items").json()["items"]) == 1


def test_link_differing_only_in_host_case_is_a_duplicate(client: TestClient) -> None:
    save(client, type="link", url="https://example.com/article")

    response = client.post("/items", json={"type": "link", "url": "https://EXAMPLE.com/article"})

    assert response.status_code == 409


def test_different_links_and_notes_with_the_same_text_are_fine(client: TestClient) -> None:
    save(client, type="link", url="https://example.com/a")
    save(client, type="link", url="https://example.com/b")
    save(client, type="note", body="https://example.com/a")
    save(client, type="note", body="https://example.com/a")  # notes may repeat


def test_two_simultaneous_saves_of_one_link_only_one_wins(client: TestClient) -> None:
    """The race a check-then-insert in Python would lose. The unique index makes it safe."""
    start = threading.Barrier(8)

    def post() -> int:
        start.wait()  # release all threads at the same instant
        return client.post(
            "/items", json={"type": "link", "url": "https://example.com/race"}
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(pool.map(lambda _: post(), range(8)))

    assert codes == [201] + [409] * 7
    assert len(client.get("/items").json()["items"]) == 1


def test_a_deleted_link_can_be_saved_again(client: TestClient) -> None:
    first = save(client, type="link", url="https://example.com/again")
    client.delete(f"/items/{first['id']}")

    response = client.post("/items", json={"type": "link", "url": "https://example.com/again"})

    assert response.status_code == 201


# ---------- GET /items ----------


def test_list_is_newest_first(client: TestClient) -> None:
    first = save(client, type="note", body="first")
    second = save(client, type="note", body="second")

    ids = [item["id"] for item in client.get("/items").json()["items"]]

    assert ids == [second["id"], first["id"]]


def test_list_pages_with_before_cursor(client: TestClient) -> None:
    saved = [save(client, type="note", body=f"note {n}")["id"] for n in range(5)]

    page1 = client.get("/items", params={"limit": 2}).json()
    page2 = client.get("/items", params={"limit": 2, "before": page1["next_before"]}).json()
    page3 = client.get("/items", params={"limit": 2, "before": page2["next_before"]}).json()

    newest_first = list(reversed(saved))
    assert [i["id"] for i in page1["items"]] == newest_first[0:2]
    assert [i["id"] for i in page2["items"]] == newest_first[2:4]
    assert [i["id"] for i in page3["items"]] == newest_first[4:5]
    assert page3["next_before"] is None


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_list_rejects_bad_limit(client: TestClient, limit: int) -> None:
    assert client.get("/items", params={"limit": limit}).status_code == 422


# ---------- GET /items/{id} ----------


def test_get_one_item(client: TestClient) -> None:
    saved = save(client, type="link", url="https://example.com/")

    response = client.get(f"/items/{saved['id']}")

    assert response.status_code == 200
    assert response.json() == saved


def test_get_unknown_item_is_404(client: TestClient) -> None:
    response = client.get("/items/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Item 999 not found"}


# ---------- DELETE /items/{id} ----------


def test_delete_returns_204_and_item_is_gone(client: TestClient) -> None:
    saved = save(client, type="note", body="temporary")

    response = client.delete(f"/items/{saved['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/items/{saved['id']}").status_code == 404


def test_delete_unknown_item_is_404(client: TestClient) -> None:
    assert client.delete("/items/999").status_code == 404
