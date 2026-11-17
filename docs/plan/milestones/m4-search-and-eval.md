# M4: Search and eval

> Read this when: you are working on M4, or need its tasks and done-when list.

**Goal:** find any item by meaning or by exact words, and measure how good the search is.
**Decisions needed:** D20.
**Tasks:**
- Keyword search (Postgres full-text) and vector search (pgvector with an HNSW index).
- Merge the two result lists into one (hybrid search).
- Filters: type, date range, tag, and `item_id` (for Discuss in M5).
- A golden set of 20 questions with the item and passage each one should find.
- An eval script that prints recall@5 and saves it to `docs/results.md`.
**Concepts:** indexes, hybrid search, metadata filters, retrieval evaluation.
**Done when:** the eval runs in CI and the number is recorded.
