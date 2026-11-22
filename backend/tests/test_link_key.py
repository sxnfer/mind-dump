"""P1 proof: saving the same URL twice gives 1 item, in every form that is the same page."""

import pytest
from fastapi.testclient import TestClient

BASE = "https://registry.example.org/p/1234"


def count_items(client: TestClient) -> int:
    return len(client.get("/items").json()["items"])


@pytest.mark.parametrize(
    "again",
    [
        BASE,
        f"{BASE}#credits",
        f"{BASE}?utm_source=newsletter",
        "HTTPS://Registry.Example.org:443/p/1234",
    ],
)
def test_the_same_page_in_another_form_is_409_and_one_item(client: TestClient, again: str) -> None:
    first = client.post("/items", json={"type": "link", "url": BASE})
    assert first.status_code == 201

    response = client.post("/items", json={"type": "link", "url": again})

    assert response.status_code == 409
    assert response.headers["location"] == f"/items/{first.json()['id']}"
    assert count_items(client) == 1


def test_the_stored_url_is_the_normalised_key(client: TestClient) -> None:
    response = client.post("/items", json={"type": "link", "url": f"{BASE}?utm_source=x#top"})

    assert response.json()["url"] == BASE


@pytest.mark.parametrize("other", [f"{BASE}/", f"{BASE}?id=5678"])
def test_forms_that_may_be_different_pages_are_kept_apart(client: TestClient, other: str) -> None:
    # D31: a false split is visible and fixable; a false merge silently loses a page.
    assert client.post("/items", json={"type": "link", "url": BASE}).status_code == 201

    assert client.post("/items", json={"type": "link", "url": other}).status_code == 201
    assert count_items(client) == 2
