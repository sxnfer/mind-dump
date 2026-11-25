# D37: Hybrid search

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; San to review)
Problem: the owner must find any saved passage, by the exact words in it (a project ID, an error code) or by what it means (a question in other words), and narrow the search by type, date, tag or item (M4; Discuss in M5 searches one item).
Without a tool: one kind of search only. Meaning alone misses exact words such as `DQ-117` or `MD-2291`; words alone miss "woodland catches fire" when the passage says "the forest burns".
Options: for words, Postgres full-text search or a separate search engine; for meaning, pgvector or a separate vector database; to merge the two lists, a weighted sum of normalised scores, or Reciprocal Rank Fusion (RRF); or only one of the two lists.
Chosen: both lists inside Postgres, merged with RRF.
- Keywords: Postgres full-text search. A stored generated `tsvector` column on `chunks` (`text_tsv`, config `english`), a GIN index, queries parsed with `websearch_to_tsquery`. The rule: chunks with all the words first, then chunks with any of them, ranked by `ts_rank`; quotes (a phrase) or a `-word` (exclude) switch to exact matching, all words only. (Changed the same day, after the first eval: the first version required every word and ranked with `ts_rank_cd`; keyword recall@5 went from 0.40 to 0.80.)
- Meaning: pgvector cosine distance (`<=>`) on `chunks.embedding`, with an HNSW index (`vector_cosine_ops`). The query is embedded with `input_type` `query` (D8).
- Merge: Reciprocal Rank Fusion. Each list gives a chunk `1 / (60 + rank)`; the sums decide the order. A chunk missing from a list gets nothing from it; ties go to the better best rank, then the lower chunk id.
- Filters (item `type`, a date range on the item's `created_at`, `tag`, `item_id`) are WHERE clauses applied to both lists before their LIMIT, so a filter never empties a result that exists. Under a filter the vector list is an exact scan of the filtered rows, not the HNSW index (see Cost).
- `GET /search` reports the mode it really used. With no embedding model, or a failing one, hybrid falls back to keyword results and says so; `mode=vector` answers 503 with the reason.
Why:
- All words first, then any word: people type sentences, and one absent word ("many", "goes") emptied the whole result. A list that returns nothing also gives the hybrid merge nothing. `ts_rank`, not `ts_rank_cd`: cover density rewards query words that stand next to each other, which an any-word match rarely has.
- RRF: the two scores are on different scales (a text rank and a distance), and RRF needs only the positions, so there is nothing to normalise or tune, and San can work an example by hand.
- Rejected: a weighted sum of normalised scores (it needs a weight and a normalising rule that both need tuning on data we do not have yet); meaning only (misses exact words such as a project ID or an error code); keywords only (misses paraphrases).
- Inside Postgres: no new service, filters are ordinary WHERE clauses on the same rows, and the indexes share the data's backups (same reasoning as D3 and D6).
Cost:
- `english` stemming is wrong for other languages.
- RRF ignores how far apart two scores are: a near-perfect match and a barely-better one differ by one rank.
- HNSW is approximate, and it filters only after it finds neighbours, so a strict filter could return fewer rows than asked. Handled: with any filter, search measures the filtered rows exactly (a `MATERIALIZED` CTE the index can't serve); without one, it uses HNSW with `hnsw.ef_search` raised to the list depth and pgvector 0.8's `hnsw.iterative_scan = strict_order`. The exact scan costs time in proportion to the filtered rows: milliseconds for one owner's library.
- `ts_rank` has no notion of rare versus common words, so on a large library a common word counts as much as `MD-2291`. The any-word list is noisier, and now feeds up to 50 rows into the merge.
We will switch if: the eval shows one list consistently hurting the merged result (then weight the lists); in particular, if the hybrid eval with a real embedding model shows the keyword list hurting the merge, weight rare words (an IDF from `ts_stat` or a small table) or cut the list's depth. Or if the library grows past what one Postgres handles comfortably.
