-- 009: text from files (D7b) and an embedding for every chunk (D8).
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).

-- pgvector ships in the database image (D3); this turns it on for this database.
CREATE EXTENSION IF NOT EXISTS vector;

-- Each chunk gets its vector, the model that made it, and (PDFs) its page.
-- embedding stays NULL while no embedding model is configured (no VOYAGE_API_KEY);
-- keyword search works without it, and `python -m mind_dump.embed_missing` catches up.
-- embedding_model is recorded because vectors from two models can't be compared: a
-- model change means re-embedding, and this column says which chunks are behind.
-- No vector index yet: search (stage 3, M4) adds it with the queries that use it.
ALTER TABLE chunks
    ADD COLUMN embedding       vector(1024),
    ADD COLUMN embedding_model text CHECK (char_length(embedding_model) BETWEEN 1 AND 200),
    ADD COLUMN page            integer CHECK (page >= 1),
    ADD CONSTRAINT chunks_embedding_has_model
        CHECK ((embedding IS NULL) = (embedding_model IS NULL));

-- A file's text: a PDF's pages, or an image's description (D7b). Derived data, like a
-- link's versions (D32): it can be rebuilt from the stored file (files/<sha256>), so a
-- `rebuild` job overwrites it. One row per file item; the chunks' start_char and
-- end_char point into `text`. The worker writes it in the same transaction as the
-- chunks (D30), so the text and its chunks always agree.
CREATE TABLE file_texts (
    item_id      bigint      PRIMARY KEY REFERENCES items (id) ON DELETE CASCADE,
    text         text        NOT NULL CHECK (char_length(text) > 0),
    text_sha256  text        NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    -- Which bytes it came from: items.sha256 when it was made.
    file_sha256  text        NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    -- PDFs: page n is text[page_starts[n]:page_ends[n]] (Postgres arrays count from 1,
    -- like page numbers). Images: both NULL.
    page_starts  integer[],
    page_ends    integer[],
    -- What made the text: "pypdf 7.1.1", or "<model> (image description)".
    extractor    text        NOT NULL CHECK (char_length(extractor) BETWEEN 1 AND 200),
    created_at   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT file_texts_pages_match CHECK (
        (page_starts IS NULL AND page_ends IS NULL)
        OR cardinality(page_starts) = cardinality(page_ends)
    )
);

-- A third kind of job: embed the chunks an item already has (no fetch, no re-chunking
-- when the text is unchanged). `embed_missing` queues these after a key or model change.
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('process', 'rebuild', 'embed'));

-- Files saved before now had no job (007 left them waiting); they get one, so the
-- worker catches them up, as 007 did for notes and links.
INSERT INTO jobs (item_id)
SELECT id FROM items WHERE type = 'file' ORDER BY id
ON CONFLICT (item_id) WHERE status IN ('queued', 'running', 'failed') DO NOTHING;
