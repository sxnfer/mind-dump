# M2: Upload files

> Read this when: you are working on M2, or need its tasks and done-when list.

**Goal:** drop a PDF or image on the panel; the file is stored and listed.
**Decisions needed:** D5.
**Tasks:**
- `POST /items/upload` with size and type limits (for example 25 MB; PDF, PNG, JPEG).
- Store the file in a local folder behind a small `Storage` interface (`save`, `open`, `delete`), so Azure Blob can replace it in M7.
- Save a checksum, so the same file twice is found as a duplicate.
- Delete removes both the row and the file.
- In the app: drag a file onto the panel; show progress.
**Concepts:** storage interfaces, limits as security, checksums.
**Done when:** a too-large file gets a clear 413 error; deleting an item leaves no file behind (a test checks it).
