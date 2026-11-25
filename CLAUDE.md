# Mind Dump

Mind Dump is an open-source "second brain" for macOS. The user drops links, files, images and notes on a true black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

The owner, San, is a data engineer who builds this to learn backend and DevOps practice and to explain every choice to employers. The learning matters as much as the app.

## Hard rules

1. **No new dependency without approval.** Before you add any library, framework, tool or service, check `docs/plan/decisions.md`. If it is not Approved there, stop. Explain the problem it solves, what happens without it, and the options. San decides.
2. **Every approval gets a record.** When San approves a decision, write `docs/decisions/DNN-short-name.md` from `docs/decisions/TEMPLATE.md`, and update its row in `docs/plan/decisions.md`.
3. **Explain the plain version; build the real one.** Do not build throwaway versions. Show the problem a tool solves (what breaks without it, how the tool works) with a visual Artifact explainer, then build the version we keep. (Changed by San on 2026-11-17; it replaces "plain version first".)
4. **Never commit secrets.** Keys live in `.env`, which git ignores. `.env.example` holds names only.
5. **Small branches, green CI.** One branch per milestone or smaller. Merge only when CI passes.

## How to work with San

- Explain why before how. Use plain English (short sentences, active voice) and one analogy per new idea.
- Let San try first on design questions. Give hints before answers.
- San works on a Mac. Give macOS commands.

## Read only what the task needs

| Read this file | When |
| --- | --- |
| `docs/plan/milestones/README.md` | You need the build order, the phase gates, or the list of milestones |
| `docs/plan/milestones/mN-*.md` | You work on milestone N. Read only that milestone's file |
| `docs/plan/decisions.md` | You might add a dependency, or need to know what is approved |
| `docs/plan/architecture.md` | You add or change a component or a connection between components |
| `docs/plan/product.md` | You work on a user-facing feature, the design, the animation, or who pays |
| `docs/plan/repo-layout.md` | You create a top-level folder or wonder where a file goes |
| `docs/plan/skills-map.md` | You write the README or prepare portfolio or interview notes |
| `docs/plan/proof-track.md` | You work on a P-step (P0 to P7), or San asks to practise defending a choice. Until 26 Nov 2026 it sets the build order |
| `docs/decisions/` | You need the reasoning behind one approved decision |
| `docs/learning/concepts.md` | San settles a concept and wants it saved, or wants to practise one. Add one paragraph per concept |

## Current status

- M0: done and merged to `main` (PR #1). Repo is public; secret scanning, push protection and the ruleset "main: require PR and CI" are on.
- M1: done and merged to `main` (PR #2): Postgres via Compose, migration runner, items API with tests, + button, 409 on duplicate links, schema diagram (`docs/schema.md`). Next milestone work comes through the proof track (P1 builds M3's fetch, raw landing and versions early).
- Until 26 Nov 2026, the proof track (`docs/plan/proof-track.md`) sets the order: P0 to P4 are musts, backend only. The agent writes the code; San makes the decisions. A step is done only when San can defend it without notes.
- Proof track: **P1** merged (PR #8). **P2** merged (PR #9: D34 uploads by content hash). **P3** merged (PR #10: jobs table, worker service, chunking; D6, D35, D36 with D30). P3 walkthrough and defend not done yet. P2 defend: main answer clean, follow-up needed hints, re-ask once.
- M3: done and merged to `main` (PR #10, PR #11): jobs and worker, PDF text page by page and image descriptions (D7b), Voyage embeddings and the Claude SDK (D8), the `embed_missing` and `check_models` commands. Model keys are optional.
- Proof track: **P0** in progress. D29 merged (PR #4): trunk-based, PR + CI gate, fix forward. Direct-push proof done (rejected by both rules). Hotfix drill done (PR #5). Next: defend Q2/Q3, logged in `docs/plan/proof-track.md`.
- M4: done and merged to `main` (PR #12): hybrid search (D37: full-text + pgvector HNSW, RRF, filters), `GET /search`, tags API, eval (`python -m mind_dump.eval`, `docs/results.md`, real OpenAI vectors: recall@5 keyword 0.80, vector 1.00, hybrid 1.00 after D37's second amendment left any-word rows out of the merge), CI eval floor 0.75. D20: no reranker for now.
- M5: done and merged to `main` (PR #13): agent loop with `search` and `get_item` tools (D9), conversations replayed append-only (D21), citations checked in code with snapshots and a grounding flag (D38), `model_usage` with a daily cost cap and a rate limit (D39), Discuss scoped to one item, SSE streaming, `GET /usage`, API 0.7.0, 478 tests.
- Built by agents on 2026-11-25 with San's delegation: D7b, D37, D20, D9, D21, D38, D39 and the Claude SDK part of D8 were decided by Claude; San reviewed and approved all eight on 2026-11-25, and has not defended them yet (no walkthrough or defend log entry). Checked against the real OpenAI and Claude APIs on 2026-11-25 (keys in `.env`): `check_models`, embeddings, image descriptions, answers, follow-ups, streaming, Discuss, the search eval in all three modes, and the answers eval (`python -m mind_dump.eval_answers`: citations valid 13/13, expected passage cited 10/10, 3/3 unanswerable not grounded, judge 28 of 31 cited sentences supported). San's demo stack was upgraded to this version on 2026-11-25 (migrations 004 to 011; a dump from before the upgrade is in the git-ignored `data/backups/`).
- Data collection choices: 1 raw landing (D28), 2 idempotent jobs (D30), 3 business key (D31), 4 history (D32), 5 duplicate files (D34) recorded. Also approved: D5 storage, D7 web extraction, D33 fetch client, D6 jobs, D35 retries, D36 chunking, D7b PDFs and images, D8 models, D37 hybrid search, D20 no reranker, D9 agent loop, D21 chat history, D38 citations, D39 cost cap (D7b onwards by delegation).
- Embeddings default to OpenAI `text-embedding-3-small` (1,024 dimensions) by San's decision on 2026-11-25 (D8 amended, PR #15); Voyage `voyage-4` stays selectable with `EMBEDDING_PROVIDER=voyage`.
- Interview (26 Nov): live demo from San's Mac, then defend.
- Update this section when a milestone starts or ends.
