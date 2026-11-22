-- 005: the text history of each saved link (D32).
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- One row per real change of meaning: a new version starts only when the extracted
-- text's hash changes, never for ads, banners or markup. valid_to is empty while the
-- version is current. "What did it say at time T?" is the row where
-- valid_from <= T AND (valid_to IS NULL OR valid_to > T).
-- Versions are derived (raw file + extractor), so they can be rebuilt from raw files.

CREATE TABLE item_versions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id      bigint      NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    fetch_id     bigint      NOT NULL REFERENCES fetches (id) ON DELETE CASCADE,  -- first seen here
    valid_from   timestamptz NOT NULL,
    valid_to     timestamptz,
    text         text        NOT NULL CHECK (char_length(text) > 0),
    text_sha256  text        NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT item_versions_range CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- At most one current version per item: the database refuses a second, even from a bug.
CREATE UNIQUE INDEX item_versions_one_current ON item_versions (item_id) WHERE valid_to IS NULL;

-- Point-in-time reads: one item's versions by start time.
CREATE INDEX item_versions_item_from_idx ON item_versions (item_id, valid_from);
