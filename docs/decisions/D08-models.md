# D8: Embeddings and LLM

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; San to review)
Problem: search by meaning needs a vector for every chunk, and images need a description (D7b); later, answers and tags need an LLM. Each owner runs their own copy with their own keys.
Without a tool: keyword search only. "Mangrove restoration" never finds a chunk that says "coastal blue carbon", and images stay unreadable.
Options: a hosted model API with the owner's key, or a local model. For the calls: an SDK, or plain HTTPS.
Chosen:
- Embeddings: Voyage AI, model `voyage-4`, 1,024 dimensions, called over plain HTTPS with the existing `httpx2` (`POST https://api.voyageai.com/v1/embeddings`; `input_type` `document` for chunks, `query` for searches). Up to 128 texts per request. No Voyage SDK.
- LLM: Claude through the official `anthropic` Python SDK, model `claude-opus-5-5` by default.
- Both sit behind small interfaces (`Embedder` in `embeddings.py`, `Describer` in `describe.py`), chosen by settings, so a local model can be added later without touching the worker.
- Keys come only from the environment: `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`. Model names are settings with defaults: `EMBEDDING_MODEL=voyage-4`, `LLM_MODEL=claude-opus-5-5`.
- Both keys are optional. With no `VOYAGE_API_KEY`, chunks are saved without vectors and keyword search still works; `python -m mind_dump.embed_missing` catches up later. With no `ANTHROPIC_API_KEY`, images end `error` with a reason that names the key.
- Each chunk records which model embedded it (`chunks.embedding_model`).
- All model calls happen before the D30 transaction opens, never inside it. Failures follow D35.
Why:
- Voyage: Anthropic has no embedding model and points to Voyage. `voyage-4` is the balanced general model of the current family, with 32,000-token inputs, and 1,024 dimensions fit pgvector's HNSW index (limit 2,000).
- No Voyage SDK: it is one endpoint, and the SDK would add a dependency tree for it. `httpx2` is already approved (D33) and its mock transport tests the client.
- The Claude SDK, not raw HTTP: typed errors, built-in retries and streaming, and the tool-use types the M5 agent loop needs. Its current major version is built on `httpx2`, which is already in the lockfile. This replaces what Claude first told San ("raw httpx2 for both").
Cost: two keys for the owner to obtain. Text leaves the Mac for two hosted APIs. A model change means re-embedding everything; `embedding_model` says which chunks are behind, and `embed_missing` re-embeds them. The SDK adds a few small packages (`jiter`, `docstring-parser`, `sniffio`).
We will switch if: the owner wants nothing to leave the Mac (then a local model behind the same interfaces), or the eval in M4 shows another embedding model finds more.
