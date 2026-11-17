-- 002: tags and the many-to-many link between items and tags.
-- Tags arrive in M3 (workers suggest them); the tables exist now so the schema is designed up front.

CREATE TABLE tags (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name  text NOT NULL UNIQUE CHECK (char_length(name) BETWEEN 1 AND 50)
);

CREATE TABLE item_tags (
    item_id  bigint NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    tag_id   bigint NOT NULL REFERENCES tags (id)  ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);

-- The primary key covers "tags of item X". This index covers "items with tag Y".
CREATE INDEX item_tags_tag_id_idx ON item_tags (tag_id);
