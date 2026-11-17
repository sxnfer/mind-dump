<!-- Copy to docs/decisions/DNN-short-name.md. One file per decision. Example filled in below. -->

# D6: Background work

Status: Approved, 2026-11-20
Problem: saving a 30-page PDF takes 20 s to process. The user must not wait.
Without a tool: do the work inside the request. "Saved" takes 20 s, and a crash loses the job.
Options: jobs table in Postgres; Redis; Azure Service Bus.
Chosen: jobs table in Postgres.
Why: no new service to run; same backups as the data.
Cost: we write the retry and locking logic ourselves.
We will switch if: more than ~50 jobs a second, or many services must read the queue.
