# D30: Idempotent jobs

Status: Approved, 2026-11-20
Problem: a job queue can promise that every job runs at least once, never exactly once. A worker that dies can't say how far it got, so its lease runs out and another worker runs the job again from the start. Every job must therefore be safe to run twice.
Without a tool: plain INSERTs. A retry duplicates every chunk, and search returns each passage twice. Marking the job done first is worse: a crash then loses the work silently, and the item says "ready" but is never searchable.
Options: mark done first, then work (at-most-once); work, then mark done last, with upserts on `(item_id, chunk_index)`; work, then mark done last, replacing the item's whole chunk set in one transaction.
Chosen: at-least-once delivery plus idempotent processing. The worker does the work first and marks the job done last. It replaces the item's chunk set (DELETE the item's chunks, then INSERT the new ones), and marks the job done, all in the same transaction.
Why (San): a crash should cause a repeat, which is loud and fixable, never a loss, which is silent. An upsert fixes retries but leaves orphans when a re-process makes fewer chunks, because the old extra chunks keep their stale text. Chunks are derived data that can always be rebuilt from the raw file (D28), so replacing them is safe. One transaction means a crash before COMMIT rolls everything back: the old chunks stay, readers never see the middle, and "done but no chunks" can't happen, because the queue lives in the same Postgres as the data.
Cost: a re-process rewrites every chunk of the item, even unchanged ones. The text hash (D28) skips the whole step when the text didn't change, so this happens only on real changes.
We will switch if: items grow so large that rewriting all their chunks takes a long time or locks readers out noticeably. Then compare per-chunk hashes and write only the chunks that changed.
