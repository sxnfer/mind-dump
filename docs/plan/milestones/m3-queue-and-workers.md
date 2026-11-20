# M3: Queue and workers

> Read this when: you are working on M3, or need its tasks and done-when list.

**Goal:** saving stays fast; a worker reads, chunks and embeds each item later.
**Decisions needed:** D6, D7, D8.
**Tasks:**
- `jobs` table: id, item_id, kind, status (queued, running, done, failed, dead), attempts, error, timestamps.
- Workers claim a job safely so two workers never take the same one (`SELECT ... FOR UPDATE SKIP LOCKED`).
- Retry with a growing wait; after 3 failures the job is `dead` and the item shows an error.
- Steps: fetch the page or open the file, extract text, chunk it, embed each chunk, save chunks with item id, page and character position.
- Raw landing for links (D28): land each fetch's body, gzipped and append-only, through `Storage` before extracting, and record it in a `fetches` table (`item_id`, `fetched_at`, `requested_url`, `final_url`, `http_status`, `content_type`, `byte_size`, `sha256`, `storage_key`). Skip storing a body whose SHA-256 equals the item's latest file. Extract only from the landed file, and only for 2xx responses of a type we can parse.
- Skip re-chunking and re-embedding when the SHA-256 of the extracted text equals the text the current chunks came from.
- Idempotent: running a job twice gives the same chunks, never duplicates.
- The model API key comes from `.env`, never from code.
**Concepts:** queues, idempotency, retries, failure handling, provenance (where each chunk came from), raw landing, append-only storage, content hashes.
**Done when:** you kill a worker mid-job, restart it, and the item ends up processed once (a test proves it).
**Done when (D28), each proved by a test:**
- Re-process from raw: fetch a link from a fake server, stop the server, re-process; the chunks are rebuilt from the landed file with no network call.
- Append-only: a re-fetch that returns 404 lands a new file and a `fetches` row with status 404; the earlier good file is unchanged and the chunks stay.
- Raw hash: a re-fetch with identical bytes adds a `fetches` row and no new file.
- Text hash: a re-fetch where only an ad slot changed lands a new file but does not re-chunk or call the embedding API.
**Gate 1:** a saved link is searchable on your Mac.
