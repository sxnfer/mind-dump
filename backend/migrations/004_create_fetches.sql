-- 004: one row per fetch of a saved link (D28). Append-only: rows are never updated.
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- Every fetch is recorded, 404s included: it is the evidence for any odd answer later.
-- storage_key always names the file that holds this fetch's bytes. When the bytes match
-- the item's previous fetch, no new file is stored and the key points at the earlier one.

CREATE TABLE fetches (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id        bigint      NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    fetched_at     timestamptz NOT NULL,
    requested_url  text        NOT NULL,
    final_url      text        NOT NULL,  -- where the redirects ended
    http_status    smallint    NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    content_type   text,
    byte_size      integer     NOT NULL CHECK (byte_size >= 0),
    sha256         text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),  -- of the raw body
    storage_key    text        NOT NULL
);

-- "The item's latest fetch" and "its fetches in order" both read this index.
CREATE INDEX fetches_item_time_idx ON fetches (item_id, fetched_at);
