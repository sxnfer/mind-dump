# D33: Fetch client

Status: Approved, 2026-11-22
Problem: the worker downloads saved pages. D28 must record the requested URL, the final URL after redirects, and the status, including for a 404. P1's tests must replay recorded pages without the live web.
Without a tool: Python's `urllib.request`. It works, but a 404 raises an error you catch to read the body, it doesn't record the redirect chain, and tests must patch the network.
Options: `urllib.request`; `httpx2`, already in the lockfile as a test dependency (D25); `requests`, a new package that does less.
Chosen: `httpx2`, moved from the dev group to the runtime dependencies. The fetcher uses explicit timeouts, follows redirects (keeping the history), caps the body size, and treats every status as a normal response.
Why (San): it's already in the lockfile, so there's no new package, and tests can mock it with its mock transport instead of calling real sites.
Cost: one more runtime dependency in the image. A known gap, for M9: the fetcher will follow any URL a user saves, including private addresses (SSRF). That is fine while each copy has one owner on their own Mac, but it must be guarded before any shared or public deploy.
We will switch if: never planned. This is the same library family FastAPI's TestClient uses.
