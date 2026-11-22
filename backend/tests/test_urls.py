"""The business key for links (D31): what counts as "the same URL"."""

import pytest

from mind_dump.urls import normalise_url

BASE = "https://registry.example.org/p/1234"


@pytest.mark.parametrize(
    ("saved", "key"),
    [
        # Same page: these never change what the server sends back.
        (f"{BASE}#credits", BASE),
        (f"{BASE}?utm_source=newsletter&utm_medium=email", BASE),
        (f"{BASE}?UTM_Source=x", BASE),
        (f"{BASE}?fbclid=abc", BASE),
        (f"{BASE}?gclid=abc&mc_cid=1&mc_eid=2", BASE),
        (f"{BASE}?id=9&utm_source=x#top", f"{BASE}?id=9"),
        # Kept: these can be different pages, and a false merge loses a page silently.
        (f"{BASE}/", f"{BASE}/"),
        (f"{BASE}?id=1234", f"{BASE}?id=1234"),
        ("https://registry.example.org/P/1234", "https://registry.example.org/P/1234"),
        # Kept byte for byte: re-encoding would turn %20 into + and make a new key.
        (f"{BASE}?q=blue%20carbon&utm_campaign=x", f"{BASE}?q=blue%20carbon"),
        (f"{BASE}?a=1&b=2", f"{BASE}?a=1&b=2"),
    ],
)
def test_normalise_url(saved: str, key: str) -> None:
    assert normalise_url(saved) == key
