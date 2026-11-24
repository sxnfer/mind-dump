-- 006: uploaded files (PDF, PNG, JPEG) become items of type 'file' (D34).
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- The bytes live in storage under files/<sha256> (D5); the row holds what describes them.
-- A file's identity is the SHA-256 of its bytes: the same bytes are the same file,
-- whatever its name. Names and sizes can lie; the hash can't.

-- 001 wrote the type check inline, so Postgres named it items_type_check.
ALTER TABLE items DROP CONSTRAINT items_type_check;
ALTER TABLE items ADD CONSTRAINT items_type_check CHECK (type IN ('note', 'link', 'file'));

ALTER TABLE items
    ADD COLUMN sha256     text   CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN media_type text   CHECK (media_type IN ('application/pdf', 'image/png', 'image/jpeg')),
    ADD COLUMN size_bytes bigint CHECK (size_bytes > 0),
    ADD COLUMN filename   text   CHECK (char_length(filename) BETWEEN 1 AND 200);

-- The second layer of checks, as in 001: a file row must say what it holds.
ALTER TABLE items ADD CONSTRAINT items_file_has_content
    CHECK (type <> 'file' OR (sha256 IS NOT NULL AND media_type IS NOT NULL AND size_bytes IS NOT NULL));

-- The same file can be saved only once. As in 003, the database enforces it, so two
-- uploads of the same bytes at the same moment cannot both win.
CREATE UNIQUE INDEX items_file_sha256_key ON items (sha256) WHERE type = 'file';
