"""The agent loop (D9) and its citation check (D38), on the real test database with a
scripted model: no network. E1 (the loop), E2 (the turn limit), E3 (citations) and E5
(Discuss scope) of M5's acceptance list.

A script step can be a callable that reads the request it is given: that is how these
tests prove the tools' results really reached the model.
"""

import json

import pytest
from chat_helpers import Conn, add_doc, context, count, drain, settings, tool_results

from mind_dump import agent
from mind_dump.agent import ANSWER_NOW, NOT_RUN, Scope, resolve_citations, run_turn
from mind_dump.embeddings import FakeEmbedder
from mind_dump.errors import TemporaryError
from mind_dump.llm import ScriptedLLM, TurnRequest, text_turn, tool_turn

BUFFER = "Between ten and twenty percent of credits go into the shared buffer pool."
SERIAL = "A serial number encodes the project, the vintage year and the batch."


@pytest.fixture
def docs(conn: Conn) -> dict:
    buffer_item, buffer_chunks = add_doc(
        conn, [BUFFER, "The pool covers reversals such as fire."], "Permanence", pages=[3, 4]
    )
    serial_item, serial_chunks = add_doc(conn, [SERIAL], "Registries")
    return {
        "buffer": buffer_item,
        "buffer_chunks": buffer_chunks,
        "serial": serial_item,
        "serial_chunks": serial_chunks,
    }


# ---------- E1: the loop ----------


def test_search_then_get_item_then_an_answer_with_real_results(conn: Conn, docs: dict) -> None:
    seen: dict = {}

    def after_search(request: TurnRequest) -> object:
        (result,) = tool_results(request.messages)
        hits = result["parsed"]["results"]
        assert hits[0]["text"] == BUFFER  # the real database's passage reached the model
        seen["chunk"] = hits[0]["chunk_id"]
        return tool_turn(("get_item", {"item_id": hits[0]["item_id"]}))

    def after_get_item(request: TurnRequest) -> object:
        (result,) = tool_results(request.messages)
        item = result["parsed"]
        assert item["item"]["title"] == "Permanence"
        assert [c["text"] for c in item["chunks"]][0] == BUFFER
        return text_turn(f"Ten to twenty percent go to the buffer pool. [c:{seen['chunk']}]")

    llm = ScriptedLLM(
        [tool_turn(("search", {"query": "buffer pool"})), after_search, after_get_item]
    )
    events, result = drain(run_turn(llm, context(conn), [], {}, "How much goes to the buffer?"))

    assert len(llm.requests) == 3
    assert result.grounded and not result.incomplete and result.stop_reason == "end_turn"
    (citation,) = result.citations
    assert citation["chunk_id"] == seen["chunk"] == docs["buffer_chunks"][0]
    assert (citation["item_id"], citation["page"], citation["quote"]) == (docs["buffer"], 3, BUFFER)
    assert ("status", {"state": "searching", "query": "buffer pool"}) in events
    assert ("status", {"state": "reading", "item_id": docs["buffer"]}) in events
    # The loop added: assistant(search), user(results), assistant(get_item), user(results),
    # assistant(answer). The tools' JSON is what the model saw.
    assert [m["role"] for m in result.api_messages] == ["assistant", "user"] * 2 + ["assistant"]


def test_two_tool_calls_in_one_turn_are_both_run_and_answered_in_one_message(
    conn: Conn, docs: dict
) -> None:
    two = tool_turn(
        ("search", {"query": "serial number"}), ("get_item", {"item_id": docs["buffer"]})
    )
    llm = ScriptedLLM([two, text_turn("Done.")])
    drain(run_turn(llm, context(conn), [], {}, "q"))

    second = llm.requests[1].messages
    assert second[-2] == {"role": "assistant", "content": two.content}
    results = tool_results(second)  # the LAST message holds both results...
    assert [r["tool_use_id"] for r in results] == [
        b["id"] for b in two.content if b["type"] == "tool_use"
    ]
    assert second[-1]["role"] == "user" and len(second[-1]["content"]) == 2  # ...and only them
    assert results[0]["parsed"]["results"][0]["text"] == SERIAL
    assert results[1]["parsed"]["item"]["item_id"] == docs["buffer"]
    assert not any(r.get("is_error") for r in results)


def test_a_tool_that_fails_returns_an_error_result_and_the_loop_goes_on(
    conn: Conn, docs: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("database on fire")

    monkeypatch.setitem(agent.TOOL_RUNNERS, "search", broken)
    llm = ScriptedLLM(
        [
            tool_turn(("search", {"query": "x"}), ("get_item", {"item_id": 999})),
            tool_turn(("get_item", {"item_id": "seven"}), ("nonsense", {})),
            text_turn("The items don't cover this."),
        ]
    )
    _, result = drain(run_turn(llm, context(conn), [], {}, "q"))

    first = tool_results(llm.requests[1].messages)
    assert all(r["is_error"] for r in first)
    assert "database on fire" in first[0]["content"]
    assert "no item 999" in first[1]["content"]
    second = tool_results(llm.requests[2].messages)
    assert "positive whole number" in second[0]["content"]
    assert "Unknown tool" in second[1]["content"]
    assert result.text == "The items don't cover this." and not result.grounded


# ---------- E2: the turn limit ----------


def test_a_model_that_only_calls_tools_is_stopped_and_marked_incomplete(
    conn: Conn, docs: dict
) -> None:
    cfg = settings(agent_max_turns=3)
    llm = ScriptedLLM([tool_turn(("search", {"query": "pool"})) for _ in range(20)])
    _, result = drain(run_turn(llm, context(conn, cfg), [], {}, "q"))

    assert len(llm.requests) == 4  # 3 with tools, then one "answer now" with tools off
    assert [r.final for r in llm.requests] == [False, False, False, True]
    # The third results message carries the note to answer now, after the results.
    assert llm.requests[3].messages[-1]["content"][-1] == {"type": "text", "text": ANSWER_NOW}
    assert result.incomplete and not result.grounded
    # The history never ends on an unanswered tool call: the last call is closed.
    closing = result.api_messages[-1]
    assert closing["role"] == "user"
    assert all(b["is_error"] and b["content"] == NOT_RUN for b in closing["content"])
    assert result.usage["model_calls"] == 4
    assert count(conn, "model_usage") == 4  # one row per call made
    assert len(result.usage_ids) == 4


def test_at_the_limit_a_model_that_answers_is_complete(conn: Conn, docs: dict) -> None:
    def answer(request: TurnRequest) -> object:
        assert request.final  # the second call is the last one: tools off
        return text_turn(f"Ten to twenty percent. [c:{docs['buffer_chunks'][0]}]")

    llm = ScriptedLLM([tool_turn(("search", {"query": "buffer"})), answer])
    _, result = drain(run_turn(llm, context(conn, settings(agent_max_turns=1)), [], {}, "q"))
    assert llm.requests[1].final
    assert not result.incomplete and result.grounded


# ---------- E3: citations ----------


def test_only_passages_handed_to_the_model_count_as_citations(conn: Conn, docs: dict) -> None:
    returned = docs["buffer_chunks"][0]
    not_returned = docs["serial_chunks"][0]  # exists, but no tool returned it
    text = (
        f"Ten to twenty percent go to the pool [c:{returned}]. Serials encode the year "
        f"[c:{not_returned}]. And something else [c:999999]. Again [c:{returned}]."
    )
    llm = ScriptedLLM([tool_turn(("search", {"query": "buffer pool"})), text_turn(text)])
    _, result = drain(run_turn(llm, context(conn), [], {}, "q"))

    assert result.grounded
    assert result.citations == [
        {
            "marker": f"[c:{returned}]",
            "chunk_id": returned,
            "item_id": docs["buffer"],
            "type": "note",
            "title": "Permanence",
            "url": None,
            "page": 3,
            "start_char": 0,
            "end_char": len(BUFFER),
            "quote": BUFFER,
        }
    ]
    assert result.invalid_citations == [
        {"marker": f"[c:{not_returned}]", "chunk_id": not_returned, "reason": "not_retrieved"},
        {"marker": "[c:999999]", "chunk_id": 999999, "reason": "no_such_chunk"},
    ]
    assert f"[c:{not_returned}]" not in result.text and "[c:999999]" not in result.text
    assert "Serials encode the year." in result.text  # the sentence stays, tidied
    assert result.text.count(f"[c:{returned}]") == 2


def test_an_answer_with_no_valid_citation_is_not_grounded(conn: Conn, docs: dict) -> None:
    llm = ScriptedLLM([text_turn(f"From memory: twenty percent [c:{docs['buffer_chunks'][0]}].")])
    _, result = drain(run_turn(llm, context(conn), [], {}, "q"))
    assert not result.grounded and result.citations == []
    assert result.invalid_citations[0]["reason"] == "not_retrieved"
    assert result.text == "From memory: twenty percent."


def test_resolve_citations_orders_and_dedupes() -> None:
    handed = {
        n: {"item_id": 1, "type": "note", "title": "t", "url": None, "page": None,
            "start_char": 0, "end_char": 1, "text": "x"}
        for n in (5, 9)
    }  # fmt: skip
    text, cites, bad = resolve_citations("a [c:9] b [c:5][c:9] c [c:7]", handed, lambda ids: set())
    assert [c["chunk_id"] for c in cites] == [9, 5]
    assert bad == [{"marker": "[c:7]", "chunk_id": 7, "reason": "no_such_chunk"}]
    assert text == "a [c:9] b [c:5][c:9] c"


# ---------- E5: Discuss scope ----------


@pytest.fixture
def discuss(conn: Conn) -> dict:
    passages = [f"Paragraph {i} about mangrove planting and soil." for i in range(5)]
    target, chunks = add_doc(conn, passages, "Field notes")
    # Another item that matches every query far better, and is a link.
    other, other_chunks = add_doc(
        conn, ["Buffer pool buffer pool buffer pool mangrove mangrove."], "Other", type_="link"
    )
    # The scope points at paragraph 2 by its position, as a citation would.
    row = conn.execute("SELECT start_char, end_char FROM chunks WHERE id = %s", (chunks[2],))
    pos = row.fetchone()
    scope = Scope(target, "Field notes", pos["start_char"], pos["end_char"], None, passages[2])
    return {"target": target, "chunks": chunks, "other": other, "scope": scope,
            "passages": passages, "other_chunks": other_chunks}  # fmt: skip


def test_a_scoped_search_sees_only_its_item_whatever_the_model_asks(
    conn: Conn, discuss: dict
) -> None:
    llm = ScriptedLLM(
        [
            # Both items hold the word; the other one holds it twice, and is a link.
            tool_turn(("search", {"query": "mangrove", "type": "link"})),
            tool_turn(("get_item", {"item_id": discuss["other"]})),
            text_turn("Done."),
        ]
    )
    ctx = context(conn, scope=discuss["scope"], embedder=FakeEmbedder())
    drain(run_turn(llm, ctx, [], {}, "What about the pool?"))

    (search,) = tool_results(llm.requests[1].messages)
    found = search["parsed"]["results"]
    assert found and {r["item_id"] for r in found} == {discuss["target"]}
    assert "limited to item" in search["parsed"]["notice"] and "type" in search["parsed"]["notice"]
    (other,) = tool_results(llm.requests[2].messages)
    assert other["is_error"] and f"about item {discuss['target']} only" in other["content"]


def test_the_first_request_holds_the_passage_and_its_neighbours(conn: Conn, discuss: dict) -> None:
    chunks, passages = discuss["chunks"], discuss["passages"]
    llm = ScriptedLLM([text_turn(f"It says paragraph 2. [c:{chunks[2]}] [c:{chunks[1]}]")])
    _, result = drain(run_turn(llm, context(conn, scope=discuss["scope"]), [], {}, "Explain"))

    first = llm.requests[0].messages
    assert len(first) == 1
    context_block, question = first[0]["content"]
    assert question == {"type": "text", "text": "Explain"}
    text = context_block["text"]
    for i in (1, 2, 3):  # the cited passage and the ones just before and after it
        assert f'<passage chunk_id="{chunks[i]}">\n{passages[i]}\n</passage>' in text
    for i in (0, 4):
        assert passages[i] not in text
    assert text.index(passages[2]) < text.index(passages[1])  # the cited one first
    # Passages our code handed over can be cited, like a tool's.
    assert [c["chunk_id"] for c in result.citations] == [chunks[2], chunks[1]]


def test_tool_inputs_are_checked(conn: Conn, docs: dict) -> None:
    llm = ScriptedLLM(
        [
            tool_turn(
                ("search", {"query": "  "}),
                ("search", {"query": "pool", "from": "last week"}),
                ("search", {"query": "pool", "type": "video"}),
                ("get_item", {"item_id": docs["buffer"], "from_chunk": -1}),
            ),
            text_turn("ok"),
        ]
    )
    drain(run_turn(llm, context(conn), [], {}, "q"))
    results = tool_results(llm.requests[1].messages)
    assert all(r["is_error"] for r in results)
    assert json.dumps([r["content"] for r in results]).count("must") == 4


class _FailingEmbedder:
    model = "text-embedding-3-small"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise TemporaryError("OpenAI answered HTTP 503 Service Unavailable")

    def embed_query(self, text: str) -> list[float]:
        raise TemporaryError("OpenAI answered HTTP 503 Service Unavailable")


NAMES_THE_MODEL_MUST_NOT_SEE = (
    "OPENAI_API_KEY", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY", "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL", "_API_KEY", "OpenAI", "Voyage", "openai", "voyage", ".env",
)  # fmt: skip


@pytest.mark.parametrize("provider", ["openai", "voyage"])
@pytest.mark.parametrize("embedder", [None, _FailingEmbedder()], ids=["no embedder", "failing"])
def test_a_words_only_search_tells_the_model_no_setting_or_provider(
    conn: Conn, provider: str, embedder: object
) -> None:
    """The API's notice names the key for the owner; the tool result the model reads
    says only that the passages matched by words."""
    add_doc(conn, ["The buffer pool holds a share of the credits."])
    llm = ScriptedLLM([tool_turn(("search", {"query": "buffer pool"})), text_turn("Done.")])
    cfg = settings(embedding_provider=provider, embedding_model="")
    drain(run_turn(llm, context(conn, cfg, embedder=embedder), [], {}, "What is the pool?"))

    (search,) = tool_results(llm.requests[1].messages)
    assert search["parsed"]["results"]  # found by words
    assert search["parsed"]["notice"] == agent.WORDS_ONLY
    for name in NAMES_THE_MODEL_MUST_NOT_SEE:
        assert name not in search["content"], name
