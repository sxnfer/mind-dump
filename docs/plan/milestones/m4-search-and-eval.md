# M4: Search and eval

> Read this when: you are working on M4, or need its tasks and done-when list.

**Goal:** find any item by meaning or by exact words, and measure how good the search is.
**Decisions needed:** none left. (Approved: D37 hybrid search, D20 reranking: none for now.)
**Status:** built on branch `m4-search`. Keyword numbers recorded in `docs/results.md`; the vector and hybrid numbers wait for a run with `VOYAGE_API_KEY`.
**Tasks:**
- ✅ Keyword search (Postgres full-text) and vector search (pgvector with an HNSW index). *(Migration 010: generated `chunks.text_tsv` + GIN index, HNSW index on `chunks.embedding`. `search.py`: `websearch_to_tsquery` + `ts_rank_cd`; cosine distance with the query embedded as `query` (D8).)*
- ✅ Merge the two result lists into one (hybrid search). *(Reciprocal Rank Fusion, `1 / (60 + rank)`, D37. Each hit shows its rank in each list and the merged score.)*
- ✅ Filters: type, date range, tag, and `item_id` (for Discuss in M5). *(WHERE clauses on both lists before their LIMIT; an exact vector scan under any filter, so a filter never loses a match. Tags got a minimal API: `PUT /items/{id}/tags`, and items show their tags.)*
- ✅ A golden set of 20 questions with the item and passage each one should find. *(`backend/eval/golden.json` over 12 original documents in `backend/eval/corpus/`: 6 that share words, 9 paraphrases, 5 with an exact code or number.)*
- ✅ An eval script that prints recall@5 and saves it to `docs/results.md`. *(`python -m mind_dump.eval`: loads the corpus through the real pipeline; recall@5 and MRR@10 per mode, with every miss.)*
**Concepts:** indexes, hybrid search, metadata filters, retrieval evaluation.
**Done when:** ✅ the eval runs in CI and the number is recorded. *(CI step "Search eval (keyword recall@5 floor)"; `docs/results.md`.)*
**Done when (stage 3 acceptance), each proved by a test:**
- ✅ Keyword: one-chunk word first, stemming, phrases and `-`, stop words give `[]`. *(`test_search.py`, C1 tests)*
- ✅ Vector: the chunk equal to the query first; NULL vectors never appear. *(`test_search.py`, C2 tests)*
- ✅ RRF by hand, ties deterministic; a chunk in both lists beats chunks high in one. *(`test_rrf_by_hand`, `test_a_chunk_in_both_lists_beats_chunks_ranked_high_in_only_one`)*
- ✅ Filters in all three modes; a selective filter never loses results under 250 closer chunks. *(`test_filters_narrow_results_in_every_mode`, `test_a_selective_filter_never_loses_results_that_exist`)*
- ✅ No key: hybrid falls back to keyword and says so; vector answers 503; a failing model degrades hybrid. *(C5 tests)*
- ✅ Only current, live data after a change or a delete. *(`test_search_sees_only_the_current_text_after_a_change`, `test_a_deleted_item_never_appears`)*
- ✅ `EXPLAIN` shows the GIN and HNSW indexes. *(`test_the_keyword_query_uses_the_gin_index`, `test_the_unfiltered_vector_query_uses_the_hnsw_index`)*
- ✅ Tags: set, replace, clear, filter, cascade on delete. *(`test_tags.py`)*
- ✅ The eval end to end on the committed corpus; every golden passage sits in one chunk. *(`test_eval.py`)*
