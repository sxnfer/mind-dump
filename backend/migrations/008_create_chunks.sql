-- 008: chunks, the passages search will find and cite (D36).
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- Chunks are derived data: the item's text cut into pieces of about 1,000 characters.
-- A job replaces an item's whole set in one transaction with "done" (D30): DELETE, then
-- INSERT. text = source_text[start_char:end_char], so a citation can point at the place.
-- Stage 2 adds the embedding for each chunk (D8).

CREATE TABLE chunks (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id          bigint      NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    chunk_index      integer     NOT NULL CHECK (chunk_index >= 0),  -- 0, 1, 2, ...
    start_char       integer     NOT NULL CHECK (start_char >= 0),
    end_char         integer     NOT NULL,
    text             text        NOT NULL CHECK (char_length(text) > 0),
    -- Which text these chunks were cut from. The SHA-256 of the whole source text: when a
    -- new run's text has the same hash, the chunks are left alone (D28's text hash).
    source_sha256    text        NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    -- Links: the version whose text was cut. Notes: empty. Rebuilding versions (D32)
    -- replaces version rows, so this goes empty until the next job points it again.
    item_version_id  bigint      REFERENCES item_versions (id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT chunks_range CHECK (end_char > start_char),
    CONSTRAINT chunks_item_index_key UNIQUE (item_id, chunk_index)
);
-- The unique constraint's index also serves "an item's chunks in order".
