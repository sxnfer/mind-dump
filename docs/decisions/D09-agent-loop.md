# D9: Agent loop

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; reviewed and approved by San the same day)
Problem: to answer a question from the owner's items, the model must look things up: search for passages, then read the items around them, maybe search again, then answer (M5).
Without a tool: one search with the question as typed, the top passages pasted into one model call. A question that needs a second search, or the text around a hit, gets a worse answer or none.
Options: a hand-written loop on the Messages API; the SDK's Tool Runner helper; Anthropic's Managed Agents; an agent framework.
Chosen: a hand-written loop on the Messages API with the official `anthropic` SDK (D8), in `agent.py`.
- The model gets two tools: `search` (calls the real `search.search()`, hybrid when an embedding model is configured) and `get_item` (one item's passages in order, in bounded parts).
- Our code runs each tool call against Postgres, returns the result, and repeats until the model answers or a turn limit is reached (`AGENT_MAX_TURNS`, default 6). At the limit the model is asked once to answer with what it has; if it still asks for a tool, the answer is marked incomplete.
- Several tool calls in one model turn are all run, and all results go back in one user message. A tool that fails returns an `is_error` result; the loop goes on.
- Every model call writes a usage row (D39).
Why:
- About 100 lines San can read and explain step by step.
- The tools must run inside the owner's backend, next to the local database.
- The turn limit and the cost cap sit in our code, where tests can prove them.
- Rejected: the SDK's Tool Runner helper (it hides the loop San needs to explain, and it is a beta surface); Anthropic's Managed Agents (Anthropic hosts the loop and a sandbox, which suits agents that need a workspace; ours needs only two database reads, and each owner would have to create hosted agent resources); an agent framework (a large dependency to wrap one loop).
Cost: we own the loop's edge cases: several tool calls in one turn, tool errors, the turn limit, and every stop reason (`tool_use`, `end_turn`, `max_tokens`, `refusal`, `pause_turn`).
We will switch if: the agent needs many tools, long-running work or a sandbox.

Amended 2026-11-26, at San's request: answers are flowing prose. The system prompt asks for full sentences in one to three short paragraphs, with no lists, headings, bold or other markup; steps or several items are written as a sentence. Lists and markup looked busy in the capsule, which shows the text as it comes. The answers eval reports how many answers hold to this ("Written as flowing prose"); it does not decide whether a question passes.
