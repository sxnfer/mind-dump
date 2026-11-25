-- 011: chats with the agent (D9, D21), their citations (D38), and what every model call
-- cost (D39).
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).

-- A conversation: one question and its follow-ups. A Discuss chat is scoped to one item
-- and one passage of it (scope_*). The scope is a SNAPSHOT, with no foreign key on
-- purpose: deleting the item must not delete the chat. The chat stays readable (every
-- answer keeps its citations' snapshots); the API refuses new messages in it with a
-- 409, because its search would only ever see an item that is gone. Item ids are
-- identity values and are never reused, so a missing items row means "deleted".
-- The passage is stored by its character positions, not its chunk id, so it survives
-- re-chunking (D36): a re-run gives the item new chunk ids, not a new text.
CREATE TABLE conversations (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Set when created, or from the first question when the first answer is saved.
    title             text        CHECK (char_length(title) BETWEEN 1 AND 200),
    scope_item_id     bigint,                -- Discuss: the item; NULL: all items
    scope_item_title  text,                  -- as it was when the chat began
    scope_start_char  integer CHECK (scope_start_char >= 0),
    scope_end_char    integer,
    scope_page        integer CHECK (scope_page >= 1),
    scope_text        text,                  -- the cited passage, as the owner saw it
    -- POST /ask filters (type, from, to, tag) applied by our code to every search.
    filters           jsonb       NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    -- A passage needs an item, and positions that point at real text.
    CONSTRAINT conversations_passage_has_item
        CHECK (scope_start_char IS NULL OR scope_item_id IS NOT NULL),
    CONSTRAINT conversations_passage_range CHECK (
        (scope_start_char IS NULL AND scope_end_char IS NULL AND scope_text IS NULL)
        OR (scope_end_char > scope_start_char AND scope_text IS NOT NULL)
    )
);

-- One row per turn: the owner's message, or the agent's whole answer to it.
-- api_messages: the exact Messages API messages of this turn, in order. A user row
-- holds one ({"role": "user", ...}); an assistant row holds every message the loop
-- added: assistant turns with their tool calls, our tool results, the final answer.
-- The next request replays all rows' api_messages in seq order, unchanged (D21).
-- Type json, not jsonb, on purpose: json keeps the text exactly as written, key order
-- included; jsonb re-orders keys. A replayed tool call must be the same bytes it was.
-- A user row and its answer are written in ONE transaction, after the answer is
-- complete: a failed or abandoned turn leaves nothing behind.
CREATE TABLE messages (
    id                 bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id    bigint      NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq                integer     NOT NULL CHECK (seq >= 1),  -- 1, 2, 3, ... in order
    role               text        NOT NULL CHECK (role IN ('user', 'assistant')),
    text               text        NOT NULL,  -- what the app shows; answers keep [c:id] markers
    api_messages       json        NOT NULL,
    -- Assistant rows only (D38). citations: snapshots {marker, chunk_id, item_id, type,
    -- title, url, page, start_char, end_char, quote}. invalid_citations: markers removed,
    -- with the reason. sources: every chunk a tool returned this turn, as snapshots;
    -- a later turn may cite them too.
    citations          jsonb       NOT NULL DEFAULT '[]',
    invalid_citations  jsonb       NOT NULL DEFAULT '[]',
    sources            jsonb       NOT NULL DEFAULT '{}',
    grounded           boolean,
    stop_reason        text,
    incomplete         boolean,
    model              text,
    usage              jsonb,                 -- this turn's tokens and estimated cost
    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT messages_conversation_seq_key UNIQUE (conversation_id, seq),
    CONSTRAINT messages_assistant_fields CHECK (
        role = 'user' OR (grounded IS NOT NULL AND incomplete IS NOT NULL)
    )
);
-- The unique constraint's index also serves "a conversation's messages in order".

-- Every model call (D39): answers, judge calls, image descriptions, embeddings.
-- cost_usd is an ESTIMATE from the price table in settings, made when the row is written.
-- Rows are never deleted with what they paid for: the money was spent.
CREATE TABLE model_usage (
    id                           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at                   timestamptz NOT NULL DEFAULT now(),
    provider                     text        NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 50),
    model                        text        NOT NULL CHECK (char_length(model) BETWEEN 1 AND 200),
    -- answer, judge, describe, check, embed_documents, embed_query
    purpose                      text        NOT NULL CHECK (char_length(purpose) BETWEEN 1 AND 50),
    input_tokens                 integer     NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens                integer     NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cache_creation_input_tokens  integer     NOT NULL DEFAULT 0
        CHECK (cache_creation_input_tokens >= 0),
    cache_read_input_tokens      integer     NOT NULL DEFAULT 0 CHECK (cache_read_input_tokens >= 0),
    cost_usd                     numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),
    conversation_id              bigint      REFERENCES conversations (id) ON DELETE SET NULL
);
-- "Today's total" and "the last 7 days": a range on created_at.
CREATE INDEX model_usage_created_at_idx ON model_usage (created_at);

-- One row per question the API accepted (D39's rate limit: questions in the last 60
-- seconds). Written before the model is called, so failing questions count too: a
-- client stuck retrying a failing ask is exactly what the limit is for.
CREATE TABLE ask_requests (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    conversation_id  bigint      REFERENCES conversations (id) ON DELETE SET NULL
);
CREATE INDEX ask_requests_created_at_idx ON ask_requests (created_at);
