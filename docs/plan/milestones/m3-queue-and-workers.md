# M3: Queue and workers

> Read this when: you are working on M3, or need its tasks and done-when list.

**Goal:** saving stays fast; a worker reads, chunks and embeds each item later.
**Decisions needed:** D6, D7, D8.
**Tasks:**
- `jobs` table: id, item_id, kind, status (queued, running, done, failed, dead), attempts, error, timestamps.
- Workers claim a job safely so two workers never take the same one (`SELECT ... FOR UPDATE SKIP LOCKED`).
- Retry with a growing wait; after 3 failures the job is `dead` and the item shows an error.
- Steps: fetch the page or open the file, extract text, chunk it, embed each chunk, save chunks with item id, page and character position.
- Idempotent: running a job twice gives the same chunks, never duplicates.
- The model API key comes from `.env`, never from code.
**Concepts:** queues, idempotency, retries, failure handling, provenance (where each chunk came from).
**Done when:** you kill a worker mid-job, restart it, and the item ends up processed once (a test proves it).
**Gate 1:** a saved link is searchable on your Mac.
