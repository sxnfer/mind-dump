"""P1 proofs: raw landing (D28) and page history (D32), against a fake registry site.

The site is served by httpx2's mock transport (D33): no test touches the real web.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx2
import psycopg
import pytest
from psycopg.rows import DictRow

from mind_dump import pages
from mind_dump.extract import extract_text
from mind_dump.fetcher import fetch, make_client
from mind_dump.storage import LocalStorage

URL = "https://registry.example.org/projects/1234"
Conn = psycopg.Connection[DictRow]


def registry_page(
    status: str = "Registered",
    credits: str = "120,000",
    generated: str = "Oct 13",
    banner: str = "",
) -> str:
    """The article sits in <main>. The banner and footer are furniture that changes often."""
    cookie = f"<aside class='cookies'>{banner}</aside>" if banner else ""
    return f"""<!doctype html><html><head><title>Project 1234</title></head><body>
      <header><nav><a href="/">Registry</a> <a href="/projects">Projects</a></nav></header>
      {cookie}
      <main>
        <h1>Project 1234: Mangrove restoration</h1>
        <p>Status: {status}</p>
        <p>Credits issued: {credits}</p>
      </main>
      <footer>© 2026 Example Registry · page generated {generated}</footer>
    </body></html>"""


class FakeSite:
    """Path -> (status, html). Change `pages` between fetches to change the site."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {}
        self.redirects: dict[str, str] = {}

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path in self.redirects:
            return httpx2.Response(301, headers={"location": self.redirects[request.url.path]})
        status, page = self.pages.get(request.url.path, (404, "<h1>Page not found</h1>"))
        return httpx2.Response(status, html=page)


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def client(site: FakeSite) -> Iterator[httpx2.Client]:
    with make_client(httpx2.MockTransport(site.handle)) as http:
        yield http


@pytest.fixture
def item_id(conn: Conn) -> int:
    row = conn.execute("INSERT INTO items (type, url) VALUES ('link', %s) RETURNING id", (URL,))
    return row.fetchone()["id"]


def on(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 9, 0, tzinfo=UTC)


def fetch_on(
    conn: Conn,
    storage: LocalStorage,
    client: httpx2.Client,
    item_id: int,
    when: datetime,
    url: str = URL,
    extract: pages.Extractor = extract_text,
) -> pages.Outcome:
    result = fetch(client, url, now=lambda: when)
    return pages.process_fetch(conn, storage, item_id, result, extract)


def count(conn: Conn, table: str, item_id: int) -> int:
    query = {
        "fetches": "SELECT count(*) AS n FROM fetches WHERE item_id = %s",
        "item_versions": "SELECT count(*) AS n FROM item_versions WHERE item_id = %s",
    }[table]
    return conn.execute(query, (item_id,)).fetchone()["n"]


def files(storage: LocalStorage, item_id: int) -> list[str]:
    folder = storage.root / "raw" / str(item_id)
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def versions(conn: Conn, item_id: int) -> list[DictRow]:
    return pages.list_versions(conn, item_id)


# ---------- the first fetch ----------


def test_first_fetch_lands_the_file_records_the_fetch_and_opens_version_1(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())

    outcome = fetch_on(conn, storage, client, item_id, on(10, 13))

    assert outcome.landed.stored_new_file and outcome.new_version
    assert files(storage, item_id) == [outcome.landed.storage_key.split("/")[-1]]
    fetch_row = conn.execute("SELECT * FROM fetches WHERE item_id = %s", (item_id,)).fetchone()
    assert fetch_row["http_status"] == 200
    assert fetch_row["final_url"] == URL
    [v1] = versions(conn, item_id)
    assert v1["valid_from"] == on(10, 13) and v1["valid_to"] is None
    assert "Credits issued: 120,000" in v1["text"]
    assert "Registry Projects" not in v1["text"]  # the nav was dropped (D7)


# ---------- re-fetches: the P1 proof table ----------


def test_an_unchanged_page_adds_a_fetch_but_no_file_and_no_version(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())
    fetch_on(conn, storage, client, item_id, on(10, 13))

    outcome = fetch_on(conn, storage, client, item_id, on(10, 20))

    assert not outcome.landed.stored_new_file and not outcome.new_version
    assert count(conn, "fetches", item_id) == 2
    assert len(files(storage, item_id)) == 1  # raw hash matched: nothing new stored
    assert len(versions(conn, item_id)) == 1


def test_only_furniture_changed_stores_a_file_but_adds_no_version(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())
    fetch_on(conn, storage, client, item_id, on(10, 13))
    site.pages["/projects/1234"] = (
        200,
        registry_page(generated="Oct 27", banner="We use cookies. Accept?"),
    )

    outcome = fetch_on(conn, storage, client, item_id, on(10, 27))

    assert outcome.landed.stored_new_file  # new bytes are kept (D28)...
    assert not outcome.new_version  # ...but the meaning didn't change (D32)
    assert len(files(storage, item_id)) == 2
    assert len(versions(conn, item_id)) == 1


def test_a_changed_article_closes_the_old_version_and_opens_one_new_one(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())
    fetch_on(conn, storage, client, item_id, on(10, 13))
    site.pages["/projects/1234"] = (200, registry_page("Under review", "145,000", "Nov 3"))

    outcome = fetch_on(conn, storage, client, item_id, on(11, 3))

    assert outcome.new_version
    v1, v2 = versions(conn, item_id)
    assert (v1["valid_from"], v1["valid_to"]) == (on(10, 13), on(11, 3))
    assert (v2["valid_from"], v2["valid_to"]) == (on(11, 3), None)
    assert "Credits issued: 120,000" in v1["text"]
    assert "Credits issued: 145,000" in v2["text"]


def test_a_404_is_landed_as_evidence_and_never_touches_the_versions(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())
    fetch_on(conn, storage, client, item_id, on(10, 13))
    del site.pages["/projects/1234"]

    outcome = fetch_on(conn, storage, client, item_id, on(11, 10))

    assert outcome.landed.stored_new_file and not outcome.new_version
    statuses = conn.execute(
        "SELECT http_status FROM fetches WHERE item_id = %s ORDER BY fetched_at", (item_id,)
    ).fetchall()
    assert [row["http_status"] for row in statuses] == [200, 404]
    [v1] = versions(conn, item_id)
    assert v1["valid_to"] is None  # the last good text stays current
    assert "120,000" in v1["text"]


def test_a_redirect_records_where_the_link_really_led(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.redirects["/p/1234"] = URL
    site.pages["/projects/1234"] = (200, registry_page())

    fetch_on(conn, storage, client, item_id, on(10, 13), url="https://registry.example.org/p/1234")

    row = conn.execute("SELECT requested_url, final_url FROM fetches").fetchone()
    assert row["requested_url"] == "https://registry.example.org/p/1234"
    assert row["final_url"] == URL


# ---------- failures and re-runs ----------


def test_a_parser_crash_keeps_the_landed_file_and_the_fetch_record(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())

    def broken_extractor(html: bytes, content_type: str | None) -> str:
        raise RuntimeError("parser bug")

    with pytest.raises(RuntimeError, match="parser bug"):
        fetch_on(conn, storage, client, item_id, on(10, 13), extract=broken_extractor)

    # D28: a parser bug never costs the source. Step 1 committed; step 2 rolled back.
    assert count(conn, "fetches", item_id) == 1
    assert len(files(storage, item_id)) == 1
    assert versions(conn, item_id) == []

    # After the fix, re-process from the landed file. No network: the site is gone.
    site.pages.clear()
    fetch_id = conn.execute("SELECT id FROM fetches").fetchone()["id"]
    assert pages.apply_fetch(conn, storage, fetch_id) is True
    assert "120,000" in versions(conn, item_id)[0]["text"]


def test_applying_the_same_fetch_twice_gives_the_same_versions(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, registry_page())
    outcome = fetch_on(conn, storage, client, item_id, on(10, 13))

    assert pages.apply_fetch(conn, storage, outcome.landed.fetch_id) is False  # a retried job

    assert len(versions(conn, item_id)) == 1


def test_a_page_with_no_readable_text_opens_no_version(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    site.pages["/projects/1234"] = (200, "<html><body><div id='app'></div></body></html>")

    outcome = fetch_on(conn, storage, client, item_id, on(10, 13))

    assert not outcome.new_version
    assert versions(conn, item_id) == []


# ---------- an extractor change rebuilds history, it doesn't add to it ----------


def test_rebuild_after_an_extractor_fix_merges_fake_versions(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite, item_id: int
) -> None:
    def leaky_extractor(html: bytes, content_type: str | None) -> str:
        # A buggy rule set: it misses <main> and keeps the footer, so "page generated
        # <date>" leaks into the text and every fetch looks like a change.
        for old, new in [(b"main>", b"div>"), (b"<footer>", b"<p>"), (b"</footer>", b"</p>")]:
            html = html.replace(old, new)
        return extract_text(html)

    for when, generated in [(on(10, 13), "Oct 13"), (on(10, 20), "Oct 20"), (on(10, 27), "Oct 27")]:
        site.pages["/projects/1234"] = (200, registry_page(generated=generated))
        fetch_on(conn, storage, client, item_id, when, extract=leaky_extractor)
    assert len(versions(conn, item_id)) == 3  # fake: only the footer changed
    site.pages.clear()  # rebuilding needs no network

    count_after = pages.rebuild_versions(conn, storage, item_id)

    assert count_after == 1
    [v1] = versions(conn, item_id)
    assert (v1["valid_from"], v1["valid_to"]) == (on(10, 13), None)  # dates survive
    assert "page generated" not in v1["text"]


def test_processing_a_note_or_a_missing_item_is_refused(
    conn: Conn, storage: LocalStorage, client: httpx2.Client, site: FakeSite
) -> None:
    note = conn.execute("INSERT INTO items (type, body) VALUES ('note', 'n') RETURNING id")
    site.pages["/projects/1234"] = (200, registry_page())

    for target in (note.fetchone()["id"], 999):
        with pytest.raises(pages.LinkNotFound):
            fetch_on(conn, storage, client, target, on(10, 13))


def test_pages_refuses_a_connection_inside_an_open_transaction(
    database_url: str, storage: LocalStorage, client: httpx2.Client, item_id: int
) -> None:
    # Inside an open transaction, `with conn.transaction()` is only a savepoint: nothing
    # would really commit, and a crash later could undo a "finished" step.
    with psycopg.connect(database_url) as not_autocommit:
        result = fetch(client, URL, now=lambda: on(10, 13))
        with pytest.raises(ValueError, match="autocommit"):
            pages.land_fetch(not_autocommit, storage, item_id, result)  # type: ignore[arg-type]
