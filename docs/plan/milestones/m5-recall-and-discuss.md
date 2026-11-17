# M5: Recall and discuss

> Read this when: you are working on M5, or need its tasks and done-when list.

**Goal:** answers with citations, and a chat about one citation.
**Decisions needed:** D9, D21.
**Tasks:**
- A hand-written agent loop: the model can call `search` and `get_item` tools.
- Every claim cites item, page and passage; the app can open the source at that spot.
- "Discuss": a chat whose search adds `WHERE item_id = <cited item>`, and also fetches the chunks next to the cited one for context.
- `conversations` and `messages` tables.
- A grounding check: if the items do not hold the answer, say so.
- A daily cost cap and a per-minute rate limit (as in RAG Galaxy).
**Concepts:** tool use, scoped retrieval, conversation state, guardrails, cost control.
**Done when:** an eval of 10 questions checks that each cited passage really holds the claim.
