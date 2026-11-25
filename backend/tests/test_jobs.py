"""The queue (D6, D35): safe claiming, the lease, and tries that count when claimed.

Every claim gets `now` from the test, so nothing here waits for real time to pass.
"""

import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from mind_dump import jobs

Conn = psycopg.Connection[DictRow]
LEASE = timedelta(minutes=5)


@pytest.fixture
def t0() -> datetime:
    """A little after the database's now(), so jobs queued with run_after = now() are due."""
    return datetime.now(UTC) + timedelta(minutes=1)


def new_note(conn: Conn, body: str = "a note") -> int:
    return conn.execute(
        "INSERT INTO items (type, body) VALUES ('note', %s) RETURNING id", (body,)
    ).fetchone()["id"]


def job_row(conn: Conn, job_id: int) -> DictRow:
    return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def queue_notes(conn: Conn, n: int) -> list[int]:
    return [jobs.enqueue(conn, new_note(conn, f"note {i}"))["id"] for i in range(n)]


# ---------- A1: two workers never take the same job ----------


def test_concurrent_claimers_take_every_job_exactly_once(
    conn: Conn, database_url: str, t0: datetime
) -> None:
    job_ids = queue_notes(conn, 40)
    claimed: list[list[int]] = [[] for _ in range(6)]
    start = threading.Barrier(6)

    def claimer(slot: int) -> None:
        # Each claimer has its own connection, like separate worker processes.
        with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as own:
            start.wait()  # all start together, to make collisions likely
            while (job := jobs.claim(own, t0, LEASE)) is not None:
                claimed[slot].append(job.id)

    threads = [threading.Thread(target=claimer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    everything = [job_id for slot in claimed for job_id in slot]
    assert sorted(everything) == sorted(job_ids)  # every job claimed...
    assert len(everything) == len(set(everything))  # ...exactly once
    assert sum(1 for slot in claimed if slot) > 1  # and the work really was shared


def test_a_claimer_skips_a_job_another_transaction_holds_instead_of_waiting(
    conn: Conn, database_url: str, t0: datetime
) -> None:
    first, second = queue_notes(conn, 2)
    with psycopg.connect(database_url, row_factory=dict_row) as holder:
        # Another worker's open transaction holds the oldest job's row lock.
        holder.execute("SELECT id FROM jobs WHERE id = %s FOR UPDATE", (first,))
        with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as other:
            # If the claim waited for that lock, this would fail after 2 seconds.
            other.execute("SET lock_timeout = '2s'")
            job = jobs.claim(other, t0, LEASE)
        holder.rollback()

    assert job is not None and job.id == second  # skipped, not blocked


# ---------- A8: the lease ----------


def test_a_running_job_is_not_claimable_until_its_lease_runs_out(conn: Conn, t0: datetime) -> None:
    (job_id,) = queue_notes(conn, 1)
    job = jobs.claim(conn, t0, LEASE)
    assert job is not None and job.id == job_id and job.status == "running"
    assert job_row(conn, job_id)["locked_until"] == t0 + LEASE

    assert jobs.claim(conn, t0 + LEASE - timedelta(seconds=1), LEASE) is None  # still leased

    again = jobs.claim(conn, t0 + LEASE, LEASE)  # the worker died; the lease ran out
    assert again is not None and again.id == job_id and again.attempts == 2
    assert "lease expired" in again.error


def test_a_worker_whose_lease_was_taken_over_can_not_finish_the_job(
    conn: Conn, t0: datetime
) -> None:
    queue_notes(conn, 1)
    slow = jobs.claim(conn, t0, LEASE)
    fast = jobs.claim(conn, t0 + LEASE, LEASE)  # took over after the lease ran out

    with pytest.raises(jobs.LeaseLost):
        jobs.mark_done(conn, slow, t0 + LEASE)  # the old owner is fenced off
    jobs.mark_done(conn, fast, t0 + LEASE)
    assert job_row(conn, fast.id)["status"] == "done"


# ---------- A7: a try counts when it is claimed ----------


def test_a_job_that_kills_its_worker_every_time_ends_dead_after_3_claims(
    conn: Conn, t0: datetime
) -> None:
    (job_id,) = queue_notes(conn, 1)
    now = t0
    for attempt in (1, 2, 3):
        job = jobs.claim(conn, now, LEASE)  # ...and the worker dies, finishing nothing
        assert job is not None and job.attempts == attempt
        now += LEASE

    assert jobs.claim(conn, now, LEASE) is None  # the 4th claim finds it out of tries
    row = job_row(conn, job_id)
    assert row["status"] == "dead"
    assert row["attempts"] == 3
    assert "gave up" in row["error"]
    assert row["finished_at"] == now
    assert jobs.claim(conn, now + timedelta(days=1), LEASE) is None  # never loops


def test_claiming_moves_past_a_used_up_job_to_the_next_due_one(conn: Conn, t0: datetime) -> None:
    (stuck,) = queue_notes(conn, 1)
    now = t0
    for _ in range(3):
        jobs.claim(conn, now, LEASE)
        now += LEASE
    (fresh,) = queue_notes(conn, 1)
    # Due at the same moment as the stuck job's lease expiry, so the stuck one comes first.
    conn.execute("UPDATE jobs SET run_after = %s WHERE id = %s", (now, fresh))

    job = jobs.claim(conn, now, LEASE)  # one call: the stuck job dies, the fresh one runs

    assert job is not None and job.id == fresh
    assert job_row(conn, stuck)["status"] == "dead"


# ---------- retries at the queue level (D35) ----------


def test_failures_wait_1_then_5_minutes_then_the_job_is_dead(conn: Conn, t0: datetime) -> None:
    (job_id,) = queue_notes(conn, 1)

    job = jobs.claim(conn, t0, LEASE)
    assert jobs.mark_failed(conn, job, t0, "HTTP 503") == "failed"
    assert job_row(conn, job_id)["run_after"] == t0 + timedelta(minutes=1)
    assert jobs.claim(conn, t0 + timedelta(seconds=59), LEASE) is None  # not due yet

    t1 = t0 + timedelta(minutes=1)
    job = jobs.claim(conn, t1, LEASE)
    assert job.attempts == 2
    assert jobs.mark_failed(conn, job, t1, "HTTP 503") == "failed"
    assert job_row(conn, job_id)["run_after"] == t1 + timedelta(minutes=5)

    t2 = t1 + timedelta(minutes=5)
    job = jobs.claim(conn, t2, LEASE)
    assert job.attempts == 3
    assert jobs.mark_failed(conn, job, t2, "HTTP 503") == "dead"
    row = job_row(conn, job_id)
    assert row["status"] == "dead"
    assert row["error"] == "HTTP 503 (gave up after 3 tries)"


# ---------- one unfinished job per item ----------


def test_an_item_has_at_most_one_unfinished_job(conn: Conn, t0: datetime) -> None:
    item_id = new_note(conn)
    assert jobs.enqueue(conn, item_id) is not None
    assert jobs.enqueue(conn, item_id) is None  # queued: no second

    job = jobs.claim(conn, t0, LEASE)
    assert jobs.enqueue(conn, item_id) is None  # running: no second
    jobs.mark_failed(conn, job, t0, "HTTP 503")
    assert jobs.enqueue(conn, item_id) is None  # waiting to retry: no second

    job = jobs.claim(conn, t0 + timedelta(minutes=1), LEASE)
    jobs.mark_done(conn, job, t0 + timedelta(minutes=1))
    assert jobs.enqueue(conn, item_id) is not None  # finished: a fresh run is fine

    with pytest.raises(psycopg.errors.UniqueViolation):  # the database itself refuses
        conn.execute("INSERT INTO jobs (item_id) VALUES (%s)", (item_id,))
