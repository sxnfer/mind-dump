# D32: Page history (versions)

Status: Approved, 2026-11-22
Problem: saved pages change. "What did this page say on 1 November?" must have a right answer, for example when a registry page's credits issued went from 120,000 to 145,000.
Without a tool: overwrite the item's text on each fetch. The past is still recoverable from raw files (D28), but not queryable: you would find the right fetch, unzip it and parse it again. Overwrite also answers past-date questions confidently wrong, saying 145,000 for 1 November.
Options: overwrite only; keep versions, starting a new one on every good fetch, on new raw bytes, or on new article text.
Chosen: keep versions in an `item_versions` table, one row per real change, with `valid_from` and `valid_to` (empty while current). A new version starts only when the extracted text's SHA-256 (D28) differs from the current version's.
- Only 2xx responses are extracted (D28), so a 404 or a broken page never opens a version.
- Point-in-time read: the row where `valid_from <= date` and (`valid_to` is empty or `valid_to > date`), exposed as `GET /items/{id}?as_of=<date>`.
- Versions are derived data (raw file plus extractor). When the extractor changes, rebuild each item's versions from its raw files in fetch order and replace the set in one transaction (as in D30). Dates come from the `fetches` table, so they survive.
Why (San): versions make the past queryable, and raw files keep it recoverable; those are different jobs. Version on meaning, not bytes: an ad or cookie banner changes the bytes, not the information being queried, and the raw files already keep those byte changes. A version records that the page changed, not that our extractor changed, so an extractor upgrade rebuilds history instead of adding fake versions.
Cost: an extra table and the rebuild step. `valid_from` is when we first saw the change, not when the author made it; the real change happened somewhere since the previous fetch.
We will switch if: we need the exact time of a change. That would need more frequent fetching, not a different design.
