# M2: Upload files

> Read this when: you are working on M2, or need its tasks and done-when list.

**Goal:** drop a PDF or image on the panel; the file is stored and listed.
**Decisions needed:** D5 (approved), D34 (approved).
**Tasks:**
- [x] `POST /items/upload` with size and type limits: 25 MB; PDF, PNG, JPEG, judged by the first bytes (P2, D34).
- [x] Store the file in a local folder behind a small `Storage` interface (`put`, `get`, `delete`), so Azure Blob can replace it in P6 (P1 and P2, D5).
- [x] Save a checksum, so the same file twice is found as a duplicate: SHA-256, unique index, 409 (P2, D34).
- [x] Delete removes both the row and the file (P2).
- [ ] In the app: drag a file onto the panel; show progress.
**Concepts:** storage interfaces, limits as security, checksums.
**Done when:** a too-large file gets a clear 413 error; deleting an item leaves no file behind (a test checks it).
