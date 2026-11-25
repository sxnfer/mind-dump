"""The work queue (D6): enqueue, claim, and finish jobs. About 100 lines, as D6 priced it.

How a job moves:
    queued --claim--> running --done--> done
                         |----temporary failure, tries left--> failed --(run_after)--> claim
                         |----permanent failure, or 3rd try--> dead
    running with an expired lease (its worker died) --claim--> running again, or dead
    when its 3 tries are used up. A try counts when claimed (D35), so a job that kills
    its worker every time still ends dead instead of looping forever.

Every function takes `now` instead of reading a clock, so tests never wait.
The finishing functions check that the caller still OWNS the job: its status is
running and its attempts number is the one the caller claimed. A worker whose lease ran
out and was taken over gets LeaseLost and must write nothing (the attempts number works
as a fencing token).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import Connection
from psycopg.rows import DictRow

Conn = Connection[DictRow]

MAX_TRIES = 3
# The wait before the next try, by the number of tries so far (D35).
RETRY_WAITS = {1: timedelta(minutes=1), 2: timedelta(minutes=5)}
DEFAULT_LEASE = timedelta(minutes=5)  # far above one fetch (10 s timeout) plus chunking

# "When is this unfinished job due?" The index jobs_due_idx (migration 007) is built on
# exactly this expression, so the claim reads the index.
_DUE = "(CASE WHEN status = 'running' THEN locked_until ELSE run_after END)"

_CLAIM = f"""
WITH next AS (
    SELECT id FROM jobs
    WHERE status IN ('queued', 'running', 'failed') AND {_DUE} <= %(now)s
    ORDER BY {_DUE}, id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs AS j SET
    status       = CASE WHEN j.attempts >= %(max)s THEN 'dead' ELSE 'running' END,
    attempts     = LEAST(j.attempts + 1, %(max)s),
    locked_until = CASE WHEN j.attempts >= %(max)s THEN NULL ELSE %(lease_end)s END,
    finished_at  = CASE WHEN j.attempts >= %(max)s THEN %(now)s END,
    error = CASE
        WHEN j.status = 'running' AND j.attempts >= %(max)s
            THEN 'The worker stopped during try ' || j.attempts || ' of ' || %(max)s
                 || ' (lease expired); gave up'
        WHEN j.status = 'running'
            THEN 'The worker stopped during try ' || j.attempts || ' (lease expired)'
        ELSE j.error
    END,
    updated_at   = %(now)s
FROM next WHERE j.id = next.id
RETURNING j.id, j.item_id, j.kind, j.status, j.attempts, j.run_after, j.locked_until, j.error
"""  # noqa: S608 (_DUE is a constant; every value is a parameter)


class LeaseLost(Exception):
    """This worker no longer owns the job: its lease ran out and another worker took it,
    or the item (and with it the job) was deleted."""


@dataclass(frozen=True)
class Job:
    id: int
    item_id: int
    kind: str
    status: str
    attempts: int
    run_after: datetime
    locked_until: datetime | None
    error: str | None


def enqueue(conn: Conn, item_id: int, kind: str = "process") -> DictRow | None:
    """Queue a job for the item. None when it already has an unfinished one.

    Runs inside the caller's transaction, so the API can save an item and its job together.
    """
    return conn.execute(
        """
        INSERT INTO jobs (item_id, kind) VALUES (%s, %s)
        ON CONFLICT (item_id) WHERE status IN ('queued', 'running', 'failed') DO NOTHING
        RETURNING id, item_id, kind, status, attempts, run_after, created_at
        """,
        (item_id, kind),
    ).fetchone()


def claim(conn: Conn, now: datetime, lease: timedelta = DEFAULT_LEASE) -> Job | None:
    """Take the oldest due job, or None. Never waits for a job another worker holds.

    One statement, so with an autocommit connection the claim commits at once and other
    workers see the job as running. A job claimed with its tries used up goes dead, and
    the next due job is tried instead.
    """
    params = {"now": now, "lease_end": now + lease, "max": MAX_TRIES}
    while True:
        row = conn.execute(_CLAIM, params).fetchone()
        if row is None:
            return None
        if row["status"] == "running":
            return Job(**row)


def lock_owned(conn: Conn, job: Job) -> None:
    """Lock the job row for the rest of the transaction, if this worker still owns it."""
    row = conn.execute(
        """
        SELECT 1 FROM jobs WHERE id = %s AND status = 'running' AND attempts = %s
        FOR UPDATE
        """,
        (job.id, job.attempts),
    ).fetchone()
    if row is None:
        raise LeaseLost(f"job {job.id} try {job.attempts} is no longer ours")


def _finish(conn: Conn, job: Job, sql: str, params: dict) -> None:
    cur = conn.execute(sql, {**params, "id": job.id, "attempts": job.attempts})
    if cur.rowcount != 1:
        raise LeaseLost(f"job {job.id} try {job.attempts} is no longer ours")


def mark_done(conn: Conn, job: Job, now: datetime) -> None:
    _finish(
        conn,
        job,
        """
        UPDATE jobs SET status = 'done', locked_until = NULL, error = NULL,
                        finished_at = %(now)s, updated_at = %(now)s
        WHERE id = %(id)s AND status = 'running' AND attempts = %(attempts)s
        """,
        {"now": now},
    )


def mark_dead(conn: Conn, job: Job, now: datetime, error: str) -> None:
    _finish(
        conn,
        job,
        """
        UPDATE jobs SET status = 'dead', locked_until = NULL, error = %(error)s,
                        finished_at = %(now)s, updated_at = %(now)s
        WHERE id = %(id)s AND status = 'running' AND attempts = %(attempts)s
        """,
        {"now": now, "error": error},
    )


def mark_failed(conn: Conn, job: Job, now: datetime, error: str) -> str:
    """A temporary failure: wait and try again, or dead after the last try. Returns the status."""
    wait = RETRY_WAITS.get(job.attempts)
    if job.attempts >= MAX_TRIES or wait is None:
        mark_dead(conn, job, now, f"{error} (gave up after {job.attempts} tries)")
        return "dead"
    _finish(
        conn,
        job,
        """
        UPDATE jobs SET status = 'failed', locked_until = NULL, error = %(error)s,
                        run_after = %(run_after)s, updated_at = %(now)s
        WHERE id = %(id)s AND status = 'running' AND attempts = %(attempts)s
        """,
        {"now": now, "error": error, "run_after": now + wait},
    )
    return "failed"
