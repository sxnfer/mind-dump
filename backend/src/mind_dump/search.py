"""Hybrid search (D37): find chunks by exact words, by meaning, or both, with filters.

Two lists, one merge:

    keyword_search   Postgres full-text search. The chunk's stored `text_tsv` (stemmed
                     'english' words, migration 010) is matched against the query parsed
                     by websearch_to_tsquery: chunks with all the words first, then
                     chunks with any of them, each tier ranked by ts_rank. Quotes (a
                     phrase) or a -word (exclude) switch to exact matching: all words
                     only. GIN index.
    vector_search    pgvector: the cosine distance (<=>) between the query's vector and
                     each chunk's. HNSW index when there is no filter; an exact scan of
                     the filtered rows when there is one (see vector_search).
    rrf              Reciprocal Rank Fusion: a chunk gets 1 / (60 + rank) from each list
                     it is in, and the sums decide the order. Only positions count, so a
                     text rank and a distance never have to be put on one scale.

Filters (type, created_at range, tag, item_id) are WHERE clauses applied to BOTH lists
before their LIMIT, so a filter narrows the results but never empties a result that
exists.

`search()` puts it together and decides what to do when the embedding model is missing
or failing: hybrid falls back to keyword results and says so; vector mode raises
SearchUnavailable, because an empty answer would look like "nothing matches".

Plain SQL with parameters, as in items.py. The filter clause is a constant joined into
the statements, never a value.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Literal

from psycopg import Connection
from psycopg.rows import DictRow

from mind_dump.embeddings import Embedder, Vector, to_pgvector
from mind_dump.errors import PermanentError, TemporaryError

Conn = Connection[DictRow]
Mode = Literal["hybrid", "keyword", "vector"]

RRF_K = 60  # D37: the usual constant. A bigger k flattens the gap between rank 1 and 10
CANDIDATES = 50  # rows each list hands to the merge; at least `limit`
MAX_LIMIT = 50
DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class Filters:
    """Narrow a search. None means "any". `created_from` and `created_to` are inclusive."""

    type: str | None = None  # note, link or file
    created_from: datetime | None = None
    created_to: datetime | None = None
    tag: str | None = None  # already normalised (tags.normalise_tag)
    item_id: int | None = None

    def active(self) -> bool:
        return any(
            v is not None
            for v in (self.type, self.created_from, self.created_to, self.tag, self.item_id)
        )

    def params(self) -> dict:
        return {
            "type": self.type,
            "created_from": self.created_from,
            "created_to": self.created_to,
            "tag": self.tag,
            "item_id": self.item_id,
        }


# Every filter is "parameter is NULL, or the row matches". Postgres plans each search with
# the real values, so an unused filter costs nothing. `c` is chunks, `i` is items.
_FILTERS = """
    AND (%(type)s::text IS NULL OR i.type = %(type)s::text)
    AND (%(created_from)s::timestamptz IS NULL OR i.created_at >= %(created_from)s::timestamptz)
    AND (%(created_to)s::timestamptz IS NULL OR i.created_at <= %(created_to)s::timestamptz)
    AND (%(item_id)s::bigint IS NULL OR c.item_id = %(item_id)s::bigint)
    AND (%(tag)s::text IS NULL OR EXISTS (
        SELECT 1 FROM item_tags it JOIN tags t ON t.id = it.tag_id
        WHERE it.item_id = c.item_id AND t.name = %(tag)s::text))
"""

# Words, in two tiers (D37). People type sentences, and one word missing from the passage
# ("how MANY credits...") must not empty the list, so:
#   1. chunks with ALL the query's words come first (websearch_to_tsquery as written);
#   2. then chunks with ANY of them: the same tsquery with every AND (' & ') turned into
#      OR (' | '). Only ' & ' with its spaces is replaced: a lexeme from a URL can hold a
#      bare '&'. Phrases ('a' <-> 'b') and NOT (!'c') are left as they are.
# Within a tier, ts_rank (word frequency) orders; not ts_rank_cd, whose cover density
# rewards query words standing next to each other, which an any-word match rarely has.
# A query with quotes or a -word is strict (decided in Python, is_strict): tier 1 only,
# exactly websearch_to_tsquery. The filter sits in the same WHERE as the match, so ranking
# and LIMIT only see rows that pass it; ties go to the older chunk, so the order is the
# same every run. A query of only stop words or punctuation parses to an empty tsquery,
# which matches nothing: an empty list, not an error. (Postgres sends a NOTICE for it;
# psycopg drops notices when no handler is registered, so nothing reaches the logs.)
# Everything stays one inline expression per use, so the planner can use the GIN index.
KEYWORD_SQL = (
    """
SELECT c.id,
       ts_rank(c.text_tsv,
               replace(websearch_to_tsquery('english', %(q)s)::text, ' & ', ' | ')::tsquery)
           AS score
FROM chunks c JOIN items i ON i.id = c.item_id
WHERE c.text_tsv @@ (CASE WHEN %(strict)s::boolean
                          THEN websearch_to_tsquery('english', %(q)s)
                          ELSE replace(websearch_to_tsquery('english', %(q)s)::text,
                                       ' & ', ' | ')::tsquery
                     END)
"""
    + _FILTERS
    + """
ORDER BY (c.text_tsv @@ websearch_to_tsquery('english', %(q)s)) DESC, score DESC, c.id
LIMIT %(n)s
"""
)

# Meaning, no filter: the HNSW index walks to the nearest vectors. Only vectors from the
# query's model are comparable (D8), and NULL vectors are not in the index at all.
VECTOR_ANN_SQL = """
SELECT c.id, c.embedding <=> %(v)s::vector AS score
FROM chunks c
WHERE c.embedding IS NOT NULL AND c.embedding_model = %(model)s
ORDER BY c.embedding <=> %(v)s::vector
LIMIT %(n)s
"""

# Meaning, with a filter: first the rows that pass it, then the exact distance to each,
# then the order. MATERIALIZED makes "filter first" a fact of the SQL, not a planner
# choice: the HNSW index can't serve an ORDER BY over the CTE's rows, so it can never
# return 40 neighbours from other items and then filter them all away.
_VECTOR_EXACT_HEAD = """
WITH candidates AS MATERIALIZED (
    SELECT c.id, c.embedding
    FROM chunks c JOIN items i ON i.id = c.item_id
    WHERE c.embedding IS NOT NULL AND c.embedding_model = %(model)s
"""
_VECTOR_EXACT_TAIL = """
)
SELECT id, embedding <=> %(v)s::vector AS score
FROM candidates
ORDER BY score, id
LIMIT %(n)s
"""
VECTOR_EXACT_SQL = _VECTOR_EXACT_HEAD + _FILTERS + _VECTOR_EXACT_TAIL  # noqa: S608 (constants)

_HITS_SQL = """
SELECT c.id AS chunk_id, c.chunk_index, c.page, c.start_char, c.end_char, c.text,
       i.id AS item_id, i.type AS item_type, i.title, i.url, i.filename,
       i.created_at AS item_created_at
FROM chunks c JOIN items i ON i.id = c.item_id
WHERE c.id = ANY(%s)
"""


@dataclass(frozen=True)
class Ranked:
    """One entry of one list: rank 1 is the best. score is ts_rank (keyword, higher is
    better within a tier) or the cosine distance (vector, lower is better), kept to
    explain a result."""

    chunk_id: int
    rank: int
    score: float


# A double quote, or a word with a leading minus ("-fire", not "MD-2291"): the owner
# asked for exact matching, so the any-word tier is off.
_STRICT = re.compile(r'"|(?:^|\s)-\S')


def is_strict(query: str) -> bool:
    return _STRICT.search(query) is not None


def keyword_params(query: str, filters: Filters, n: int) -> dict:
    return {"q": query, "strict": is_strict(query), "n": n, **filters.params()}


def vector_params(vector: Vector, model: str, filters: Filters, n: int) -> dict:
    return {"v": to_pgvector(vector), "model": model, "n": n, **filters.params()}


def _ranked(rows: list[DictRow]) -> list[Ranked]:
    return [Ranked(r["id"], rank, float(r["score"])) for rank, r in enumerate(rows, 1)]


def keyword_search(conn: Conn, query: str, filters: Filters, n: int) -> list[Ranked]:
    return _ranked(conn.execute(KEYWORD_SQL, keyword_params(query, filters, n)).fetchall())


def vector_search(conn: Conn, vector: Vector, model: str, filters: Filters, n: int) -> list[Ranked]:
    """The n chunks nearest to `vector`, among those that pass `filters`.

    No filter: the HNSW index. Two settings, for this transaction only:
    - hnsw.ef_search: how many candidates the walk keeps. The default (40) would cap the
      answer at 40 rows, so it is raised to at least n.
    - hnsw.iterative_scan = strict_order (pgvector 0.8): if rows are dropped after the
      walk (here only vectors from an older model), the walk continues instead of
      returning fewer than n. strict_order keeps the results in exact distance order.
    With a filter: VECTOR_EXACT_SQL, an exact scan of the rows that pass. Exact means it
    can't miss: an item's 12 chunks are found even when 10,000 chunks of other items lie
    closer. The cost is measuring every filtered row; for one owner's library that is
    milliseconds (D37's cost line).
    """
    params = vector_params(vector, model, filters, n)
    if filters.active():
        return _ranked(conn.execute(VECTOR_EXACT_SQL, params).fetchall())
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(max(40, n)),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
        return _ranked(conn.execute(VECTOR_ANN_SQL, params).fetchall())


@dataclass(frozen=True)
class Fused:
    """One chunk after the merge. score is exact (a Fraction), so equal sums are equal."""

    chunk_id: int
    score: Fraction
    ranks: dict[str, int]  # list name -> rank in it; a list it is missing from is absent


def rrf(lists: Mapping[str, Sequence[int]], k: int = RRF_K) -> list[Fused]:
    """Reciprocal Rank Fusion over lists of chunk ids, best first.

    A chunk gets 1 / (k + rank) from every list it is in; missing from a list gives 0.
    Order: higher score first; on a tie, the better best rank; then the lower chunk id.
    So the same lists always give the same order.

    Worked example (k = 60): keyword [7, 3], vector [3, 9]
        3: 1/62 + 1/61 = 0.03252   (in both lists)
        7: 1/61        = 0.01639
        9: 1/62        = 0.01613
    -> [3, 7, 9]
    """
    scores: dict[int, Fraction] = {}
    ranks: dict[int, dict[str, int]] = {}
    for name, ids in lists.items():
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, Fraction(0)) + Fraction(1, k + rank)
            ranks.setdefault(chunk_id, {})[name] = rank
    order = sorted(scores, key=lambda c: (-scores[c], min(ranks[c].values()), c))
    return [Fused(c, scores[c], ranks[c]) for c in order]


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    chunk_index: int
    page: int | None
    start_char: int
    end_char: int
    text: str
    item_id: int
    item_type: str
    title: str | None
    url: str | None
    filename: str | None
    item_created_at: datetime
    score: float  # the RRF score over the lists this search used
    keyword_rank: int | None = None
    keyword_score: float | None = None  # ts_rank against the any-word query
    vector_rank: int | None = None
    vector_distance: float | None = None  # cosine distance: 0 is the same direction


@dataclass(frozen=True)
class SearchResult:
    query: str
    requested_mode: Mode
    mode: Mode  # what really ran: hybrid can fall back to keyword
    degraded: bool  # True when the requested mode could not run in full
    notice: str | None  # why, in words for the owner
    hits: list[Hit] = field(default_factory=list)


class SearchUnavailable(Exception):
    """Vector mode can't run: no embedding model is configured, or it failed."""


NO_EMBEDDER = "Search by meaning needs an embedding model, and none is configured"


def _hits(conn: Conn, fused: list[Fused], keyword: list[Ranked], vector: list[Ranked]) -> list[Hit]:
    if not fused:
        return []
    rows = {
        r["chunk_id"]: r for r in conn.execute(_HITS_SQL, ([f.chunk_id for f in fused],)).fetchall()
    }
    kw = {r.chunk_id: r for r in keyword}
    vec = {r.chunk_id: r for r in vector}
    hits = []
    for f in fused:
        row = rows.get(f.chunk_id)
        if row is None:  # replaced or deleted between the ranking and this read
            continue
        k, v = kw.get(f.chunk_id), vec.get(f.chunk_id)
        hits.append(
            Hit(
                **row,
                score=float(f.score),
                keyword_rank=k.rank if k else None,
                keyword_score=k.score if k else None,
                vector_rank=v.rank if v else None,
                vector_distance=v.score if v else None,
            )
        )
    return hits


def search(
    conn: Conn,
    query: str,
    mode: Mode = "hybrid",
    filters: Filters | None = None,
    limit: int = DEFAULT_LIMIT,
    embedder: Embedder | None = None,
    no_embedder: str | None = None,
) -> SearchResult:
    """Run one search. Raises SearchUnavailable only in vector mode; hybrid degrades.
    no_embedder says why there is no embedder, naming the key to set
    (embeddings.no_embedder_reason), for the notice and the error.

    The query is embedded first (a network call, D8), before any SQL runs, so a slow
    model never holds a transaction open.
    """
    filters = filters or Filters()
    n = max(CANDIDATES, limit)
    used: Mode = mode
    notice: str | None = None
    query_vector: Vector | None = None

    if mode in ("vector", "hybrid"):
        try:
            if embedder is None:
                reason = f" ({no_embedder})" if no_embedder else ""
                raise SearchUnavailable(NO_EMBEDDER + reason)
            try:
                query_vector = embedder.embed_query(query)
            except (TemporaryError, PermanentError) as exc:
                raise SearchUnavailable(f"The embedding model failed: {exc}") from exc
        except SearchUnavailable as exc:
            if mode == "vector":
                raise
            used, notice = "keyword", f"{exc}. Showing keyword results only."

    keyword = keyword_search(conn, query, filters, n) if used in ("keyword", "hybrid") else []
    vector: list[Ranked] = []
    if query_vector is not None and embedder is not None:
        vector = vector_search(conn, query_vector, embedder.model, filters, n)

    lists: dict[str, list[int]] = {}
    if used in ("keyword", "hybrid"):
        lists["keyword"] = [r.chunk_id for r in keyword]
    if used in ("vector", "hybrid"):
        lists["vector"] = [r.chunk_id for r in vector]
    fused = rrf(lists)[:limit]
    return SearchResult(
        query=query,
        requested_mode=mode,
        mode=used,
        degraded=used != mode,
        notice=notice,
        hits=_hits(conn, fused, keyword, vector),
    )
