# M9: Harden and open up

> Read this when: you are working on M9, or need its tasks and done-when list.

**Goal:** safe to run, safe to share, easy for a stranger to start.
**Decisions needed:** D12.
**Tasks:**
- A local token so only the owner's app can call the API.
- Rate limits and size limits on every write endpoint.
- Backups of Postgres and files, and a restore drill: restore into a fresh database and check the item count.
- Export all data as a zip; delete everything with one command.
- A short threat model page: what an attacker could try and what stops it.
- `README.md` quick start, `CONTRIBUTING.md`, issue templates.
**Concepts:** security basics, recovery, data ownership, open-source hygiene.
**Done when:** a friend follows the README on their Mac and saves an item in under 15 minutes.
**Gate 3:** live on Azure, an alert fires, the restore drill passes, a stranger can run it.
