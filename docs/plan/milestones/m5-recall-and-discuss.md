# M5: Recall and discuss

> Read this when: you work on M5, or need its tasks and done-when list.

**Goal:** answers with citations, and a chat about one citation.
**Decisions needed:** none left. (Approved: D9 agent loop, D21 chat history, D38 citations and grounding, D39 cost cap and rate limit.)
**Status:** built on branch `m5-recall` (stage 4). The answers eval runs end to end with a scripted model in the tests; its real numbers wait for a run with `ANTHROPIC_API_KEY` (`docs/results.md` says pending).
**Tasks:**
- ✅ A hand-written agent loop: the model can call `search` and `get_item` tools. *(`agent.py`, D9: several tool calls per turn answered in one message, tool errors as `is_error` results, a turn limit `AGENT_MAX_TURNS` (6) then one "answer now" call with tools off, else `incomplete`. `llm.py`: the `LLM` interface, Claude through the SDK (streaming, adaptive thinking, explicit effort, cached system prompt and tools, refusal fallback), and a scripted fake.)*
- ✅ Every claim cites item, page and passage; the app can open the source at that spot. *(D38: `[c:<chunk id>]` markers checked in code; each citation is a snapshot with item, type, title, url, page, `start_char`, `end_char` and the quote, so it outlives re-processing and deletes.)*
- ✅ "Discuss": a chat whose search adds `WHERE item_id = <cited item>`, and also fetches the chunks next to the cited one for context. *(`POST /conversations` with `item_id` and `chunk_id` or `start_char`; our code limits every search to the item and refuses `get_item` for others; the first message carries the cited passage and its neighbours.)*
- ✅ `conversations` and `messages` tables. *(Migration 011, D21: exact API blocks as `json`, replayed unchanged and append-only.)*
- ✅ A grounding check: if the items do not hold the answer, say so. *(`grounded: false` when no valid citation; the system prompt tells the model to say so instead of answering from memory.)*
- ✅ A daily cost cap and a per-minute rate limit (as in RAG Galaxy). *(D39: `model_usage` rows for every model call, `DAILY_COST_CAP_USD` and `ASK_RATE_PER_MINUTE`, 429 with `Retry-After`, `GET /usage`.)*
**Concepts:** tool use, scoped retrieval, conversation state, guardrails, cost control.
**Done when:** ⏳ an eval of 10 questions checks that each cited passage really holds the claim. *(`python -m mind_dump.eval_answers`: 10 questions plus 3 the corpus can't answer, `backend/eval/answers.json`; per question: citations valid, the expected passage cited, the key facts stated, and a judge call per citation. Built and tested with a fake model; the real run needs `ANTHROPIC_API_KEY`.)*
**Done when (stage 4 acceptance), each proved by a test:**
- ✅ E1 Loop: search, then get_item, then an answer, on the real database; two calls in one turn, one results message; a failing tool gives `is_error`. *(`test_agent.py`)*
- ✅ E2 Turn limit: stopped, `incomplete`, usage for every call. *(`test_a_model_that_only_calls_tools_is_stopped_and_marked_incomplete`)*
- ✅ E3 Citations: valid kept with item, page, positions, quote; not retrieved and missing ids removed and reported; none valid means not grounded. *(`test_agent.py`)*
- ✅ E4 Snapshots survive a re-run and a delete. *(`test_old_answers_keep_their_sources_after_a_rerun_and_a_delete`)*
- ✅ E5 Discuss scope. *(`test_a_scoped_search_sees_only_its_item_whatever_the_model_asks`, `test_the_first_request_holds_the_passage_and_its_neighbours`, `test_discuss_by_chunk_or_position_and_after_the_item_is_deleted`)*
- ✅ E6 History replayed byte for byte, append-only. *(`test_a_follow_up_replays_the_stored_blocks_unchanged`)*
- ✅ E7 Streaming: status, delta, final in order, final equals the plain answer, a mid-stream failure saves nothing. *(`test_chat_api.py`)*
- ✅ E8 Cap and rate limit, worker usage counts. *(`test_chat_api.py`, `test_usage.py`)*
- ✅ E9 No key, refusal, `max_tokens`, temporary and permanent errors: clear status, nothing half-saved. *(`test_chat_api.py`)*
- ✅ E10 The Claude client on the real SDK with a mock transport. *(`test_llm_claude.py`)*
- ✅ E11 Usage rows for every kind of call; `GET /usage` sums. *(`test_usage.py`, `test_usage_sums_by_day_model_and_purpose`)*
- ✅ E12 The answers eval harness with a fake model; no results file from a fake run. *(`test_eval_answers.py`)*
