# D5: File storage

Status: Approved, 2026-11-22
Problem: raw page landings (D28) and uploads (M2) are files, megabytes each. They don't belong in database rows, so they need a home that survives restarts and redeploys.
Without a tool: write files to wherever the API happens to run. Inside a container that disk is ephemeral: `docker compose down` and a rebuild, or a cloud redeploy of a new image, wipes every file the old container wrote.
Options: a local folder behind a small `Storage` interface, with an Azure Blob adapter added in P6 (A); both adapters now (B); Blob only, with the Azurite emulator for every user (C); a multi-cloud library such as fsspec (D).
Chosen: A. One `Storage` interface (`put(key, bytes)`, `get(key)`, `delete(key)`), picked by one setting, `STORAGE_BACKEND`.
- `local` (default, built in P1): files under one root folder. In Docker Compose that folder is a named volume, `rawdata`, the same way Postgres keeps `pgdata`.
- `azure` (P6): an Azure Blob container, reached with a managed identity, so no key is stored anywhere.
- Keys are paths that make sense on both, for example `raw/<item_id>/<fetched_at>-<sha8>.html.gz` (D28). The worker only ever sees keys, never folders or containers.
Why (San): local folder now, because every user runs Mind Dump on their own Mac and shouldn't need an Azure account. Blob in P6, because a container's disk is wiped on every redeploy, so once the app runs in Azure the files must live outside the container. The interface makes that move one setting, not a rewrite.
Cost: about 30 lines and no new dependency now. In P6, the Azure Blob SDK (and Azurite to run the same storage tests in CI) each need their own decision. Storage size stays small: about 300 MB a year gzipped (D28), and Blob costs well under $1 a month at that size.
We will switch if: never fully. The local backend stays the default for people running their own copy. Blob becomes the backend for the deployed app in P6.
