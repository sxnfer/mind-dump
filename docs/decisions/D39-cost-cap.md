# D39: Cost cap and rate limit

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; San to review)
Problem: each owner pays for their own model keys. A bug in the agent loop, or a stuck client asking again and again, must not run up a bill.
Without a tool: the first sign of a runaway loop is the provider's invoice.
Options: a cap and a rate limit kept in Postgres; a counter in memory; no cap (watch the provider's dashboard).
Chosen: a `model_usage` table, a daily cap and a per-minute rate limit, all in Postgres (migration 011).
- Every model call (answers, judge calls, image descriptions, embeddings) writes a row to `model_usage` with its token counts and a cost estimated from a price table in settings.
- Before an ask, the API checks today's total (UTC day) against `DAILY_COST_CAP_USD` (default 2.00), and the number of questions in the last 60 seconds against `ASK_RATE_PER_MINUTE` (default 10). Over either, it answers 429 with a clear reason, and `Retry-After` (the rate limit: when the oldest question leaves the window; the cap: midnight UTC).
- Each accepted question is a row in `ask_requests`. The count and the insert run under one advisory lock, so two API processes can't both let the eleventh question in.
- Prices: Claude's from Anthropic's model table (`claude-opus-5-5`: $4 input, $20 output, $5 cache write, $0.20 cache read per million tokens). Embedding prices come from a table per model, and one setting, `EMBEDDING_PRICE_PER_MTOK` (it replaced `VOYAGE_PRICE_PER_MTOK` when D8 moved embeddings to OpenAI), overrides them for every embedding call: `voyage-4` $0.06 per million tokens (Voyage's list price, checked on 2026-11-25; the first 200 million tokens are free); `text-embedding-3-small` $0.02 and `text-embedding-3-large` $0.13, derived from the "pages per dollar" table in OpenAI's embeddings guide (62,500 and 9,615 pages at about 800 tokens a page), not read from a price list, so to be checked against OpenAI's pricing page. An embedding model in neither the table nor the setting is priced at the table's dearest entry ($0.13), so the estimate errs toward the cap instead of counting it as free. `MODEL_PRICES` (JSON) overrides any entry. An unknown Claude model is priced at the most expensive Claude price in the table, so the estimate errs toward the cap.
Why:
- The owner pays for their own key.
- A loop bug or a stuck client must not run up a bill.
- A table in Postgres needs no new service, and "cost per day" is one query (M8 will chart it). `GET /usage` shows today's and the last 7 days' totals per model and purpose.
- Rejected: no cap (trusting the provider's dashboard after the fact); an in-memory counter (lost on restart, wrong with two API processes).
Cost: the estimate uses list prices we maintain by hand, so it drifts when prices change. The cap is checked before a call, so one call can overshoot it. A call cut short (the client disconnects mid-stream) has no usage to record.
We will switch if: several users share one copy (then per-user limits).
