-- 010: hybrid search (D37): exact words through Postgres full-text search, meaning
-- through pgvector. Both lists come from the same chunks rows, so a filter is an
-- ordinary WHERE clause and the indexes share the data's backups.
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).

-- The chunk's words, stemmed by the 'english' rules ("retries" and "retry" both become
-- "retri"; stop words such as "the" are dropped). GENERATED ... STORED: Postgres
-- computes it on every INSERT and UPDATE of text, so it can never disagree with the
-- text, and the worker needs no change. Cost: 'english' stemming is wrong for other
-- languages (D37).
ALTER TABLE chunks
    ADD COLUMN text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

-- GIN: an inverted index, word -> the chunks that hold it, like the index at the back
-- of a book. Serves "text_tsv @@ websearch_to_tsquery('english', ...)".
CREATE INDEX chunks_text_tsv_idx ON chunks USING gin (text_tsv);

-- HNSW: a graph of vectors where each one links to its near neighbours, so "the closest
-- chunks to this query" walks a few hundred links instead of measuring every vector.
-- vector_cosine_ops: it serves the cosine distance operator <=>, the one search uses.
-- It is approximate, and it applies a WHERE filter only AFTER it finds neighbours, so
-- search.py does not use it under a filter (it measures the filtered rows exactly).
-- Rows with a NULL embedding are simply not in it.
CREATE INDEX chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- No new indexes for the filters. item_id uses chunks_item_index_key (item_id first),
-- tag uses tags.name (unique) and item_tags_tag_id_idx, and type and created_at are read
-- from items through its primary key: one owner's items are thousands, not millions.
