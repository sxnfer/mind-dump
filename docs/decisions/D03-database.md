# D3: Main database

Status: Approved, 2026-11-17
Problem: items must survive restarts, and the API and the M3 workers must share them. M3 needs a job queue, and M4 needs vectors.
Without a tool: data in a Python list dies with the process, and each process sees a different list.
Options: SQLite; Postgres (with pgvector).
Chosen: Postgres 18, using the `pgvector/pgvector:pg18` image so vectors need only `CREATE EXTENSION` in M4.
Why: many writers at once with row-level locks; `FOR UPDATE SKIP LOCKED` makes a jobs table a real queue (D6); pgvector for search by meaning; Azure offers managed Postgres (M7).
Cost: a server to run (a container locally), backups to think about (M9), more to learn than SQLite.
We will switch if: never likely for this app's scale.
