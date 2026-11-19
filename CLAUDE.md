# Mind Dump

Mind Dump is an open-source "second brain" for macOS. The user drops links, files, images and notes on a true black panel at the notch. A local backend saves them at once, processes them in the background, and answers questions with citations. Each person runs their own copy on their own Mac, with their own AI model key.

The owner, San, is a data engineer who builds this to learn backend and DevOps practice and to explain every choice to employers. The learning matters as much as the app.

## Hard rules

1. **No new dependency without approval.** Before you add any library, framework, tool or service, check `docs/plan/decisions.md`. If it is not Approved there, stop. Explain the problem it solves, what happens without it, and the options. San decides.
2. **Every approval gets a record.** When San approves a decision, write `docs/decisions/DNN-short-name.md` from `docs/decisions/TEMPLATE.md`, and update its row in `docs/plan/decisions.md`.
3. **Plain version first.** Where it is cheap, build the version without the tool first, so the tool solves a pain San has felt.
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

## Current status

- Milestone: **M0** (not started).
- Next step: San decides D1 (backend language), D13 (packaging), D14 (CI) and D17 (licence). Then write the four decision records and follow `docs/plan/milestones/m0-skeleton-tests-ci.md`.
- Until 26 Nov 2026, the proof track (`docs/plan/proof-track.md`) sets the order: P0 to P4 are musts, backend only. The agent writes the code; San makes the decisions. A step is done only when San can defend it without notes.
- Update this section when a milestone starts or ends.
