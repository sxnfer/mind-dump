"""Search (M4, D37): keyword, vector, the RRF merge, filters, fallbacks and indexes.

No network: the embedder is the fake (a text's vector comes from its hash, so a chunk
whose text equals the query has distance 0) or a stub that fails on purpose. Most tests
write chunks straight into the table, with exactly the vectors they need; C6 goes through
the real worker.
"""

import math
from collections.abc import Iterator
from datetime import UTC, datetime
from fractions import Fraction

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from test_worker import FakeClock, save_note

from mind_dump import jobs
from mind_dump.embeddings import FakeEmbedder, Vector, fake_vector, to_pgvector
from mind_dump.errors import TemporaryError
from mind_dump.fetcher import make_client
from mind_dump.search import (
    KEYWORD_SQL,
    VECTOR_ANN_SQL,
    VECTOR_EXACT_SQL,
    Filters,
    SearchUnavailable,
    is_strict,
    keyword_params,
    rrf,
    search,
    vector_params,
)
from mind_dump.storage import LocalStorage
from mind_dump.worker import Worker

Conn = psycopg.Connection[DictRow]
MODEL = FakeEmbedder().model  # "fake-embedder": what the API's fake will embed with


# ---------- helpers ----------


def add_item(
    conn: Conn,
    type_: str = "note",
    created_at: datetime | None = None,
    title: str | None = None,
) -> int:
    """An item row of any type (no job: these tests write chunks themselves)."""
    n = conn.execute("SELECT count(*) AS n FROM items").fetchone()["n"]
    row = conn.execute(
        """
        INSERT INTO items (type, title, url, body, sha256, media_type, size_bytes, created_at)
        VALUES (%(type)s, %(title)s,
                CASE WHEN %(type)s = 'link' THEN 'https://example.org/' || %(n)s END,
                CASE WHEN %(type)s = 'note' THEN 'body' END,
                CASE WHEN %(type)s = 'file' THEN lpad(to_hex(%(n)s::int), 64, '0') END,
                CASE WHEN %(type)s = 'file' THEN 'application/pdf' END,
                CASE WHEN %(type)s = 'file' THEN 10 END,
                coalesce(%(created_at)s, now()))
        RETURNING id
        """,
        {"type": type_, "title": title, "n": n, "created_at": created_at},
    ).fetchone()
    return row["id"]


def add_chunk(
    conn: Conn,
    item_id: int,
    text: str,
    vector: Vector | None | str = "auto",
    model: str = MODEL,
) -> int:
    """A chunk with the fake's vector of its text ("auto"), a given vector, or none."""
    if vector == "auto":
        vector = fake_vector(text, model)
    index = conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE item_id = %s", (item_id,)
    ).fetchone()["n"]
    return conn.execute(
        """
        INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text, source_sha256,
                            embedding, embedding_model)
        VALUES (%s, %s, 0, %s, %s, repeat('a', 64), %s::vector, %s)
        RETURNING id
        """,
        (
            item_id,
            index,
            len(text),
            text,
            to_pgvector(vector) if vector is not None else None,
            model if vector is not None else None,
        ),
    ).fetchone()["id"]


def near(vector: Vector, seed: str, weight: float) -> Vector:
    """A unit vector a little way from `vector`: more weight, further away."""
    noise = fake_vector(seed)
    mixed = [a + weight * b for a, b in zip(vector, noise, strict=True)]
    length = math.sqrt(sum(x * x for x in mixed))
    return [x / length for x in mixed]


def ids(result) -> list[int]:
    return [h.chunk_id for h in result.hits]


class FailingEmbedder:
    """Embeds nothing: every query call fails the way a 503 from Voyage does (D35)."""

    model = MODEL

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        raise TemporaryError("Voyage AI answered HTTP 503 Service Unavailable")

    def embed_query(self, text: str) -> Vector:
        raise TemporaryError("Voyage AI answered HTTP 503 Service Unavailable")


@pytest.fixture
def fake_client(client: TestClient) -> TestClient:
    """The API with the fake embedder, as if VOYAGE_API_KEY were set."""
    client.app.state.embedder = FakeEmbedder()
    return client


# ---------- C1: keyword ----------


def test_a_word_in_exactly_one_chunk_ranks_that_chunk_first(conn: Conn) -> None:
    item = add_item(conn)
    add_chunk(conn, item, "The registry issues credits after verification.")
    target = add_chunk(conn, item, "Mangroves store carbon along the coast. Registry rules apply.")
    add_chunk(conn, item, "Credits are retired in the registry when used.")
    result = search(conn, "mangroves", "keyword")
    assert ids(result) == [target]
    assert result.hits[0].keyword_rank == 1 and result.hits[0].vector_rank is None


def test_stemming_finds_retry_when_searching_retries(conn: Conn) -> None:
    item = add_item(conn)
    target = add_chunk(conn, item, "A failed job gets one retry after a minute.")
    add_chunk(conn, item, "Nothing about that here.")
    assert ids(search(conn, "retries", "keyword")) == [target]
    assert ids(search(conn, "retrying", "keyword")) == [target]


def test_quoted_phrases_and_minus_terms_follow_websearch_to_tsquery(conn: Conn) -> None:
    item = add_item(conn)
    phrase = add_chunk(conn, item, "Credits move to the buffer pool when a fire burns the forest.")
    apart = add_chunk(conn, item, "The pool of buyers wanted a buffer against price swings.")
    no_fire = add_chunk(conn, item, "The buffer pool holds a share of every project's credits.")
    # Unquoted: both words anywhere, so all three chunks.
    assert set(ids(search(conn, "buffer pool", "keyword"))) == {phrase, apart, no_fire}
    # Quoted: the words next to each other, in that order.
    assert set(ids(search(conn, '"buffer pool"', "keyword"))) == {phrase, no_fire}
    # Minus: without that word.
    assert ids(search(conn, '"buffer pool" -fire', "keyword")) == [no_fire]
    # or: either word.
    assert set(ids(search(conn, "forest or buyers", "keyword"))) == {phrase, apart}


@pytest.mark.parametrize("query", ["the of and", "!!! ... ???", "-", '""', "or"])
def test_stop_words_or_punctuation_only_give_an_empty_list(
    conn: Conn, client: TestClient, query: str
) -> None:
    item = add_item(conn)
    add_chunk(conn, item, "The registry and the methodology of the project.")
    assert search(conn, query, "keyword").hits == []
    response = client.get("/search", params={"q": query, "mode": "keyword"})
    assert response.status_code == 200, response.text
    assert response.json()["hits"] == []


# A typed sentence (D37's two tiers). Each of these fails with plain websearch_to_tsquery,
# which needs every word of the query in one chunk.


def test_a_typed_question_finds_a_passage_missing_one_of_its_words(conn: Conn) -> None:
    item = add_item(conn)
    add_chunk(conn, item, "Seedlings were planted at the start of the rainy season.")
    target = add_chunk(conn, item, "Of 18,450 tonnes, 2,770 went to the buffer pool.")
    # "many" (lexeme 'mani') and "credits" are in no chunk: all-words finds nothing.
    assert ids(search(conn, "How many credits went to the buffer pool?", "keyword"))[0] == target
    # "goes" (lexeme 'goe') is the only word the passage lacks.
    share = add_chunk(conn, item, "A share of the credits, ten to twenty percent, enters the pool.")
    assert ids(search(conn, "What share of credits goes into the pool?", "keyword"))[0] == share


def test_chunks_with_all_the_words_rank_above_chunks_with_some(conn: Conn) -> None:
    item = add_item(conn)
    # Two of the three words, many times: the higher ts_rank...
    some = add_chunk(conn, item, "Buffer pool. " * 8)
    # ...but this one has all three, once each.
    every = add_chunk(conn, item, "The share each project puts in the buffer pool.")
    add_chunk(conn, item, "Nothing in common with the query.")
    hits = search(conn, "buffer pool share", "keyword").hits
    assert [h.chunk_id for h in hits] == [every, some]
    assert hits[0].keyword_score < hits[1].keyword_score  # the tier decided, not the score


def test_quotes_or_a_minus_term_turn_the_any_word_tier_off(conn: Conn) -> None:
    item = add_item(conn)
    chunk = add_chunk(conn, item, "The buffer pool holds credits against fire.")
    # Plain words: "mangrove" is missing, but the other words still find the chunk.
    assert ids(search(conn, "mangrove buffer pool", "keyword")) == [chunk]
    # Quotes or a -word: exact, as written. No chunk has "mangrove", so nothing.
    assert ids(search(conn, '"buffer pool" mangrove', "keyword")) == []
    assert ids(search(conn, "mangrove buffer -drought", "keyword")) == []
    assert ids(search(conn, "buffer pool -fire", "keyword")) == []


@pytest.mark.parametrize(
    ("query", "strict"),
    [
        ("buffer pool", False),
        ("project MD-2291 credits", False),  # a hyphen inside a word is not a minus
        ("forest or buyers", False),
        ('"buffer pool"', True),
        ("buffer -fire", True),
        ("-fire buffer", True),
    ],
)
def test_which_queries_are_strict(query: str, strict: bool) -> None:
    assert is_strict(query) is strict


@pytest.mark.parametrize("mode", ["keyword", "hybrid"])
def test_filters_apply_to_both_keyword_tiers(conn: Conn, mode: str) -> None:
    all_note = add_item(conn, "note")
    all_link = add_item(conn, "link")
    some_note = add_item(conn, "note")
    some_link = add_item(conn, "link")
    add_chunk(conn, all_note, "Crediting periods can be renewed twice.", vector=None)
    add_chunk(conn, all_link, "Crediting periods can be renewed twice, they said.", vector=None)
    add_chunk(conn, some_note, "Renewal is not automatic.", vector=None)
    add_chunk(conn, some_link, "A renewed baseline.", vector=None)
    result = search(
        conn, "How many times are crediting periods renewed?", mode, Filters(type="note"),
        embedder=FakeEmbedder(),
    )  # fmt: skip
    assert [h.item_id for h in result.hits if h.keyword_rank] == [all_note, some_note]


# ---------- C2: vector ----------


def test_the_chunk_equal_to_the_query_ranks_first_in_vector_mode(
    fake_client: TestClient, conn: Conn
) -> None:
    item = add_item(conn)
    texts = ["Leakage moves logging next door.", "Buffer pools cover fire.", "Queues."]
    conn_rows = [add_chunk(conn, item, text) for text in texts]
    params = {"q": "Buffer pools cover fire.", "mode": "vector"}
    response = fake_client.get("/search", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "vector" and body["degraded"] is False
    first = body["hits"][0]
    assert first["chunk_id"] == conn_rows[1]
    assert first["vector_rank"] == 1 and first["keyword_rank"] is None
    assert first["vector_distance"] == pytest.approx(0, abs=1e-6)


@pytest.mark.parametrize("filtered", [False, True])
def test_chunks_with_no_vector_never_appear_in_the_vector_list(conn: Conn, filtered: bool) -> None:
    item = add_item(conn)
    embedded = [add_chunk(conn, item, f"Embedded chunk {i}.") for i in range(3)]
    add_chunk(conn, item, "Waiting for a key: no vector yet.", vector=None)
    add_chunk(conn, item, "Also waiting.", vector=None)
    filters = Filters(item_id=item) if filtered else Filters()
    result = search(
        conn, "Waiting for a key: no vector yet.", "vector", filters, 50, FakeEmbedder()
    )
    assert sorted(ids(result)) == embedded
    assert all(h.vector_distance is not None for h in result.hits)


def test_vectors_from_another_model_are_never_compared(conn: Conn) -> None:
    item = add_item(conn)
    ours = add_chunk(conn, item, "Same model.")
    add_chunk(conn, item, "Old model.", model="old-model")
    result = search(conn, "Old model.", "vector", embedder=FakeEmbedder())
    assert ids(result) == [ours]


# ---------- C3: the merge ----------


def test_rrf_by_hand() -> None:
    # keyword: 7, 3, 5        vector: 3, 9, 5
    #   3: 1/62 + 1/61 = 123/3782       (both lists)
    #   5: 1/63 + 1/63 = 2/63           (both, lower down)
    #   7: 1/61                         (keyword only, rank 1)
    #   9: 1/62                         (vector only, rank 2)
    fused = rrf({"keyword": [7, 3, 5], "vector": [3, 9, 5]})
    assert [f.chunk_id for f in fused] == [3, 5, 7, 9]
    assert fused[0].score == Fraction(1, 62) + Fraction(1, 61)
    assert fused[1].score == Fraction(2, 63)
    assert fused[2].score == Fraction(1, 61)
    assert fused[0].ranks == {"keyword": 2, "vector": 1}
    assert fused[2].ranks == {"keyword": 1}  # missing from a list: absent, scores 0 there
    assert float(fused[0].score) == pytest.approx(0.032522, abs=1e-6)


def test_rrf_breaks_ties_the_same_way_every_time() -> None:
    # 10 and 20 both score 1/61 + 1/62 exactly; same best rank (1): the lower id wins.
    assert [f.chunk_id for f in rrf({"keyword": [10, 20], "vector": [20, 10]})] == [10, 20]
    assert [f.chunk_id for f in rrf({"vector": [20, 10], "keyword": [10, 20]})] == [10, 20]
    # 4 (rank 1 in one list) and 8 (rank 1 in the other): tie, lower id first.
    assert [f.chunk_id for f in rrf({"keyword": [8], "vector": [4]})] == [4, 8]
    # Three lists: 5 is rank 1 in one list (1/61 + 0 + 0); 6 is rank 2 in two lists and
    # missing from the third (1/62 + 1/62 = 1/31 > 1/61). The score decides before ranks.
    fused = rrf({"a": [5, 6], "b": [9, 6], "c": [9]})
    assert [f.chunk_id for f in fused] == [9, 6, 5]
    # A score tie broken by the best rank, not the id: 30 is rank 1 in "a" and absent
    # from "b"; 2 is rank 1 in "b"... equal best ranks again, so the id decides: [2, 30].
    assert [f.chunk_id for f in rrf({"a": [30], "b": [2]})] == [2, 30]


def test_rrf_of_nothing_is_nothing() -> None:
    assert rrf({"keyword": [], "vector": []}) == []


def test_a_chunk_in_both_lists_beats_chunks_ranked_high_in_only_one(conn: Conn) -> None:
    query = "mangrove survival"
    q = fake_vector(query, MODEL)
    item = add_item(conn)
    # Keyword rank 1 (the words, many times), but no vector: keyword list only.
    words_only = add_chunk(
        conn, item, "Mangrove survival, mangrove survival, mangrove survival.", vector=None
    )
    # Vector rank 1 (the query's own vector), but none of the words: vector list only.
    meaning_only = add_chunk(conn, item, "Seedlings lived longer after the rains.", vector=q)
    # Rank 2 in both lists.
    both = add_chunk(
        conn, item, "Survival of mangrove seedlings rose to seventy percent.", near(q, "b", 0.3)
    )
    for i in range(5):  # far away, and none of the words
        add_chunk(conn, item, f"Unrelated note number {i}.")

    result = search(conn, query, "hybrid", embedder=FakeEmbedder())
    first = result.hits[0]
    assert first.chunk_id == both
    assert (first.keyword_rank, first.vector_rank) == (2, 2)
    assert first.score == pytest.approx(2 / 62)
    singles = {h.chunk_id: h for h in result.hits[1:3]}
    assert set(singles) == {words_only, meaning_only}
    assert singles[words_only].keyword_rank == 1 and singles[words_only].vector_rank is None
    assert singles[meaning_only].vector_rank == 1 and singles[meaning_only].keyword_rank is None


# ---------- C4: filters ----------


@pytest.fixture
def library(conn: Conn) -> dict[str, int]:
    """Four items, one chunk each, all matching the word "registry" and all at the same
    distance from the query's vector, so only the filters tell them apart."""
    q = fake_vector("registry", MODEL)
    items = {
        "note_jan": add_item(conn, "note", datetime(2026, 1, 10, 12, tzinfo=UTC)),
        "link_mar": add_item(conn, "link", datetime(2026, 3, 5, 12, tzinfo=UTC)),
        "file_jun": add_item(conn, "file", datetime(2026, 6, 1, 12, tzinfo=UTC)),
        "note_jun": add_item(conn, "note", datetime(2026, 6, 30, 23, 59, tzinfo=UTC)),
    }
    for name, item in items.items():
        add_chunk(conn, item, f"The registry entry for {name}.", vector=q)
    conn.execute("INSERT INTO tags (name) VALUES ('carbon'), ('pipelines')")
    conn.execute(
        """
        INSERT INTO item_tags (item_id, tag_id)
        SELECT i, (SELECT id FROM tags WHERE name = 'carbon') FROM unnest(%s::bigint[]) i
        """,
        ([items["note_jan"], items["file_jun"]],),
    )
    conn.execute(
        "INSERT INTO item_tags VALUES (%s, (SELECT id FROM tags WHERE name = 'pipelines'))",
        (items["note_jun"],),
    )
    return items


CASES = [
    ({}, {"note_jan", "link_mar", "file_jun", "note_jun"}),
    ({"type": "note"}, {"note_jan", "note_jun"}),
    ({"type": "link"}, {"link_mar"}),
    ({"type": "file"}, {"file_jun"}),
    ({"from": "2026-03-01"}, {"link_mar", "file_jun", "note_jun"}),
    ({"to": "2026-03-05"}, {"note_jan", "link_mar"}),  # a date means the whole day
    ({"from": "2026-06-30", "to": "2026-06-30"}, {"note_jun"}),
    ({"from": "2026-02-01T00:00:00Z", "to": "2026-06-01T11:00:00Z"}, {"link_mar"}),
    ({"tag": "carbon"}, {"note_jan", "file_jun"}),
    ({"tag": "  CARBON "}, {"note_jan", "file_jun"}),  # normalised like the tags API
    ({"tag": "nothing-tagged-this"}, set()),
    ({"type": "note", "tag": "carbon"}, {"note_jan"}),
    ({"type": "note", "from": "2026-06-01"}, {"note_jun"}),
    ({"tag": "carbon", "from": "2026-02-01", "to": "2026-12-31"}, {"file_jun"}),
]


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
@pytest.mark.parametrize(("params", "expected"), CASES)
def test_filters_narrow_results_in_every_mode(
    fake_client: TestClient,
    library: dict[str, int],
    mode: str,
    params: dict,
    expected: set[str],
) -> None:
    response = fake_client.get("/search", params={"q": "registry", "mode": mode, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == mode
    names = {v: k for k, v in library.items()}
    assert {names[h["item"]["id"]] for h in body["hits"]} == expected


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_the_item_id_filter_in_every_mode(
    fake_client: TestClient, library: dict[str, int], mode: str
) -> None:
    item = library["link_mar"]
    response = fake_client.get("/search", params={"q": "registry", "mode": mode, "item_id": item})
    assert [h["item"]["id"] for h in response.json()["hits"]] == [item]


@pytest.fixture
def crowded(conn: Conn) -> tuple[int, list[int]]:
    """250 chunks of another item, all closer to the query than any chunk of the target
    item: the trap an approximate index falls into under a filter."""
    query_vector = fake_vector("mangrove carbon", MODEL)
    decoy = add_item(conn)
    for i in range(250):
        add_chunk(conn, decoy, f"Decoy {i}: mangrove carbon.", near(query_vector, f"d{i}", 0.05))
    target = add_item(conn, "link")  # the only link, and the only tagged item
    target_chunks = [add_chunk(conn, target, f"Target {i}: mangrove carbon.") for i in range(12)]
    conn.execute("INSERT INTO tags (name) VALUES ('target')")
    conn.execute("INSERT INTO item_tags SELECT %s, id FROM tags WHERE name = 'target'", (target,))
    conn.execute("ANALYZE chunks")
    return target, target_chunks


def test_the_trap_is_real_a_filter_after_hnsw_loses_the_item(
    conn: Conn, crowded: tuple[int, list[int]]
) -> None:
    """Proof that the next test can fail: the naive query (HNSW first, filter after)
    finds none of the target's chunks, because its 40 neighbours are all decoys."""
    target, _ = crowded
    # "+ 0" hides item_id's btree index, as with a type or tag filter (no index on chunks
    # can serve those), so the planner has only HNSW-then-filter or a full scan.
    naive = VECTOR_ANN_SQL.replace("ORDER BY", "AND c.item_id + 0 = %(item_id)s ORDER BY")
    conn.execute("SET enable_seqscan = off")  # make the planner take the HNSW index
    assert "chunks_embedding_hnsw_idx" in plan(
        conn, naive, {**vector_params(fake_vector("x"), MODEL, Filters(), 10), "item_id": target}
    )
    params = vector_params(fake_vector("mangrove carbon", MODEL), MODEL, Filters(), 10)
    rows = conn.execute(naive, {**params, "item_id": target}).fetchall()
    assert len(rows) < 10


@pytest.mark.parametrize("by", ["item_id", "type", "tag"])
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_a_selective_filter_never_loses_results_that_exist(
    conn: Conn, crowded: tuple[int, list[int]], mode: str, by: str
) -> None:
    target, target_chunks = crowded
    # The planner's index-happiest mood: if HNSW could be used under the filter, it would.
    conn.execute("SET enable_seqscan = off")
    unfiltered = search(conn, "mangrove carbon", "vector", limit=10, embedder=FakeEmbedder())
    assert all(h.item_id != target for h in unfiltered.hits)  # decoys really are closer

    filters = {
        "item_id": Filters(item_id=target),
        "type": Filters(type="link"),
        "tag": Filters(tag="target"),
    }[by]
    for limit in (10, 50):
        result = search(conn, "mangrove carbon", mode, filters, limit, FakeEmbedder())
        assert {h.item_id for h in result.hits} == {target}
        assert len(result.hits) == min(limit, len(target_chunks))
        assert all(h.vector_rank is not None for h in result.hits)


def test_a_selective_filter_through_the_api(
    fake_client: TestClient, crowded: tuple[int, list[int]]
) -> None:
    target, _ = crowded
    response = fake_client.get(
        "/search",
        params={"q": "mangrove carbon", "mode": "vector", "item_id": target, "limit": 10},
    )
    assert [h["item"]["id"] for h in response.json()["hits"]] == [target] * 10


# ---------- C5: no embedder, or a failing one ----------


def test_no_embedder_hybrid_gives_keyword_results_and_says_so(
    client: TestClient, conn: Conn
) -> None:
    assert client.app.state.embedder is None  # tests run with no VOYAGE_API_KEY
    item = add_item(conn)
    chunk = add_chunk(conn, item, "The registry tracks every credit.", vector=None)
    body = client.get("/search", params={"q": "registry"}).json()
    assert body["requested_mode"] == "hybrid"
    assert body["mode"] == "keyword"
    assert body["degraded"] is True
    assert "VOYAGE_API_KEY" in body["notice"]
    assert [h["chunk_id"] for h in body["hits"]] == [chunk]
    assert body["hits"][0]["keyword_rank"] == 1


def test_no_embedder_keyword_mode_is_not_degraded(client: TestClient, conn: Conn) -> None:
    add_chunk(conn, add_item(conn), "The registry tracks every credit.", vector=None)
    body = client.get("/search", params={"q": "registry", "mode": "keyword"}).json()
    assert (body["mode"], body["degraded"], body["notice"]) == ("keyword", False, None)


def test_no_embedder_vector_mode_is_a_clear_error(client: TestClient, conn: Conn) -> None:
    add_chunk(conn, add_item(conn), "The registry tracks every credit.")
    response = client.get("/search", params={"q": "registry", "mode": "vector"})
    assert response.status_code == 503
    assert "VOYAGE_API_KEY" in response.json()["detail"]


def test_a_failing_embedder_degrades_hybrid_to_keyword(client: TestClient, conn: Conn) -> None:
    client.app.state.embedder = FailingEmbedder()
    chunk = add_chunk(conn, add_item(conn), "The registry tracks every credit.")
    response = client.get("/search", params={"q": "registry", "mode": "hybrid"})
    assert response.status_code == 200
    body = response.json()
    assert (body["mode"], body["degraded"]) == ("keyword", True)
    assert "503" in body["notice"]
    assert [h["chunk_id"] for h in body["hits"]] == [chunk]


def test_a_failing_embedder_in_vector_mode_is_a_clear_error(client: TestClient, conn: Conn) -> None:
    client.app.state.embedder = FailingEmbedder()
    add_chunk(conn, add_item(conn), "The registry tracks every credit.")
    response = client.get("/search", params={"q": "registry", "mode": "vector"})
    assert response.status_code == 503
    assert "embedding model failed" in response.json()["detail"]
    with pytest.raises(SearchUnavailable):
        search(conn, "registry", "vector", embedder=FailingEmbedder())


# ---------- validation ----------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "   "},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 51},
        {"q": "x", "mode": "fuzzy"},
        {"q": "x", "type": "video"},
        {"q": "x", "from": "yesterday"},
        {"q": "x", "to": "2026-13-01"},
        {"q": "x", "from": "2026-02-01", "to": "2026-01-01"},
        {"q": "x", "tag": "   "},
        {"q": "x", "tag": "t" * 51},
        {"q": "x", "item_id": 0},
        {"q": "x" * 1001},
    ],
)
def test_bad_input_is_422(client: TestClient, params: dict) -> None:
    assert client.get("/search", params=params).status_code == 422


def test_a_hit_says_where_it_is_and_how_it_was_found(fake_client: TestClient, conn: Conn) -> None:
    created = fake_client.post(
        "/items", json={"type": "note", "title": "Queues", "body": "Leases notice dead workers."}
    ).json()
    add_chunk(conn, created["id"], "Leases notice dead workers.")
    body = fake_client.get("/search", params={"q": "Leases notice dead workers."}).json()
    assert body["mode"] == "hybrid" and body["degraded"] is False and body["notice"] is None
    hit = body["hits"][0]
    assert set(hit) == {
        "chunk_id", "chunk_index", "page", "start_char", "end_char", "text", "item",
        "score", "keyword_rank", "keyword_score", "vector_rank", "vector_distance",
    }  # fmt: skip
    assert hit["item"] == {
        "id": created["id"],
        "type": "note",
        "title": "Queues",
        "url": None,
        "filename": None,
        "created_at": created["created_at"],
    }
    assert (hit["chunk_index"], hit["start_char"], hit["end_char"]) == (0, 0, 27)
    assert hit["keyword_rank"] == 1 and hit["vector_rank"] == 1
    assert hit["score"] == pytest.approx(2 / 61)


# ---------- C6: only current, live data ----------


@pytest.fixture
def worker(conn: Conn, storage: LocalStorage) -> Iterator[Worker]:
    """The real worker with the fake embedder. Notes never fetch, so the site refuses."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"notes never fetch, but {request.url} was asked for")

    with make_client(httpx2.MockTransport(refuse)) as http:
        yield Worker(
            conn=conn, storage=storage, http=http, clock=FakeClock(), embedder=FakeEmbedder()
        )


def run_all(worker: Worker) -> None:
    while worker.run_once() is not None:
        pass


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_sees_only_the_current_text_after_a_change(
    conn: Conn, worker: Worker, mode: str
) -> None:
    old = "The registry rejected batch alpha for missing serials."
    new = "The registry accepted batch omega after the audit."
    item = save_note(conn, old)
    run_all(worker)
    assert any(old == h.text for h in search(conn, old, mode, embedder=FakeEmbedder()).hits)

    conn.execute("UPDATE items SET body = %s WHERE id = %s", (new, item))
    jobs.enqueue(conn, item)
    run_all(worker)

    for query in (old, "alpha", new, "omega"):
        hits = search(conn, query, mode, embedder=FakeEmbedder()).hits
        assert all("alpha" not in h.text for h in hits)
    found = search(conn, new, mode, embedder=FakeEmbedder()).hits
    assert found[0].text == new and found[0].item_id == item


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_a_deleted_item_never_appears(
    conn: Conn, worker: Worker, client: TestClient, mode: str
) -> None:
    gone = save_note(conn, "The registry lists project gamma.")
    kept = save_note(conn, "The registry lists project delta.")
    run_all(worker)
    assert client.delete(f"/items/{gone}").status_code == 204
    for query in ("registry", "gamma", "The registry lists project gamma."):
        hits = search(conn, query, mode, embedder=FakeEmbedder()).hits
        assert {h.item_id for h in hits} <= {kept}
    assert (
        conn.execute("SELECT count(*) AS n FROM chunks WHERE item_id = %s", (gone,)).fetchone()["n"]
        == 0
    )


# ---------- C7: the indexes are used ----------


@pytest.fixture
def many_chunks(conn: Conn) -> None:
    """500 chunks with random vectors, then fresh statistics for the planner."""
    item = add_item(conn)
    conn.execute(
        """
        INSERT INTO chunks (item_id, chunk_index, start_char, end_char, text, source_sha256,
                            embedding, embedding_model)
        SELECT %s, g, 0, 20, 'chunk ' || g || ' about word' || (g %% 100), repeat('a', 64),
               ARRAY(SELECT random() + g * 0 FROM generate_series(1, 1024))::vector,
               %s
        FROM generate_series(0, 499) g
        """,
        (item, MODEL),
    )
    conn.execute("ANALYZE chunks")
    conn.execute("ANALYZE items")


def plan(conn: Conn, sql: str, params: dict) -> str:
    rows = conn.execute("EXPLAIN " + sql, params).fetchall()
    return "\n".join(r["QUERY PLAN"] for r in rows)


def test_the_keyword_query_uses_the_gin_index(conn: Conn, many_chunks: None) -> None:
    # Pinned: with seq scans off, the planner reports an index only if it CAN use one.
    conn.execute("SET enable_seqscan = off")
    text = plan(conn, KEYWORD_SQL, keyword_params("word7", Filters(), 50))
    assert "chunks_text_tsv_idx" in text, text


@pytest.mark.parametrize("query", ["word7 word8 nowhere", '"word7 word8"', "word7 -word8"])
def test_both_keyword_tiers_use_the_gin_index(conn: Conn, many_chunks: None, query: str) -> None:
    conn.execute("SET enable_seqscan = off")
    text = plan(conn, KEYWORD_SQL, keyword_params(query, Filters(type="note"), 50))
    assert "chunks_text_tsv_idx" in text, text


def test_the_unfiltered_vector_query_uses_the_hnsw_index(conn: Conn, many_chunks: None) -> None:
    conn.execute("SET enable_seqscan = off")
    params = vector_params(fake_vector("q", MODEL), MODEL, Filters(), 50)
    text = plan(conn, VECTOR_ANN_SQL, params)
    assert "chunks_embedding_hnsw_idx" in text, text


def test_the_filtered_vector_query_scans_exactly(conn: Conn, many_chunks: None) -> None:
    """Under a filter the HNSW index must NOT be used (C4): the exact path measures the
    filtered rows."""
    conn.execute("SET enable_seqscan = off")
    params = vector_params(fake_vector("q", MODEL), MODEL, Filters(item_id=1), 50)
    text = plan(conn, VECTOR_EXACT_SQL, params)
    assert "hnsw" not in text, text
