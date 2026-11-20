# D27: Postgres driver

Status: Approved, 2026-11-17
Problem: Python must send SQL to Postgres and read rows back, without opening a new connection (20-50 ms) for every request.
Without a tool: no way to talk to Postgres from Python.
Options: psycopg 3 (+ psycopg_pool); asyncpg; SQLAlchemy.
Chosen: psycopg 3 (`psycopg[binary]`) and `psycopg_pool`.
Why: the standard driver; plain SQL with server-side parameters (no SQL injection); a connection pool; pgvector support for M4. SQLAlchemy would hide the SQL San is here to learn.
Cost: we write SQL and map rows to models ourselves. The `binary` extra ships a prebuilt C library (larger wheel, no compiler needed).
We will switch if: we need an ORM for complex models, which is unlikely.
