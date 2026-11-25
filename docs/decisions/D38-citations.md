# D38: Citations and grounding

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; reviewed and approved by San the same day)
Problem: every claim in an answer must point at the exact item, page and passage it came from, and the owner must see when the items do not hold the answer (the Trust row in `product.md`).
Without a tool: an answer in fluent prose that may come from the model's own memory, with no way to check which saved passage, if any, says it.
Options: the model writes a marker with the chunk id and our code checks it; trust the model's own list of sources; the API's built-in citations feature.
Chosen: markers checked in code.
- The model writes a marker with the chunk id after each claim, for example `[c:123]`. Our code, not the model, decides what counts.
- A citation is valid only if that chunk was returned by a tool call in this conversation. (In Discuss, the cited passage and its neighbours that our code puts into the first message count too: our code chose them, as it chooses a tool's results.) Invalid markers are removed from the text and reported (`invalid_citations`, with the reason: not retrieved, or no such chunk).
- An answer with no valid citation is marked `grounded: false`. The system prompt tells the model to say the items do not hold the answer rather than answer from its own knowledge.
- Each citation is stored as a snapshot: item id, type, title, url, page, character positions and the passage text. An old answer still shows its sources after the item is re-processed (new chunk ids) or deleted.
Why:
- A check in code can be tested and cannot be talked out of.
- Chunk ids already map to item, page and position (D36).
- Rejected: trusting the model's own list of sources (it can cite something it never read); the API's built-in citations feature (it ties the format to one provider and does not work together with structured outputs).
Cost: a marker says which passage, not which words in it. A valid citation proves the passage was retrieved, not that it supports the sentence; the answers eval's judge checks that.
We will switch if: the eval shows cited passages often do not support their claims.
