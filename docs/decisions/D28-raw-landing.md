# D28: Raw landing for links

Status: Approved, 2026-11-19
Problem: M3 fetches a page and extracts its text in one pass, so the HTML exists only in memory. To re-process (a parser bug, a new chunk size in M4, a new extractor) we must fetch again, and by then the page may be changed, gone, behind a paywall or blocking us.
Without a tool: re-processing depends on the live web. A dead page means the source is lost, and a 404 page can be parsed into the search index.
Options: extract straight from the response and keep nothing; land the raw response and overwrite it on each fetch; land every fetch, append-only.
Chosen: land every fetch, append-only, before extracting.
- The worker saves the response body, gzipped, through the same `Storage` interface as uploads (D5), under `raw/<item_id>/<fetched_at>-<sha8>.html.gz`. Files are never overwritten.
- If the body's SHA-256 equals the item's latest landed file, no new file is stored; only the fetch is recorded.
- One row per fetch in a `fetches` table: `item_id`, `fetched_at`, `requested_url`, `final_url`, `http_status`, `content_type`, `byte_size`, `sha256`, `storage_key`.
- Bodies are landed for error statuses too (evidence). Extraction runs only on 2xx responses with a content type we can parse.
- Extraction reads only from the landed file, never from the network.
- A second SHA-256, of the extracted text, decides whether to re-chunk and re-embed. Same text: skip.
Why: the source survives parser bugs, page changes and dead links; re-processing is offline and repeatable (same input every time), which M4 tuning needs; a 404 can never replace the last good copy; the fetch record explains any odd answer later. The raw hash skips storing identical bytes; the text hash skips paying for embeddings when only ads or markup changed.
Cost: disk space, about 2 GB a year for one person (about 300 MB gzipped), plus a `fetches` table and files to delete when an item is deleted. No new dependency: `gzip` and `hashlib` are in Python's standard library. Depends on D5 (file storage), still Open.
We will switch if: raw storage grows past what a laptop disk should hold. Then add a retention rule (for example, keep the first and the latest good fetch per item).
