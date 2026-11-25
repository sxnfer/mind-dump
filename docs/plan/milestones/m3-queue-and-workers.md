# M3: Queue and workers

> Read this when: you are working on M3, or need its tasks and done-when list.

**Goal:** saving stays fast; a worker reads, chunks and embeds each item later.
**Decisions needed:** none left. (Approved: D5 storage, D6 jobs, D7 web extraction, D7b PDFs and images, D8 models, D28 raw landing, D30 idempotent jobs, D32 page history, D33 fetch client, D35 retries, D36 chunking.)
**Status:** built. P3 built the queue, the worker and chunking on branch `p3-jobs`; stage 2 (branch `m3-embeddings`) added file text (D7b) and embeddings (D8). Still open: Gate 1 (search, M4).

**Tasks:**
- ✅ `jobs` table: id, item_id, kind, status (queued, running, done, failed, dead), attempts, error, timestamps. *(P3: migration 007, plus `run_after` and the lease `locked_until`; one unfinished job per item.)*
- ✅ Workers claim a job safely so two workers never take the same one (`SELECT ... FOR UPDATE SKIP LOCKED`). *(P3: `jobs.py`, `worker.py`, Compose service `worker`.)*
- ✅ Retry with a growing wait; after 3 failures the job is `dead` and the item shows an error. *(P3, D35: 1 then 5 minutes; `status: error` with the reason in `GET /items`.)*
- ✅ Steps: fetch the page or open the file, extract text, chunk it, embed each chunk, save chunks with item id, page and character position. *(P3: links and notes (`chunking.py`, migration 008). Stage 2: PDFs page by page with pypdf and images through a Claude description (`filetext.py`, `describe.py`, D7b), text kept in `file_texts`; chunks never cross a page and carry `page`; every chunk embedded with Voyage `voyage-4` (`embeddings.py`, D8), vector and model name in `chunks` (migration 009). All model calls happen before the D30 transaction.)*
- ✅ Raw landing for links (D28): land each fetch's body, gzipped and append-only, through `Storage` before extracting, and record it in a `fetches` table (`item_id`, `fetched_at`, `requested_url`, `final_url`, `http_status`, `content_type`, `byte_size`, `sha256`, `storage_key`). Skip storing a body whose SHA-256 equals the item's latest file. Extract only from the landed file, and only for 2xx responses of a type we can parse.
- ✅ Skip re-chunking and re-embedding when the SHA-256 of the extracted text equals the text the current chunks came from. *(P3: `chunks.source_sha256`. Stage 2: no embedding call either, except rows with no vector or another model's, which are embedded in place; `python -m mind_dump.embed_missing` queues those after a key or model change.)*
- ✅ Idempotent: running a job twice gives the same chunks, never duplicates. *(P3, D30.)*
- ✅ The model API key comes from `.env`, never from code. *(`VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, both optional; `python -m mind_dump.check_models` tests them.)*
**Concepts:** queues, idempotency, retries, failure handling, provenance (where each chunk came from), raw landing, append-only storage, content hashes.
**Done when:** ✅ you kill a worker mid-job, restart it, and the item ends up processed once (a test proves it). *(`test_worker_process.py::test_a_worker_killed_before_commit_loses_nothing_and_the_job_completes_once`: a separate OS process, `os._exit` before COMMIT.)*
**Done when (D28), each proved by a test:**
- ✅ Re-process from raw: fetch a link from a fake server, stop the server, re-process; the chunks are rebuilt from the landed file with no network call. *(`test_worker.py::test_reprocess_from_raw_rebuilds_the_same_chunks_with_no_network`)*
- ✅ Append-only: a re-fetch that returns 404 lands a new file and a `fetches` row with status 404; the earlier good file is unchanged and the chunks stay. *(`test_a_refetch_that_gets_404_keeps_the_good_file_and_the_chunks`)*
- ✅ Raw hash: a re-fetch with identical bytes adds a `fetches` row and no new file. *(`test_a_refetch_with_identical_bytes_stores_no_new_file`)*
- ✅ Text hash: a re-fetch where only an ad slot changed lands a new file but does not re-chunk or call the embedding API. *(`test_a_refetch_where_only_furniture_changed_keeps_the_chunk_rows`, `test_m3_worker.py::test_a_refetch_where_only_furniture_changed_makes_no_embedding_call`)*
**Done when (D30), each proved by a test:**
- ✅ Crash before COMMIT: a worker that fails after deleting and inserting chunks, but before COMMIT, leaves the item's old chunks exactly as they were, and the job is not done. *(`test_worker.py::test_a_crash_before_commit_leaves_the_old_chunks_and_the_job_not_done`)*
- ✅ Retry gives the same chunks: running the same job twice gives the same chunk set (same count, indexes and text), with no duplicates. *(`test_running_the_same_job_twice_gives_the_same_chunks`)*
- ✅ Fewer chunks leave no orphans: re-processing an item that now makes 3 chunks instead of 5 leaves exactly 3, with nothing left at indexes 3 and 4. *(`test_fewer_chunks_leave_no_orphans`)*
**Done when (stage 2: D7b, D8), each proved by a test in `test_m3_worker.py` unless named:**
- ✅ A PDF upload ends `ready`; chunks carry page numbers, never cross a page, and `text[start:end]` of the stored text reproduces each one. *(`test_a_pdf_upload_ends_ready_with_pages_and_exact_positions`)*
- ✅ Scanned, encrypted and damaged PDFs end `error` at once, never retried. *(`test_a_pdf_without_readable_text_is_an_error_at_once`)*
- ✅ An image ends `ready` with chunks from its description; with no `ANTHROPIC_API_KEY` it ends `error` naming the key. *(`test_an_image_upload_ends_ready_with_chunks_from_its_description`, `test_an_image_with_no_describer_is_an_error_naming_the_key`)*
- ✅ No network inside the transaction, and text changed during embedding is retried, not saved. *(`test_models_are_called_before_the_transaction_opens`, `test_text_changed_between_embedding_and_commit_is_retried_not_saved`)*
- ✅ No key: chunks saved without vectors; after the key and `embed_missing`, the same rows gain vectors. A new `EMBEDDING_MODEL` re-embeds in place. *(`test_without_an_embedder_chunks_are_saved_and_catch_up_keeps_their_ids`, `test_a_new_embedding_model_re_embeds_existing_chunks`)*
**Gate 1:** a saved link is searchable on your Mac.
