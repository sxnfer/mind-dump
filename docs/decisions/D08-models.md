# D8: Embeddings and LLM

Status: Approved, 2026-11-25 (LLM part decided by Claude under San's delegation, San to review; embeddings provider changed by San the same day)
Problem: search by meaning needs a vector for every chunk, and images need a description (D7b); later, answers and tags need an LLM. Each owner runs their own copy with their own keys.
Without a tool: keyword search only. "Mangrove restoration" never finds a chunk that says "coastal blue carbon", and images stay unreadable.
Options: a hosted model API with the owner's key, or a local model. For the calls: an SDK, or plain HTTPS.
Chosen:
- Embeddings: OpenAI, model `text-embedding-3-small`, 1,024 dimensions requested with the `dimensions` parameter (so vectors fit the existing `vector(1024)` column: no migration), called over plain HTTPS with the existing `httpx2` (`POST https://api.openai.com/v1/embeddings`). Up to 128 texts per request. No OpenAI SDK. Voyage AI `voyage-4` stays selectable with `EMBEDDING_PROVIDER=voyage`, behind the same `Embedder` interface (the two clients share one base class).
- First chosen (Claude, same day, replaced by San): Voyage AI, model `voyage-4`, 1,024 dimensions, plain `httpx2` (`POST https://api.voyageai.com/v1/embeddings`; `input_type` `document` for chunks, `query` for searches). No Voyage SDK.
- LLM: Claude through the official `anthropic` Python SDK, model `claude-opus-5-5` by default.
- Both sit behind small interfaces (`Embedder` in `embeddings.py`, `Describer` in `describe.py`), chosen by settings, so a local model can be added later without touching the worker.
- Keys come only from the environment: `OPENAI_API_KEY` (or `VOYAGE_API_KEY` with `EMBEDDING_PROVIDER=voyage`), `ANTHROPIC_API_KEY`. Only the configured embedding provider's key counts. An unknown `EMBEDDING_PROVIDER` stops startup with the valid values. Model names are settings with defaults: `EMBEDDING_MODEL` (`text-embedding-3-small` for openai, `voyage-4` for voyage), `LLM_MODEL=claude-opus-5-5`.
- All keys are optional. With no embedding key, chunks are saved without vectors and keyword search still works; `python -m mind_dump.embed_missing` catches up later. With no `ANTHROPIC_API_KEY`, images end `error` with a reason that names the key.
- Each chunk records which model embedded it (`chunks.embedding_model`). Switching provider is a model change like any other: vector search compares only vectors from the configured model, and `embed_missing` re-embeds the rest in place.
- All model calls happen before the D30 transaction opens, never inside it. Failures follow D35.
Why:
- Why OpenAI (San): San already has an OpenAI key with credits, so embeddings need no new account and no second paid provider to set up.
- Trade-offs of the switch, as facts: OpenAI takes up to 8,192 tokens per input against Voyage's 32,000 (chunks are about 250 tokens, so it does not bind). OpenAI has no query-versus-document setting: a search and a chunk are sent the same way. One provider fewer for an owner who already has an OpenAI key. Item text now goes to OpenAI instead of Voyage. Which model finds more on San's data is unmeasured until the eval runs with a key for each. OpenAI's docs don't say whether shortened vectors have unit length; search uses cosine distance, which doesn't depend on it.
- Voyage (the first choice, kept as the alternative): Anthropic has no embedding model and points to Voyage. `voyage-4` is the balanced general model of the current family, with 32,000-token inputs, and 1,024 dimensions fit pgvector's HNSW index (limit 2,000).
- No SDK for either embedding API: each is one endpoint, and the SDK would add a dependency tree for it. `httpx2` is already approved (D33) and its mock transport tests the client.
- The Claude SDK, not raw HTTP: typed errors, built-in retries and streaming, and the tool-use types the M5 agent loop needs. Its current major version is built on `httpx2`, which is already in the lockfile. This replaces what Claude first told San ("raw httpx2 for both").
Cost: two keys for the owner to obtain (OpenAI and Anthropic). Text leaves the Mac for two hosted APIs. A model change means re-embedding everything; `embedding_model` says which chunks are behind, and `embed_missing` re-embeds them. The SDK adds a few small packages (`jiter`, `docstring-parser`, `sniffio`).
We will switch if: the owner wants nothing to leave the Mac (then a local model behind the same interfaces), or the eval in M4 shows another embedding model finds more.
