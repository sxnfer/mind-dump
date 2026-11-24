# D6: Background work

Status: Approved, 2026-11-24
Problem: saving must answer at once, but fetching, extracting and chunking an item takes seconds and can fail. That work needs to happen later, survive a crash, and never run twice at the same time.
Without a tool: do the work inside the request. "Saved" waits for the slowest website, and a crash or a restart loses the job, because nothing remembers it was asked for.
Options: work inside the request; a jobs table in Postgres; a broker (Redis, Azure Service Bus). For tracking progress: one watermark for the whole run ("processed up to item N"), or a status per job.
Chosen: a `jobs` table in the same Postgres, with a status per job: `queued`, `running`, `done`, `failed` (waiting to retry), `dead` (out of tries).
- A worker claims one job with `SELECT ... FOR UPDATE SKIP LOCKED`, so two workers never take the same job and neither waits for the other.
- A claimed job gets a lease (`locked_until`). If the worker dies, the lease runs out and another worker takes the job again (at-least-once, D30).
- The work and "mark done" commit in one transaction with the chunks (D30).
- P3 chunks text only. Embedding waits for D8, which stays Open; it adds one step and one column later.
Why (San): a status per job lets each job fail, retry and die on its own, and a crash re-runs only the jobs that were running. A watermark lets one stuck item block everything after it, and gives a re-fetch of an old item nowhere to go. A table in Postgres needs no new service, shares the data's backups, and lets "done" commit with the chunks.
Cost: we write the claiming, lease and retry logic ourselves (about 100 lines), and a polling worker adds a small, steady load on Postgres.
We will switch if: more than about 50 jobs a second, or other services must read the queue. Then a broker such as Azure Service Bus.
