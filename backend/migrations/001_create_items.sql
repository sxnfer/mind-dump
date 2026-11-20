-- 001: items, the notes and links the owner saves.
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).

CREATE TABLE items (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        text        NOT NULL CHECK (type IN ('note', 'link')),
    title       text        CHECK (char_length(title) BETWEEN 1 AND 200),
    url         text        CHECK (char_length(url) <= 2048),
    body        text        CHECK (char_length(body) BETWEEN 1 AND 20000),
    -- M3 adds 'processing', 'ready' and 'failed' as workers pick items up.
    status      text        NOT NULL DEFAULT 'saved',
    created_at  timestamptz NOT NULL DEFAULT now(),

    -- The second layer of checks: the API validates first, the database refuses
    -- anything that slips past (a bug, a manual INSERT, a future worker).
    CONSTRAINT items_link_has_url  CHECK (type <> 'link' OR url IS NOT NULL),
    CONSTRAINT items_note_has_body CHECK (type <> 'note' OR body IS NOT NULL)
);

-- GET /items lists newest first and pages by id ("older than item N").
-- The primary key index already serves "ORDER BY id DESC", so no extra index is needed yet.
