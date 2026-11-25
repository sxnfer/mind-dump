# M3: Queue and workers

> Read this when: you are working on M3, or need its tasks and done-when list.

**Goal:** saving stays fast; a worker reads, chunks and embeds each item later.
**Decisions needed:** D6, D8. (Approved: D5 storage, D7 web extraction, D28 raw landing, D30 idempotent jobs, D32 page history, D33 fetch client. P1 builds the fetch, raw landing and versions part early, before 26 Nov.)
**Status:** P3 built the queue, the worker and chunking on branch `p3-jobs` (✅ below). Still open: embeddings (wait for D8), PDF text for files, and Gate 1 (search, M4).

**Tasks:**
- ✅ `jobs` table: id, item_id, kind, status (queued, running, done, failed, dead), attempts, error, timestamps. *(P3: migration 007, plus `run_after` and the lease `locked_until`; one unfinished job per item.)*
- ✅ Workers claim a job safely so two workers never take the same one (`SELECT ... FOR UPDATE SKIP LOCKED`). *(P3: `jobs.py`, `worker.py`, Compose service `worker`.)*
- ✅ Retry with a growing wait; after 3 failures the job is `dead` and the item shows an error. *(P3, D35: 1 then 5 minutes; `status: error` with the reason in `GET /items`.)*
- Steps: fetch the page or open the file, extract text, chunk it, embed each chunk, save chunks with item id, page and character position. *(P3: links and notes are fetched, extracted and chunked with item id and character positions (`chunking.py`, migration 008). Not yet: files (PDF text, stage 2), embeddings (D8).)*
- Raw landing for links (D28): land each fetch's body, gzipped and append-only, through `Storage` before extracting, and record it in a `fetches` table (`item_id`, `fetched_at`, `requested_url`, `final_url`, `http_status`, `content_type`, `byte_size`, `sha256`, `storage_key`). Skip storing a body whose SHA-256 equals the item's latest file. Extract only from the landed file, and only for 2xx responses of a type we can parse.
- ✅ Skip re-chunking and re-embedding when the SHA-256 of the extracted text equals the text the current chunks came from. *(P3: `chunks.source_sha256`; re-embedding arrives with D8.)*
- ✅ Idempotent: running a job twice gives the same chunks, never duplicates. *(P3, D30.)*
- The model API key comes from `.env`, never from code.
**Concepts:** queues, idempotency, retries, failure handling, provenance (where each chunk came from), raw landing, append-only storage, content hashes.
**Done when:** ✅ you kill a worker mid-job, restart it, and the item ends up processed once (a test proves it). *(`test_worker_process.py::test_a_worker_killed_before_commit_loses_nothing_and_the_job_completes_once`: a separate OS process, `os._exit` before COMMIT.)*
**Done when (D28), each proved by a test:**
- ✅ Re-process from raw: fetch a link from a fake server, stop the server, re-process; the chunks are rebuilt from the landed file with no network call. *(`test_worker.py::test_reprocess_from_raw_rebuilds_the_same_chunks_with_no_network`)*
- ✅ Append-only: a re-fetch that returns 404 lands a new file and a `fetches` row with status 404; the earlier good file is unchanged and the chunks stay. *(`test_a_refetch_that_gets_404_keeps_the_good_file_and_the_chunks`)*
- ✅ Raw hash: a re-fetch with identical bytes adds a `fetches` row and no new file. *(`test_a_refetch_with_identical_bytes_stores_no_new_file`)*
- ✅ (chunks) Text hash: a re-fetch where only an ad slot changed lands a new file but does not re-chunk or call the embedding API. *(`test_a_refetch_where_only_furniture_changed_keeps_the_chunk_rows`; the embedding half waits for D8.)*
**Done when (D30), each proved by a test:**
- ✅ Crash before COMMIT: a worker that fails after deleting and inserting chunks, but before COMMIT, leaves the item's old chunks exactly as they were, and the job is not done. *(`test_worker.py::test_a_crash_before_commit_leaves_the_old_chunks_and_the_job_not_done`)*
- ✅ Retry gives the same chunks: running the same job twice gives the same chunk set (same count, indexes and text), with no duplicates. *(`test_running_the_same_job_twice_gives_the_same_chunks`)*
- ✅ Fewer chunks leave no orphans: re-processing an item that now makes 3 chunks instead of 5 leaves exactly 3, with nothing left at indexes 3 and 4. *(`test_fewer_chunks_leave_no_orphans`)*
**Gate 1:** a saved link is searchable on your Mac.
