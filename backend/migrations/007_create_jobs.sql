-- 007: the work queue (D6). One row per request to process an item.
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- A worker claims a job with SELECT ... FOR UPDATE SKIP LOCKED and holds it for a
-- lease (locked_until). If the worker dies, the lease runs out and another worker takes
-- the job again (at-least-once, D30). Statuses:
--   queued   waiting for its first try
--   running  claimed; owned by one worker until locked_until
--   failed   a temporary failure; waits until run_after for the next try (D35)
--   done     finished; the item's chunks are current
--   dead     out of tries, or a permanent failure; error says why
-- A try counts when it is claimed (D35), so attempts goes up on the claim, not the failure.

CREATE TABLE jobs (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id       bigint      NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    -- process: the normal run (a link is fetched first).
    -- rebuild: no network; a link's versions are rebuilt from its landed files (D28, D32),
    --          and the chunks are rewritten even when the text hash is unchanged.
    kind          text        NOT NULL DEFAULT 'process' CHECK (kind IN ('process', 'rebuild')),
    status        text        NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'running', 'failed', 'done', 'dead')),
    attempts      smallint    NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    run_after     timestamptz NOT NULL DEFAULT now(),  -- not claimable before this
    locked_until  timestamptz,                         -- the lease, while running
    error         text,                                -- the latest failure's reason
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,                         -- set when done or dead

    CONSTRAINT jobs_running_has_lease CHECK (status <> 'running' OR locked_until IS NOT NULL),
    CONSTRAINT jobs_finished_when_final
        CHECK ((status IN ('done', 'dead')) = (finished_at IS NOT NULL)),
    CONSTRAINT jobs_dead_says_why CHECK (status <> 'dead' OR error IS NOT NULL)
);

-- At most one unfinished job per item. The database decides, so two "re-run" clicks at
-- the same moment can't both queue a job, and two workers never process the same item.
CREATE UNIQUE INDEX jobs_one_open_per_item ON jobs (item_id)
    WHERE status IN ('queued', 'running', 'failed');

-- The claim query: "unfinished jobs that are due, oldest due first". A job is due at
-- run_after while waiting, and at locked_until (lease expiry) while running. The claim
-- query uses exactly this expression, so Postgres reads the index instead of the table.
CREATE INDEX jobs_due_idx
    ON jobs ((CASE WHEN status = 'running' THEN locked_until ELSE run_after END), id)
    WHERE status IN ('queued', 'running', 'failed');

-- "The item's latest job" (its processing state in GET /items).
CREATE INDEX jobs_item_idx ON jobs (item_id, id);

-- The processing state now comes from the item's latest job, so the placeholder column
-- from 001 (always 'saved') goes, leaving one source of truth.
ALTER TABLE items DROP COLUMN status;

-- Items saved before the queue existed get a job, so the worker catches them up.
-- Files wait for PDF text (stage 2).
INSERT INTO jobs (item_id) SELECT id FROM items WHERE type IN ('note', 'link') ORDER BY id;
