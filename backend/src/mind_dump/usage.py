"""What model calls cost (D39): one row per call, a daily cap and a rate limit.

Every model client reports each call it makes as a `Usage` (tokens in and out) to a
sink. `UsageRecorder` is that sink: it prices the call from the table in settings and
writes a `model_usage` row in its own short transaction, so the row is kept even when
the work that paid for it fails or is rolled back.

Before an ask (POST /ask, POST /conversations/{id}/messages) the API runs two checks,
both BEFORE any model call:
    check_cost_cap  today's estimated spend (UTC day) against DAILY_COST_CAP_USD
    admit_ask       questions in the last 60 seconds against ASK_RATE_PER_MINUTE;
                    if under, it records this one (ask_requests)
Over either: LimitExceeded, which the API turns into 429 with Retry-After.

Costs are estimates from list prices kept by hand (config.DEFAULT_MODEL_PRICES), so they
drift when prices change; the cap is checked before a call, so one call can overshoot.
"""

import math
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from psycopg import Connection
from psycopg.rows import DictRow

from mind_dump.config import EMBEDDING_PROVIDERS, Price, Settings

Conn = Connection[DictRow]
MILLION = Decimal(1_000_000)
RATE_WINDOW = timedelta(seconds=60)
# Any constant works; it only has to be the same in every API process. ("mind" in ASCII)
ASK_LOCK_KEY = 0x6D696E64


@dataclass(frozen=True)
class Usage:
    """One model call's token counts (D39). purpose: answer, judge, describe, check,
    embed_documents or embed_query."""

    provider: str  # anthropic, openai, voyage, fake
    model: str  # the model that served the call (a refusal fallback can change it)
    purpose: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# What a model client calls after each call. Returns the model_usage row id, if any.
UsageSink = Callable[[Usage], int | None]


def price_for(usage: Usage, settings: Settings) -> Price:
    """The table's price for this model. An unknown Claude model gets the most expensive
    Claude price in the table, so the estimate errs toward the cap. An embedding call
    (OpenAI or Voyage) gets EMBEDDING_PRICE_PER_MTOK if set, else its model's price in
    config.DEFAULT_EMBEDDING_PRICES, else that table's dearest price (also toward the
    cap). Anything else (the test fakes) costs nothing."""
    if usage.model in settings.model_prices:
        return settings.model_prices[usage.model]
    if usage.provider in EMBEDDING_PROVIDERS:
        return Price(input=settings.embedding_price(usage.model))
    if usage.provider == "anthropic":
        claude = [p for m, p in settings.model_prices.items() if m.startswith("claude-")]
        if claude:
            return max(claude, key=lambda p: (p.output, p.input))
    return Price(input=Decimal(0))


def estimate_cost(usage: Usage, settings: Settings) -> Decimal:
    p = price_for(usage, settings)
    total = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_creation_input_tokens * p.cache_write
        + usage.cache_read_input_tokens * p.cache_read
    ) / MILLION
    return total.quantize(Decimal("0.000001"))


def record_usage(
    conn: Conn, usage: Usage, settings: Settings, conversation_id: int | None = None
) -> int:
    row = conn.execute(
        """
        INSERT INTO model_usage (provider, model, purpose, input_tokens, output_tokens,
                                 cache_creation_input_tokens, cache_read_input_tokens,
                                 cost_usd, conversation_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            usage.provider,
            usage.model,
            usage.purpose,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_input_tokens,
            usage.cache_read_input_tokens,
            estimate_cost(usage, settings),
            conversation_id,
        ),
    ).fetchone()
    return row["id"]


class UsageRecorder:
    """A UsageSink that writes each call in its own transaction.

    `connect` lends a connection: the API passes its pool's `connection` (commits on
    exit); the worker passes its autocommit connection (see for_connection)."""

    def __init__(
        self, connect: Callable[[], AbstractContextManager[Conn]], settings: Settings
    ) -> None:
        self._connect = connect
        self.settings = settings

    @classmethod
    def for_connection(cls, conn: Conn, settings: Settings) -> "UsageRecorder":
        """For an autocommit connection, used only outside transactions (the worker calls
        its models before its D30 transaction opens, so each row commits at once)."""
        return cls(lambda: nullcontext(conn), settings)

    def __call__(self, usage: Usage) -> int:
        with self._connect() as conn:
            return record_usage(conn, usage, self.settings)


# ---------- the checks before an ask ----------


class LimitExceeded(Exception):
    """Over the daily cap or the rate limit: the API answers 429."""

    def __init__(self, detail: str, retry_after: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after


def today_cost(conn: Conn) -> Decimal:
    """Estimated spend since 00:00 UTC today."""
    row = conn.execute(
        """
        SELECT coalesce(sum(cost_usd), 0) AS total FROM model_usage
        WHERE created_at >= date_trunc('day', now(), 'UTC')
        """
    ).fetchone()
    return Decimal(row["total"])


def check_cost_cap(conn: Conn, settings: Settings) -> None:
    row = conn.execute(
        """
        SELECT coalesce(sum(cost_usd), 0) AS total,
               extract(epoch FROM date_trunc('day', now(), 'UTC') + interval '1 day' - now())
                   AS to_midnight
        FROM model_usage
        WHERE created_at >= date_trunc('day', now(), 'UTC')
        """
    ).fetchone()
    total = Decimal(row["total"])
    cap = settings.daily_cost_cap_usd
    if total >= cap:
        raise LimitExceeded(
            f"Today's estimated model spend is ${total:.2f}, which has reached the daily cap "
            f"of ${cap:.2f} (DAILY_COST_CAP_USD). It resets at 00:00 UTC.",
            max(1, math.ceil(float(row["to_midnight"]))),
        )


def admit_ask(conn: Conn, settings: Settings, conversation_id: int | None = None) -> None:
    """Count the questions of the last 60 seconds and, if there is room, record this one.

    The count and the insert run under one transaction-level advisory lock: two API
    processes (or two threads) can't both read "9" and both insert the tenth and
    eleventh. The lock is released at COMMIT."""
    limit = settings.ask_rate_per_minute
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ASK_LOCK_KEY,))
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        recent = [
            r["created_at"]
            for r in conn.execute(
                "SELECT created_at FROM ask_requests WHERE created_at > %s ORDER BY created_at",
                (now - RATE_WINDOW,),
            ).fetchall()
        ]
        if len(recent) >= limit:
            # Room opens when enough of the oldest leave the window.
            frees_at = recent[len(recent) - limit] + RATE_WINDOW
            raise LimitExceeded(
                f"Too many questions: {len(recent)} in the last 60 seconds, the limit is "
                f"{limit} (ASK_RATE_PER_MINUTE). Try again shortly.",
                max(1, math.ceil((frees_at - now).total_seconds())),
            )
        conn.execute(
            "INSERT INTO ask_requests (created_at, conversation_id) VALUES (%s, %s)",
            (now, conversation_id),
        )


# ---------- GET /usage ----------


def _rows(conn: Conn, since_sql: str, by_day: bool) -> list[dict]:
    day = "(created_at AT TIME ZONE 'UTC')::date AS day," if by_day else ""
    group = "day, " if by_day else ""
    sql = f"""
        SELECT {day} provider, model, purpose, count(*) AS calls,
               sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
               sum(cache_creation_input_tokens) AS cache_creation_input_tokens,
               sum(cache_read_input_tokens) AS cache_read_input_tokens,
               sum(cost_usd) AS cost_usd
        FROM model_usage
        WHERE created_at >= {since_sql}
        GROUP BY {group} provider, model, purpose
        ORDER BY {group} provider, model, purpose
    """  # noqa: S608 (only constants are joined in)
    return [dict(r) for r in conn.execute(sql).fetchall()]


def summary(conn: Conn, settings: Settings) -> dict:
    """Today's (UTC) totals per model and purpose, and the last 7 days' per day."""
    today_start = "date_trunc('day', now(), 'UTC')"
    week_start = "date_trunc('day', now(), 'UTC') - interval '6 days'"
    today = _rows(conn, today_start, by_day=False)
    week = _rows(conn, week_start, by_day=True)
    today_date: date = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date AS d").fetchone()["d"]
    spent = sum((Decimal(r["cost_usd"]) for r in today), Decimal(0))
    return {
        "today": {
            "date": today_date,
            "cost_usd": spent,
            "cap_usd": settings.daily_cost_cap_usd,
            "remaining_usd": max(Decimal(0), settings.daily_cost_cap_usd - spent),
            "by_model": today,
        },
        "last_7_days": {
            "from": today_date - timedelta(days=6),
            "to": today_date,
            "cost_usd": sum((Decimal(r["cost_usd"]) for r in week), Decimal(0)),
            "by_day": week,
        },
    }
