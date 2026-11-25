"""Model usage and cost (D39): every kind of model call writes a priced row, and the
worker's calls count toward the day's cap. E8 and E11 of M5's acceptance list (the HTTP
side, 429s and GET /usage, is in test_chat_api.py). No network.
"""

import json
from decimal import Decimal

import anthropic
import httpx2
import pytest
from chat_helpers import Conn, add_usage, settings
from fastapi.testclient import TestClient
from test_m3_worker import (  # noqa: F401 (pytest fixtures)
    clock,
    describer,
    embedder,
    png,
    site,
    upload,
    worker,
)
from test_worker import save_note

from mind_dump.config import Price, _prices
from mind_dump.describe import ClaudeDescriber, FakeDescriber
from mind_dump.embeddings import FakeEmbedder, VoyageEmbedder
from mind_dump.usage import (
    LimitExceeded,
    Usage,
    UsageRecorder,
    admit_ask,
    check_cost_cap,
    estimate_cost,
    today_cost,
)
from mind_dump.worker import Worker


def rows(conn: Conn) -> list[dict]:
    return conn.execute(
        "SELECT provider, model, purpose, input_tokens, output_tokens, cost_usd"
        " FROM model_usage ORDER BY id"
    ).fetchall()


def test_cost_estimates_from_the_price_table() -> None:
    cfg = settings()
    opus = Usage("anthropic", "claude-opus-5-5", "answer", 1_000_000, 100_000, 200_000, 2_000_000)
    # $4 in + $2 out + $1 cache write + $0.40 cache read
    assert estimate_cost(opus, cfg) == Decimal("7.400000")
    # An unknown Claude model is priced at the dearest Claude price in the table.
    unknown = Usage("anthropic", "claude-something-new", "answer", 1_000_000)
    assert estimate_cost(unknown, cfg) == Decimal("10.000000")
    voyage = Usage("voyage", "voyage-4", "embed_documents", 2_000_000)
    assert estimate_cost(voyage, settings(voyage_price_per_mtok=Decimal("0.5"))) == Decimal("1")
    assert estimate_cost(Usage("fake", "fake-llm", "answer", 10**9), cfg) == 0


def test_model_prices_json_overrides_entries() -> None:
    prices = _prices('{"claude-opus-5-5": {"input": 3, "output": 15}, "x": {"input": "1.5"}}')
    assert prices["claude-opus-5-5"] == Price(Decimal(3), Decimal(15))
    assert prices["x"].input == Decimal("1.5")
    assert prices["claude-opus-5"].input == Decimal(5)  # the rest stay


def test_the_worker_s_embedding_and_description_calls_count_toward_the_cap(
    client: TestClient,
    conn: Conn,
    worker: Worker,  # noqa: F811
) -> None:
    cfg = settings(
        # Neither kind of call alone reaches it: both must count.
        daily_cost_cap_usd=Decimal("0.60"),
        model_prices={
            "fake-embedder": Price(Decimal(10_000)),  # dear, so a few words cost cents
            "fake-describer": Price(Decimal(500), Decimal(1000)),
        },
    )
    record = UsageRecorder.for_connection(conn, cfg)
    worker.embedder = FakeEmbedder(on_usage=record)
    worker.describer = FakeDescriber("A chart of credits issued per quarter.", on_usage=record)
    save_note(conn, "Twenty words or so about mangroves.")
    upload(client, png())
    while worker.run_once() is not None:
        pass

    got = rows(conn)
    assert {(r["model"], r["purpose"]) for r in got} == {
        ("fake-embedder", "embed_documents"),
        ("fake-describer", "describe"),
    }
    describe = next(r for r in got if r["purpose"] == "describe")
    assert (describe["input_tokens"], describe["output_tokens"]) == (1000, 7)
    assert describe["cost_usd"] == Decimal("0.507000")  # 1000 x $500/M + 7 x $1000/M
    assert all(r["cost_usd"] > 0 for r in got)
    assert today_cost(conn) == sum(r["cost_usd"] for r in got) == Decimal("0.637")
    with pytest.raises(LimitExceeded, match="DAILY_COST_CAP_USD"):
        check_cost_cap(conn, cfg)
    check_cost_cap(conn, settings(daily_cost_cap_usd=Decimal("100")))  # under: no error


def test_voyage_reports_its_own_token_counts(conn: Conn) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        n = len(json.loads(request.content)["input"])
        data = [{"index": i, "embedding": [0.0] * 1024} for i in range(n)]
        return httpx2.Response(200, json={"data": data, "usage": {"total_tokens": 7 * n}})

    cfg = settings(voyage_price_per_mtok=Decimal("1000"))  # a round price, not the default
    record = UsageRecorder.for_connection(conn, cfg)
    http = httpx2.Client(transport=httpx2.MockTransport(answer))
    voyage = VoyageEmbedder("key", "voyage-4", http=http, batch_size=2, on_usage=record)
    voyage.embed_documents(["a", "b", "c"])
    voyage.embed_query("q")
    got = [(r["purpose"], r["input_tokens"]) for r in rows(conn)]
    assert got == [("embed_documents", 14), ("embed_documents", 7), ("embed_query", 7)]
    # VOYAGE_PRICE_PER_MTOK applies: 7 tokens x $1000/M.
    assert rows(conn)[-1]["cost_usd"] == Decimal("0.007000")


def test_a_claude_description_records_the_response_usage(conn: Conn) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "A chart."}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 1500, "output_tokens": 50},
            },
        )  # fmt: skip

    client = anthropic.Anthropic(
        api_key="sk-ant-test",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(answer)),
        max_retries=0,
    )
    described = ClaudeDescriber(
        client, "claude-opus-5-5", UsageRecorder.for_connection(conn, settings())
    )
    described.describe(png(), "image/png")
    described.check()
    got = rows(conn)
    assert [(r["purpose"], r["input_tokens"], r["output_tokens"]) for r in got] == [
        ("describe", 1500, 50),
        ("check", 1500, 50),
    ]
    assert got[0]["cost_usd"] == Decimal("0.007000")  # 1500 x $4/M + 50 x $20/M


def test_the_rate_limit_counts_the_last_60_seconds(conn: Conn) -> None:
    cfg = settings(ask_rate_per_minute=3)
    for _ in range(3):
        admit_ask(conn, cfg)
    with pytest.raises(LimitExceeded) as caught:
        admit_ask(conn, cfg)
    assert 1 <= caught.value.retry_after <= 60
    assert conn.execute("SELECT count(*) AS n FROM ask_requests").fetchone()["n"] == 3
    conn.execute("UPDATE ask_requests SET created_at = created_at - interval '2 minutes'")
    admit_ask(conn, cfg)  # the window moved on


def test_the_cap_counts_only_today_utc(conn: Conn) -> None:
    add_usage(conn, "1.99")
    add_usage(conn, "50", days_ago=1)
    check_cost_cap(conn, settings())  # 1.99 < 2.00
    add_usage(conn, "0.01")
    with pytest.raises(LimitExceeded) as caught:
        check_cost_cap(conn, settings())
    assert 1 <= caught.value.retry_after <= 86_400  # until midnight UTC
