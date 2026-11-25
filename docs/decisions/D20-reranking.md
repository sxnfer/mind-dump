# D20: Reranking

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; reviewed and approved by San the same day)
Problem: the first search (D37) can return passages that are close, but not the best one first. An answer (M5) built on the top passages is only as good as their order.
Without a tool: the order is whatever hybrid search (RRF) gives.
Options: none; a cross-encoder reranking model that reads the query and each passage together; ask the LLM to rank the passages.
Chosen: none for now.
Why: a reranker is a second model call per search (cost and delay) and a new dependency or API. The eval (`python -m mind_dump.eval`, `docs/results.md`) exists precisely to show whether it would pay.
Cost: when the right passage is in the top 5 but not first, the answer may lean on a weaker one.
We will switch if: recall@5 is high but the right passage is often not in the top 1 or 2 (a low MRR), which is the gap a reranker closes.
