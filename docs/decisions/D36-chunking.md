# D36: Chunking

Status: Approved, 2026-11-24
Problem: search (M4) finds and cites passages, not whole items. The worker must cut each item's text into chunks that each hold a complete idea and remember where they came from, so an answer can point at the exact place.
Without a tool: one chunk per item. A long article becomes one blurred match, and a citation can only say "somewhere in this page".
Options: paragraphs packed to a size limit; fixed-size windows with overlap; token-based windows (needs a tokenizer, a new dependency tied to D8).
Chosen: paragraphs packed to about 1,000 characters, with no overlap.
- Split the text on blank lines into paragraphs.
- Join whole paragraphs into one chunk until the next would pass 1,000 characters.
- A single paragraph longer than that is cut at sentence ends; a sentence longer than the limit is cut at the limit.
- Each chunk records its item, its index (0, 1, 2, ...), and its start and end character positions in the version's text, for citations.
- The same text always gives the same chunks, so a retry gives the same set (D30).
Why (San): whole paragraphs keep whole ideas together, and the rule is short enough to explain chunk by chunk. Fixed windows cut mid-sentence, and their overlap stores the same text twice to repair those cuts. Tokens tie chunking to an embedding model that isn't chosen yet (D8) and add a dependency.
Cost: chunk sizes vary, and a character limit only approximates an embedding model's token limit. 1,000 characters is about 250 tokens, well inside any current model's input.
We will switch if: the M4 eval set shows answers missing context that sits across a chunk boundary. Then add overlap, or tune the size.
