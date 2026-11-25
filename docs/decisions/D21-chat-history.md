# D21: Chat history

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; reviewed and approved by San the same day)
Problem: a follow-up ("explain that simply") and a chat about one citation (Discuss) need the earlier turns. The Messages API is stateless: every request must carry the whole conversation.
Without a tool: every question starts from nothing. "What about the second one?" has no meaning.
Options: `conversations` and `messages` tables in Postgres; history kept only in the app, sent with each request.
Chosen: `conversations` and `messages` tables in Postgres (migration 011).
- Each stored turn keeps the exact content blocks we sent and the API returned (as JSON), plus the display text and the citations.
- The next request replays those blocks unchanged, in order, and only appends.
Why:
- Same database, same backups, and it can be queried.
- The exact blocks let the next request replay history unchanged and append-only, which current Claude models require for their thinking blocks (a block is valid only after the same earlier messages).
- A chat survives an app restart.
- Rejected: history only in the app (lost on restart, and the backend could not check what was cited).
Cost: the stored JSON is tied to the API's block format. Long chats grow the prompt, and every turn pays for it (prompt caching makes the repeated part cheaper).
We will switch if: chats get long enough to need summarising (then server-side compaction).
