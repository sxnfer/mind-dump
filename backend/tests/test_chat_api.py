"""Ask and Discuss over HTTP (M5): conversations, history replay, streaming, failures,
the cost cap and the rate limit, usage. The model is a ScriptedLLM set on the app, as if
ANTHROPIC_API_KEY were set: no network.

E4 (snapshots), E5 (Discuss over HTTP), E6 (history), E7 (streaming), E8 (cap and rate
limit), E9 (failures) and E11 (GET /usage) of M5's acceptance list.
"""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from chat_helpers import Conn, add_doc, add_usage, context, count, rechunk, sse_events
from fastapi.testclient import TestClient

from mind_dump import conversations as convs
from mind_dump.agent import Context
from mind_dump.errors import PermanentError, TemporaryError
from mind_dump.llm import FailAfter, ScriptedLLM, text_turn, tool_turn

BUFFER = "Between ten and twenty percent of credits go into the shared buffer pool."


@pytest.fixture
def llm(client: TestClient) -> ScriptedLLM:
    fake = ScriptedLLM([])
    client.app.state.llm = fake
    return fake


@pytest.fixture
def doc(conn: Conn) -> dict:
    item, chunks = add_doc(
        conn, ["Intro to permanence.", BUFFER, "Fire can reverse storage."], "Permanence",
        pages=[1, 2, 2],
    )  # fmt: skip
    return {"item": item, "chunks": chunks}


def cited_answer(doc: dict) -> list:
    return [
        tool_turn(("search", {"query": "buffer pool"})),
        text_turn(f"Ten to twenty percent go to the buffer pool. [c:{doc['chunks'][1]}]"),
    ]


def set_settings(client: TestClient, **changes: object) -> None:
    client.app.state.settings = replace(client.app.state.settings, **changes)


def stored_api_messages(conn: Conn, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT api_messages::text AS raw FROM messages WHERE conversation_id = %s ORDER BY seq",
        (conversation_id,),
    ).fetchall()
    return [m for r in rows for m in json.loads(r["raw"])]


# ---------- asking ----------


def test_ask_answers_with_citations_and_saves_the_turn(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = cited_answer(doc)
    r = client.post("/ask", json={"question": "How much goes to the buffer pool?"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["grounded"] and not answer["incomplete"]
    (c,) = answer["citations"]
    assert (c["chunk_id"], c["item_id"], c["page"], c["quote"]) == (
        doc["chunks"][1], doc["item"], 2, BUFFER
    )  # fmt: skip
    assert answer["usage"]["model_calls"] == 2
    detail = client.get(f"/conversations/{answer['conversation_id']}").json()
    assert detail["title"] == "How much goes to the buffer pool?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"] == answer["citations"]
    # The usage rows are tied to the conversation once it exists.
    rows = conn.execute("SELECT conversation_id FROM model_usage").fetchall()
    assert [r["conversation_id"] for r in rows] == [answer["conversation_id"]] * 2


# ---------- E4: snapshots outlive the chunk and the item ----------


def test_old_answers_keep_their_sources_after_a_rerun_and_a_delete(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = cited_answer(doc)
    answer = client.post("/ask", json={"question": "Buffer?"}).json()
    cid = answer["conversation_id"]
    before = answer["citations"][0]

    new_ids = rechunk(conn, doc["item"])  # re-processed: same text, new chunk ids
    assert doc["chunks"][1] not in new_ids
    after_rerun = client.get(f"/conversations/{cid}").json()["messages"][1]["citations"][0]
    assert after_rerun == {**before, "chunk_id": None}  # the quote and positions stay
    assert after_rerun["marker"] == f"[c:{doc['chunks'][1]}]"

    assert client.delete(f"/items/{doc['item']}").status_code == 204
    after_delete = client.get(f"/conversations/{cid}").json()["messages"][1]
    assert after_delete["citations"] == [{**before, "chunk_id": None}]
    assert after_delete["text"] == answer["text"]


# ---------- E5: Discuss over HTTP ----------


def test_discuss_by_chunk_or_position_and_after_the_item_is_deleted(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    other, _ = add_doc(conn, ["buffer pool buffer pool buffer pool"], "Elsewhere")
    r = client.post("/conversations", json={"item_id": doc["item"], "chunk_id": doc["chunks"][1]})
    assert r.status_code == 201, r.text
    conv = r.json()
    assert conv["scope"]["text"] == BUFFER and conv["scope"]["page"] == 2
    assert conv["title"] == "About: Permanence"

    llm.steps = [tool_turn(("search", {"query": "buffer pool", "tag": "x"})), text_turn("ok")]
    r = client.post(f"/conversations/{conv['id']}/messages", json={"text": "Simpler please"})
    assert r.status_code == 200, r.text
    first_request = llm.requests[0].messages[0]["content"][0]["text"]
    assert BUFFER in first_request and "Intro to permanence." in first_request
    results = json.loads(llm.requests[1].messages[-1]["content"][0]["content"])["results"]
    assert {h["item_id"] for h in results} == {doc["item"]}  # never the better-matching other
    assert other not in {h["item_id"] for h in results}

    # By position, after a re-run gave the passage a new chunk id.
    rechunk(conn, doc["item"])
    by_chunk = client.post(
        "/conversations", json={"item_id": doc["item"], "chunk_id": doc["chunks"][1]}
    )
    assert by_chunk.status_code == 422 and "start_char" in by_chunk.json()["detail"]
    start = len("Intro to permanence.") + 2
    by_position = client.post("/conversations", json={"item_id": doc["item"], "start_char": start})
    assert by_position.status_code == 201 and by_position.json()["scope"]["text"] == BUFFER

    client.delete(f"/items/{doc['item']}")
    gone = client.get(f"/conversations/{conv['id']}").json()
    assert gone["scope"]["item_exists"] is False and len(gone["messages"]) == 2
    r = client.post(f"/conversations/{conv['id']}/messages", json={"text": "More?"})
    assert r.status_code == 409 and "deleted" in r.json()["detail"]
    assert client.post("/conversations", json={"item_id": doc["item"]}).status_code == 404


# ---------- E6: history is replayed exactly and only appended ----------


def test_a_follow_up_replays_the_stored_blocks_unchanged(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = cited_answer(doc)
    cid = client.post("/ask", json={"question": "Buffer?"}).json()["conversation_id"]
    first_turn_last_request = llm.requests[-1].messages
    stored_after_one = stored_api_messages(conn, cid)

    llm.steps = [text_turn(f"Still ten to twenty. [c:{doc['chunks'][1]}]")]
    r = client.post(f"/conversations/{cid}/messages", json={"text": "Say it again?"})
    assert r.status_code == 200, r.text
    assert r.json()["grounded"]  # a passage from an earlier turn may be cited again
    follow_up = llm.requests[-1].messages

    # Byte for byte (key order included): the stored history, then the new message.
    dumps = lambda x: json.dumps(x)  # noqa: E731
    assert dumps(follow_up[:-1]) == dumps(stored_after_one)
    assert follow_up[-1] == {"role": "user", "content": [{"type": "text", "text": "Say it again?"}]}
    # Append-only: the earlier request is a prefix of the stored history, which ends with
    # the answer; thinking blocks with their signatures came back untouched.
    assert dumps(stored_after_one[: len(first_turn_last_request)]) == dumps(first_turn_last_request)
    assert stored_after_one[-1]["role"] == "assistant"
    assert stored_after_one[-1]["content"][0]["type"] == "thinking"
    assert stored_api_messages(conn, cid)[: len(stored_after_one)] == stored_after_one


# ---------- E7: streaming ----------


def without_ids(answer: dict) -> dict:
    return {k: v for k, v in answer.items() if k not in ("conversation_id", "message_id")}


def test_streaming_sends_status_deltas_and_the_same_final_answer(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = cited_answer(doc)
    plain = client.post("/ask", json={"question": "Buffer?"}).json()

    llm.steps = cited_answer(doc)
    r = client.post("/ask?stream=true", json={"question": "Buffer?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds[-1] == "final" and "delta" in kinds
    assert kinds.index("status") < kinds.index("delta") < kinds.index("final")
    assert ("status", {"state": "searching", "query": "buffer pool"}) in events
    streamed_text = "".join(d["text"] for k, d in events if k == "delta")
    final = events[-1][1]
    assert streamed_text == final["text"]
    assert without_ids(final) == without_ids(plain)
    stored = client.get(f"/conversations/{final['conversation_id']}").json()["messages"][-1]
    assert (stored["id"], stored["text"], stored["citations"]) == (
        final["message_id"], final["text"], final["citations"]
    )  # fmt: skip

    # The Accept header works too, on a follow-up message.
    llm.steps = [text_turn("Again.")]
    r = client.post(
        f"/conversations/{final['conversation_id']}/messages",
        json={"text": "And?"},
        headers={"accept": "text/event-stream"},
    )
    assert sse_events(r.text)[-1][0] == "final"


def test_a_failure_mid_stream_sends_an_error_and_saves_nothing(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = [
        tool_turn(("search", {"query": "buffer"})),
        FailAfter("Ten to tw", TemporaryError("The Claude API answered HTTP 529: overloaded")),
    ]
    r = client.post("/ask", params={"stream": "true"}, json={"question": "Buffer?"})
    events = sse_events(r.text)
    assert "".join(d["text"] for k, d in events if k == "delta") == "Ten to tw"
    kind, error = events[-1]
    assert kind == "error" and error["status"] == 503 and "529" in error["detail"]
    assert "final" not in [k for k, _ in events]
    assert count(conn, "conversations") == 0 and count(conn, "messages") == 0
    assert count(conn, "model_usage") == 1  # the call that finished was paid for


def test_a_client_that_leaves_mid_stream_leaves_no_message(conn: Conn, doc: dict) -> None:
    llm = ScriptedLLM(cited_answer(doc))
    cid = convs.create_conversation(conn, "t", None)
    ctx: Context = context(conn)
    events = convs.answer_events(llm, ctx, "Buffer?", conversation_id=cid)
    seen = []
    for kind, _ in events:
        seen.append(kind)
        if kind == "delta":
            break
    events.close()  # what Starlette does when the client disconnects
    assert "delta" in seen
    assert count(conn, "messages") == 0


# ---------- E8: the cost cap and the rate limit ----------


def test_over_the_cap_is_refused_before_any_model_call(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    set_settings(client, daily_cost_cap_usd=Decimal("1.50"))
    add_usage(conn, "1.00")
    add_usage(conn, "0.40", provider="voyage", model="voyage-4", purpose="embed_documents")
    add_usage(conn, "9.00", days_ago=1)  # yesterday's spend doesn't count
    llm.steps = cited_answer(doc)
    assert client.post("/ask", json={"question": "Buffer?"}).status_code == 200  # 1.40 < 1.50

    add_usage(conn, "0.10", provider="voyage", model="voyage-4", purpose="embed_query")
    r = client.post("/ask", json={"question": "Again?"})
    assert r.status_code == 429
    assert "DAILY_COST_CAP_USD" in r.json()["detail"] and int(r.headers["retry-after"]) >= 1
    assert len(llm.requests) == 2  # only the first ask's two calls
    cid = client.get("/conversations").json()["conversations"][0]["id"]
    r = client.post(f"/conversations/{cid}/messages", json={"text": "More?"})
    assert r.status_code == 429 and len(llm.requests) == 2


def test_the_rate_limit_answers_429_with_retry_after(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    set_settings(client, ask_rate_per_minute=2)
    llm.steps = [text_turn("a"), PermanentError("boom")]
    assert client.post("/ask", json={"question": "one"}).status_code == 200
    assert client.post("/ask", json={"question": "two"}).status_code == 502  # failures count
    r = client.post("/ask", json={"question": "three"})
    assert r.status_code == 429
    assert "ASK_RATE_PER_MINUTE" in r.json()["detail"]
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert len(llm.requests) == 2
    # Once the window has passed, there is room again.
    conn.execute("UPDATE ask_requests SET created_at = created_at - interval '61 seconds'")
    llm.steps = [text_turn("b")]
    assert client.post("/ask", json={"question": "four"}).status_code == 200


# ---------- E9: failures leave nothing half-saved ----------


def test_no_key_is_503_naming_the_setting(client: TestClient) -> None:
    assert client.app.state.llm is None  # tests run with no ANTHROPIC_API_KEY
    r = client.post("/ask", json={"question": "Anything?"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    cid = client.post("/conversations", json={}).json()["id"]
    r = client.post(f"/conversations/{cid}/messages", json={"text": "Hi"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize(
    ("step", "code", "words"),
    [
        (text_turn("", "refusal", stop_details={"category": "cyber"}), 422, "declined"),
        (text_turn("Half an ans", "max_tokens"), 502, "cut off"),
        (TemporaryError("Can't reach the Claude API: timed out"), 503, "try again"),
        (PermanentError("The Claude API refused the request (HTTP 400): bad"), 502, "HTTP 400"),
    ],
    ids=["refusal", "max_tokens", "temporary", "permanent"],
)
def test_a_failed_follow_up_saves_nothing(
    client: TestClient, conn: Conn, llm: ScriptedLLM, step: object, code: int, words: str
) -> None:
    llm.steps = [text_turn("First answer.")]
    cid = client.post("/ask", json={"question": "First?"}).json()["conversation_id"]
    before = stored_api_messages(conn, cid)

    llm.steps = [tool_turn(("search", {"query": "x"})), step]
    r = client.post(f"/conversations/{cid}/messages", json={"text": "Second?"})
    assert r.status_code == code and words in r.json()["detail"]
    assert "Nothing was saved" in r.json()["detail"] or code == 502
    assert count(conn, "messages") == 2  # neither the question nor a partial answer
    assert stored_api_messages(conn, cid) == before


def test_a_failed_first_ask_creates_no_conversation(
    client: TestClient, conn: Conn, llm: ScriptedLLM
) -> None:
    llm.steps = [TemporaryError("The Claude API answered HTTP 529: overloaded")]
    r = client.post("/ask", json={"question": "Q?"})
    assert r.status_code == 503
    assert count(conn, "conversations") == 0


# ---------- the rest of the API ----------


def test_list_get_delete_and_bad_input(
    client: TestClient, conn: Conn, llm: ScriptedLLM, doc: dict
) -> None:
    llm.steps = [text_turn("a"), text_turn("b")]
    first = client.post("/ask", json={"question": "One", "title": "Mine"}).json()
    second = client.post("/ask", json={"question": "Two", "type": "note", "from": "2026-01-01"})
    page = client.get("/conversations?limit=1").json()
    assert [c["id"] for c in page["conversations"]] == [second.json()["conversation_id"]]
    assert page["conversations"][0]["filters"]["type"] == "note"
    older = client.get(f"/conversations?before={page['next_before']}").json()
    assert older["conversations"][0]["title"] == "Mine"

    assert client.delete(f"/conversations/{first['conversation_id']}").status_code == 204
    assert client.get(f"/conversations/{first['conversation_id']}").status_code == 404
    assert client.delete(f"/conversations/{first['conversation_id']}").status_code == 404
    assert count(conn, "model_usage") == 2  # spent is spent: usage rows stay

    assert client.post("/ask", json={"question": "  "}).status_code == 422
    assert client.post("/ask", json={"question": "q", "from": "soon"}).status_code == 422
    assert client.post("/conversations/999/messages", json={"text": "x"}).status_code == 404
    assert client.post("/conversations", json={"chunk_id": 1}).status_code == 422


# ---------- E11: GET /usage ----------


def test_usage_sums_by_day_model_and_purpose(client: TestClient, conn: Conn) -> None:
    add_usage(conn, "0.50")
    add_usage(conn, "0.25")
    add_usage(conn, "0.01", provider="voyage", model="voyage-4", purpose="embed_query")
    add_usage(conn, "1.00", days_ago=3, purpose="describe")
    add_usage(conn, "5.00", days_ago=10)  # outside the 7 days
    report = client.get("/usage").json()

    today = report["today"]
    assert today["cost_usd"] == pytest.approx(0.76)
    assert today["cap_usd"] == 2.0 and today["remaining_usd"] == pytest.approx(1.24)
    rows = {(r["model"], r["purpose"]): r for r in today["by_model"]}
    assert rows[("claude-opus-5-5", "answer")]["calls"] == 2
    assert rows[("claude-opus-5-5", "answer")]["cost_usd"] == pytest.approx(0.75)
    assert rows[("claude-opus-5-5", "answer")]["input_tokens"] == 20
    assert rows[("voyage-4", "embed_query")]["cost_usd"] == pytest.approx(0.01)

    week = report["last_7_days"]
    assert week["cost_usd"] == pytest.approx(1.76)
    assert {r["purpose"] for r in week["by_day"]} == {"answer", "embed_query", "describe"}
    assert len({r["day"] for r in week["by_day"]}) == 2
    assert set(week) == {"from", "to", "cost_usd", "by_day"}
