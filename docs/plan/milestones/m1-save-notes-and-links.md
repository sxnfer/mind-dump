# M1: Save notes and links

> Read this when: you are working on M1, or need its tasks and done-when list.

**Goal:** save, list and delete notes and links through the API, stored in Postgres.
**Decisions needed:** D2, D3, D4, D18.
**Tasks:**
- Design the first tables: `items` (id, type, title, url, body, status, created_at) and `tags`.
- Write migration `001_create_items.sql` and a small script that runs new files in order.
- Endpoints: `POST /items`, `GET /items`, `GET /items/{id}`, `DELETE /items/{id}`.
- Validate input: reject an empty note, a bad URL, a too-long title, with clear 4xx errors.
- `docker-compose.yml` with the API and Postgres. One command starts both.
- In the app: the + button saves a note or the link on your clipboard.
**Concepts:** REST design, status codes, schema design, migrations, validation.
**Done when:** a saved link survives a restart of both containers; tests cover each endpoint and each bad input.
**Proof:** a schema diagram in `docs/`, API tests in CI.
