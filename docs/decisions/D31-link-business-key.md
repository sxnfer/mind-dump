# D31: Business key for links

Status: Approved, 2026-11-22 (the unique index and the 409 were built in M1, migration 003; the extra normalising is built in P1)
Problem: the same page must be saved once, even when it is pasted in slightly different forms or two saves arrive at the same moment. Otherwise it is fetched twice, versioned twice and returned twice in search.
Without a tool: check-then-insert in Python. Two simultaneous saves both see nothing and both insert. Saving raw strings also treats `…/p/1234#credits` and `…/p/1234?utm_source=x` as new pages.
Options: what counts as "the same URL" (case, port, trailing slash, tracking parameters, fragment); what a repeat save does (upsert, skip with 200, reject with 409).
Chosen: the business key is the normalised URL, enforced by a partial unique index on `items (url) WHERE type = 'link'`. A repeat save is rejected with 409 and the existing item's id.
- Normalise: lowercase the scheme and host, drop the default port, drop the `#fragment`, and strip a named list of tracking parameters (`utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`).
- Keep: the trailing slash, the path's case, and every other query parameter.
- The surrogate key `item_id` stays the primary key; the URL is never a foreign key.
Why (San): the browser never sends the fragment to the server, and tracking parameters never change the content, so both are the same page. Other parameters can be different pages (`?id=1234` and `?id=5678`), and a server may treat `/docs` and `/docs/` as different pages. A false merge is worse than a false split: a duplicate is visible and fixable later (two items whose fetches end at the same final URL), while a false merge answers 409 to a page that was never saved, so it is silently lost. 409 beats skip and upsert because the panel can tell the user "already saved": skip hides the difference, and upsert would silently change an existing item. Adding the index entry is the check, so two racing saves can't both win.
Cost: a tracking-parameter list to maintain, and `/p` and `/p/` can still become two items.
We will switch if: duplicate items found by matching final URLs become common. Then flag or merge them using the fetch records.
