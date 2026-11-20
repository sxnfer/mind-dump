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
- Milestone: **M1** (in progress, branch `m1-notes-and-links`, PR #2). Decisions D3, D4, D18, D27 approved. Built: Postgres via Compose, migration runner, items API with tests, + button, 409 on duplicate links, schema diagram (`docs/schema.md`). Next: San tests the + flow in the app, then merges PR #2. M1 explainer: https://claude.ai/artifact/DoHcxumH7LcRFnkFYUs4BE
- Until 26 Nov 2026, the proof track (`docs/plan/proof-track.md`) sets the order: P0 to P4 are musts, backend only. The agent writes the code; San makes the decisions. A step is done only when San can defend it without notes.
- Proof track: **P0** in progress. D29 merged (PR #4): trunk-based, PR + CI gate, fix forward. Direct-push proof done (rejected by both rules). Hotfix drill done (PR #5). Next: defend Q2/Q3, logged in `docs/plan/proof-track.md`.
- Data collection choices: 1 raw landing (D28) recorded; 2 idempotent jobs settled, record pending; 3 business key built (migration 003), record pending; 4 current vs history and 5 duplicate files not started.
- Update this section when a milestone starts or ends.
