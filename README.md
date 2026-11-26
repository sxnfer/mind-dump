# Mind Dump

[![CI](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/sxnfer/mind-dump/actions/workflows/ci.yml)

An open-source "second brain" for macOS. Drop links, files, images and notes on the notch, or type them into a glass capsule under it. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

> Status: Milestone 5. Click the notch and type or paste a note or a link, or drag text onto the notch, and it is saved; upload PDFs and images through the API. A worker reads, chunks and (with your key) embeds each item in the background. Search finds passages by exact words, by meaning, or both, with filters; an eval measures how well. Ask a question and get an answer whose every claim cites the item, page and passage, then chat about one cited passage; a daily cost cap and a rate limit keep the bill small.

## Run it

You need Docker Desktop, and Xcode 26 or later. The Mac app needs macOS 26 or later (it uses Liquid Glass).

```bash
# 1. Settings: copy the example and set a database password
cp .env.example .env
sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
#    Optional: add OPENAI_API_KEY and ANTHROPIC_API_KEY to .env (see "AI model keys" below)

# 2. Backend: Postgres, then migrations, then the API and the worker, in order
docker compose up -d --wait api worker
curl -i http://localhost:8000/health          # {"status":"ok"}
open http://localhost:8000/docs               # generated API docs

# 3. Mac app (macOS 26 or later): the island at the notch. Move the pointer to the notch, click, type, Return.
#    Or press Option-Shift-Space in any app to open it with the keyboard ready; press again to close.
xcodebuild -project app/MindDump.xcodeproj -scheme MindDump -derivedDataPath app/build/dd build
open app/build/dd/Build/Products/Debug/MindDump.app
```

### Fetch saved links and see their history

The worker fetches saved links by itself (P3). This command fetches them now, in the terminal. Each fetch lands the raw page, records it, and opens a new version only when the article text changes.

```bash
docker compose exec api python -m mind_dump.refetch --all     # fetch every saved link now
curl -s localhost:8000/items/1/versions                       # every text the page has shown
curl -s "localhost:8000/items/1?as_of=2026-11-01"             # what it said on 1 November
docker compose exec api python -m mind_dump.refetch --all --rebuild   # re-extract from landed files, no network
```

Upload a PDF, PNG or JPEG (at most 25 MB) as the raw request body. The same bytes twice give a 409 that points at the first copy, whatever the file is called. The worker reads a PDF's text page by page; an image gets a description from Claude (it needs `ANTHROPIC_API_KEY`).

```bash
curl -si --data-binary @file.pdf -H 'Content-Disposition: attachment; filename="file.pdf"' localhost:8000/items/upload
curl -s localhost:8000/items/1                 # status: queued, processing, ready or error
curl -s localhost:8000/items/1/text            # the file's text, with its pages
curl -s localhost:8000/items/1/chunks          # passages (with chunk_id, as in search hits and citations), page numbers, and whether each has a vector
```

### Search

`GET /search` finds passages (chunks), not just items (D37). Each hit carries its item, its place in the text (`start_char`, `end_char`, and `page` for PDFs) and how it was found: its rank in the keyword list, its rank in the vector list, and the merged score.

```bash
curl -s "localhost:8000/search?q=buffer+pool"                          # hybrid: words and meaning, merged
curl -s "localhost:8000/search?q=%22buffer+pool%22+-fire&mode=keyword" # exact phrase, without "fire"
curl -s "localhost:8000/search?q=woodland+catches+fire&mode=vector"    # by meaning (needs OPENAI_API_KEY)
curl -s "localhost:8000/search?q=registry&type=link&from=2026-11-01&to=2026-11-30&tag=carbon&limit=5"
curl -s "localhost:8000/search?q=leakage&item_id=7"                    # one item's passages
curl -s -X PUT localhost:8000/items/7/tags -H 'content-type: application/json' \
  -d '{"tags": ["Carbon Markets", "MRV"]}'                              # replace the tags ([] clears)
```

- `mode`: `hybrid` (default), `keyword` or `vector`. The response's `mode` says what really ran.
- Keywords (`mode=keyword`): chunks with all your words come first, then chunks with any of them (words are stemmed, so "retries" finds "retry"). `"quotes"` for a phrase or `-word` to exclude switch to exact matching: every word must appear. `or` means either.
- Hybrid: the meaning list does the loose matching, so the keyword list adds only exact evidence to the merge: chunks with every word you typed, then chunks holding a code or number you typed (`MD-2291`, `DQ-117`). A chunk sharing one ordinary word with your question gets no keyword boost (D37). If hybrid falls back to keywords, it uses the keyword rule above.
- Filters: `type` (note, link, file), `from` and `to` (a date means the whole day, UTC, or a full time), `tag`, `item_id`. They narrow both lists before ranking, so a filter never hides a match.
- Without the embedding key (`OPENAI_API_KEY`, or `VOYAGE_API_KEY` with `EMBEDDING_PROVIDER=voyage`), or when the embedding API is failing, hybrid returns keyword results with `"degraded": true` and a `notice`; `mode=vector` answers 503 with the reason. Both name the key to set.
- Tags are normalised one way: trimmed, inner spaces squeezed to one, lowercased. So `?tag=carbon markets` finds an item tagged `Carbon  Markets`.

### Measure search

The eval loads 12 original documents (`backend/eval/corpus/`) through the real pipeline into its own database (`mind_dump_eval`, created if missing; it refuses any database not named `*_eval` or `*_test`), asks 20 golden questions (`backend/eval/golden.json`), and prints recall@5 and MRR@10 per mode, with every miss. It writes `docs/results.md`. Run it from a checkout, with the Compose database up:

```bash
cd backend
uv run python -m mind_dump.eval                 # keyword only without a key; all three modes with one
uv run python -m mind_dump.eval --no-results    # print only
```

It reads `EMBEDDING_PROVIDER` and that provider's key (`OPENAI_API_KEY` by default) from the environment or from `.env`; with a key, the 30 chunks and 20 questions are embedded (a few thousand tokens). CI runs it in keyword mode and fails if keyword recall@5 drops below its floor.

### Ask and discuss

Ask a question (it needs `ANTHROPIC_API_KEY`). An agent searches your items, reads around what it finds, and answers. After each claim it writes a marker such as `[c:12]`: chunk 12 is in `citations`, with its item, page, character positions and the quoted passage. Our code, not the model, checks every marker: one for a passage the agent was never shown is removed and listed in `invalid_citations` (D38). An answer with no valid citation comes back `"grounded": false`: your items probably don't hold the answer, and the agent is told to say so instead of answering from memory.

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "What share of credits goes to the buffer pool?"}'
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "What did I save about queues?", "type": "link", "from": "2026-11-01"}'
curl -s localhost:8000/conversations/1                          # the chat, with citations
curl -s localhost:8000/conversations/1/messages -H 'content-type: application/json' \
  -d '{"text": "Explain that more simply"}'                     # a follow-up
curl -sN "localhost:8000/conversations/1/messages?stream=true" -H 'content-type: application/json' \
  -d '{"text": "Give an example"}'                              # streamed: Server-Sent Events
```

- Answers are short flowing prose (no lists or headings), with a marker such as `[c:12]` after each sentence that comes from a passage. `citations` lists one entry per cited passage.
- Streaming (`?stream=true` or `Accept: text/event-stream`) sends `status` events (thinking, searching with the query, reading an item), `passages` events, `delta` events with the answer's text as it comes, then `final` with the same JSON as the plain call, or `error`. Nothing is saved unless `final` arrives.
- A `passages` event lists the passages the model may cite from now on, as `{"passages": [{"chunk_id": 412, "item_id": 37}]}`: at the start of a follow-up for those of earlier turns, then after each tool result. It always comes before any `delta` that cites them, so a client can number sources by item while the text streams.
- Discuss a citation: start a chat about one item and passage, then send messages to it. Every search in it is limited to that item by our code, and the first message carries the passage and the ones just before and after it.

```bash
curl -s localhost:8000/conversations -H 'content-type: application/json' \
  -d '{"item_id": 7, "chunk_id": 12}'          # or "start_char": 1040, if the chunk was replaced
```

- A failed answer (Claude down: 503; another API error or an answer cut off: 502; Claude declines: 422) saves nothing, not even your question: send it again.
- Old answers keep their citations' quotes and positions after the item is re-processed or deleted (`chunk_id` becomes `null`). A chat about a deleted item can still be read, but takes no new messages (409).
- `GET /conversations` lists chats, newest first; `DELETE /conversations/{id}` deletes one.

### Cost: usage, the daily cap and the rate limit

Every model call (answers, image descriptions, embeddings, the eval's judge) is recorded with its tokens and an estimated cost (D39). The estimate uses list prices kept in the code (Claude's from Anthropic's model table); embedding prices come from a small table per model (`text-embedding-3-small` $0.02 and `-3-large` $0.13 per million tokens, derived from OpenAI's "pages per dollar" table, so check them against OpenAI's pricing page; `voyage-4` $0.06, Voyage's list price), and `EMBEDDING_PRICE_PER_MTOK` overrides them. An embedding model not in the table is priced at the dearest entry, so the estimate errs toward the cap.

```bash
curl -s localhost:8000/usage        # today (UTC) and the last 7 days, per model and purpose
```

- `DAILY_COST_CAP_USD` (default 2.00): when today's estimated total reaches it, asking answers `429` until midnight UTC. One answer can overshoot it a little: the cap is checked before each question.
- `ASK_RATE_PER_MINUTE` (default 10): more questions than that in 60 seconds answer `429` with `Retry-After`.
- `LLM_EFFORT` (default `medium`) sets how much Claude thinks per answer, and so its cost; `AGENT_MAX_TURNS` (default 6) bounds its searches per question.

Set them in `.env`, then `docker compose up -d api worker`.

### Measure answers

`python -m mind_dump.eval_answers` asks 10 questions about the eval corpus (and 3 it can't answer) through the real agent, and checks for each: every citation valid, the expected passage cited, the key facts stated, and (one small Claude call per citation) whether the cited passage supports its sentence. It writes the "Answers" section of `docs/results.md`. It needs `ANTHROPIC_API_KEY` (and uses the embedding key, `OPENAI_API_KEY` by default, for hybrid search if set); every call it makes is priced and the total is printed. Without the key it says the eval is pending.

```bash
cd backend
uv run python -m mind_dump.eval_answers               # needs ANTHROPIC_API_KEY
uv run python -m mind_dump.eval_answers --no-results  # print only
```

### AI model keys (optional)

Each owner uses their own keys (D8). All are optional. Embeddings come from OpenAI by default (`text-embedding-3-small`, 1,024 dimensions); set `EMBEDDING_PROVIDER=voyage` to use Voyage AI (`voyage-4`) instead. Only the chosen provider's key is used.

| Key | Get it at | Without it |
| --- | --- | --- |
| `OPENAI_API_KEY` | platform.openai.com | Everything is saved, read and chunked; chunks have no vectors, so search finds exact words only (hybrid falls back to keyword results). |
| `VOYAGE_API_KEY` | dashboard.voyageai.com | Only used with `EMBEDDING_PROVIDER=voyage`, in place of `OPENAI_API_KEY`. |
| `ANTHROPIC_API_KEY` | platform.claude.com | Images end in `error` (there is nothing to read without a description), and asking answers `503`; notes, links, PDFs and search work. |

Put them in `.env`, then:

```bash
docker compose up -d worker api                                   # pick up the new settings
docker compose exec worker python -m mind_dump.check_models       # OK per provider, or the reason
docker compose exec worker python -m mind_dump.embed_missing      # embed what was saved before
```

Run `embed_missing` again after changing `EMBEDDING_MODEL` or `EMBEDDING_PROVIDER`: vectors from two models can't be compared, so the chunks are embedded again in place. When keys are set, item text goes to OpenAI (or Voyage AI, if chosen), and images, questions and the passages the agent reads go to Anthropic.

Option-Shift-Space opens the capsule from any app, ready to type, and closes it again. It needs no permission. If another app holds the same keys, the shortcut is off and the app logs `[hotkey] could not register`.

Right-click the notch or the capsule to quit. The app is unsigned (see `docs/decisions/`), so build it from source.

`docker compose down` stops everything and keeps your data (the database and the landed pages, in two volumes). `docker compose down -v` deletes the data too.

## Develop the Mac app

The app is a black "island" that hangs from the notch, and a Liquid Glass capsule that appears just under it, in place (`app/MindDump/Island/`, `app/MindDump/Panel/CapsuleView.swift`). The window never moves; only the two shapes change inside it: the island on springs, the capsule by a short fade and a 4% scale from its top edge. The capsule has two modes, Capture and Ask (the round button or Tab flips them). Ask streams an answer with numbered sources from `POST /ask` (`app/MindDump/Ask/`), and the capsule grows into a card for it. Sources are numbered by item: two passages of one note share its number and its row, and opening the row shows both quotes, each with its page for a PDF. The row's header joins the pages as ranges ("p. 4, 9–11"). A row with several passages shows each quote in at most three lines, and at most six quotes, then "and N more passages". Text, or one PDF, PNG or JPEG file, dragged onto the notch is saved when you let go (`app/MindDump/Capture/DropModel.swift`; a file goes to `POST /items/upload`).

Debug builds carry these aids, mostly in `app/MindDump/Support/Debug.swift`:

```bash
APP=app/build/dd/Build/Products/Debug/MindDump.app
"$APP/Contents/MacOS/MindDump"                        # run from Terminal: prints frame timings
MD_MOTION_SCALE=8 "$APP/Contents/MacOS/MindDump"      # everything eight times slower
MD_ASK_FIXTURE=1 "$APP/Contents/MacOS/MindDump"       # Ask plays canned answers: no model call, no cost
MD_REDUCE_MOTION=1 "$APP/Contents/MacOS/MindDump"     # behave as if Reduce Motion were on
MD_REDUCE_TRANSPARENCY=1 "$APP/Contents/MacOS/MindDump"  # a solid dark capsule, as with Reduce Transparency
MD_API_BASE=http://127.0.0.1:18000 "$APP/..."         # talk to a throwaway backend, not your items
MD_MOTION_TUNING=drastic "$APP/Contents/MacOS/MindDump"  # springs: calm, springy (default) or drastic
notifyutil -p io.github.sxnfer.MindDump.debug.open    # drive it from a shell: peek, open, close, saved, film, script
notifyutil -p io.github.sxnfer.MindDump.debug.tuning-calm   # switch springs while it runs (tuning-springy, tuning-drastic)
```

The motion comes in three tunings (`IslandMotion.Tuning`): `calm` (the iPhone's measured springs, almost no overshoot), `springy` (every shape change overshoots a little and settles) and `drastic` (it pushes clearly past and eases back, with more blur on the words). A switch prints `[motion] tuning <name>` and applies from the next transition. The default is the one constant `IslandMotion.defaultTuning`. Reduce Motion ignores the tuning.

Each transition prints a line such as `[frames] open: 156 frames, median 8.33 ms (120 Hz), worst 8.33 ms, late 0`. A late frame is a dropped frame. A whole answer stream is timed as one line, `[frames] stream: …`.

The canned answers pick a script from a word in the question: `long` (passes the card's height), `pdf` (19 passages of one 13-page PDF: one row with many passages), `nothing` (not from your items), `fail` (an error mid-stream), `limit` (429), `key` (no Claude key); anything else gets a short answer that cites two passages of one item and one of another, so it shows two sources.

`script` plays `<app temp folder>/script.txt`, one step per line: `pointer notch|away`, `click` (the notch), `click away`, `click <control>` (`mode`, `send`, `new-question`, `source-1`, `open-1`, …), `type …`, `key return|escape|tab|cmd-n`, `scroll <points>`, `drag <text>`, `dragfile <path>`, `dragexit`, `drop`, `dropfile`, `measure <name>`, `wait <s>`, `film [frames] [seconds]`, `snap`, `tuning calm|springy|drastic`, `pill saved|reading|ready|failed`, `openwaiting` (the capsule without the keyboard), `release` (a drop target let go, then "Saved", with no save), `backdrop white|dark|busy|off` (a window of ours behind the capsule) and `shot` (a still of the screen there, to see the glass), `boxsize`, `keystate`, `resignkey`, `hotkey [down|up]` (the global shortcut, from just after the system delivers it), `quit`. The full list is at the top of `DebugScript` in `Debug.swift`.

## Develop the backend

The backend uses [uv](https://docs.astral.sh/uv/) for Python 3.12 and locked dependencies.

```bash
cd backend
uv sync                                       # install exactly what uv.lock says
docker compose up -d db                       # tests need Postgres (they use mind_dump_test)
uv run pytest                                 # tests
uv run python -m mind_dump.migrate            # apply new migrations (needs DATABASE_URL)
uv run ruff check . && uv run ruff format .   # lint and format
```

## The CI gate

Every pull request runs three jobs, and the ruleset blocks the merge until all pass (D14, D29, D40). The branch must also be up to date with `main`.

| Check | Job | Run it on your Mac |
| --- | --- | --- |
| Lockfile matches `pyproject.toml` | backend | `cd backend && uv lock --check` (fix: `uv lock`) |
| Lint, format, tests, search eval | backend | see "Develop the backend" |
| Migrations from an empty database, twice | backend | `cd backend && DATABASE_URL=postgresql://mind_dump:<password>@localhost:5432/mind_dump_migcheck scripts/migrations_twice.sh` (creates that database; drop it after) |
| Image build and smoke test | backend | `docker compose up` |
| Secrets in the PR's commits (gitleaks) | scans | `docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.30.1 git /repo --redact -v` |
| Vulnerable dependencies with a fix (`uv audit`) | scans | `cd backend && uv audit --locked --output-format json > /tmp/audit.json; python3 scripts/audit_gate.py /tmp/audit.json --uv-exit $?` |
| Image: high or critical with a fix (Grype) | scans | `docker build -t mind-dump-api:scan backend && docker save mind-dump-api:scan -o /tmp/image.tar && docker run --rm -v /tmp:/scan -v "$PWD/.grype.yaml:/c.yaml" anchore/grype:v0.122.1 docker-archive:/scan/image.tar -c /c.yaml --only-fixed --fail-on high` |
| Xcode build | app | see "Run it" |

Findings with no fix yet don't block a merge. `nightly-scans.yml` runs the scans without that filter every night (or by hand from the Actions tab) and fails, which emails the owner. An accepted risk is a dated entry with a reason: `--ignore <ID>` in `ci.yml` for `uv audit`, `.grype.yaml` for Grype. CI pins the scanners by digest or SHA-256, and every action by commit hash; the local commands above use version tags for brevity.

## Layout

| Path | What lives there |
| --- | --- |
| `backend/` | API (FastAPI) and, from M3, workers. `Dockerfile`, tests. `eval/`: the corpus, the search eval's golden questions (M4) and the answers eval's questions (M5). |
| `app/` | Native macOS app (SwiftUI in an `NSPanel`). |
| `.github/workflows/` | CI: lint, test, migrations, image build and smoke test; Xcode build; secret, dependency and image scans (D40); nightly scans. |
| `docs/plan/` | The plan and milestones. |
| `docs/decisions/` | One record per approved decision, with the reasons. |
| `docs/results.md` | The latest search eval numbers (M4) and answers eval numbers (M5). |

## Licence

MIT. See `LICENSE`.
