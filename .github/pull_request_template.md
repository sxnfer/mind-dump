## What changed

<!-- One or two sentences. Link the milestone or proof-track step (e.g. P1). -->

## How it was tested

<!-- The tests you added or ran, and anything you checked by hand. -->

## Risk

<!-- What could break, and how we would notice. "Low: docs only" is a fine answer. -->

## Before merging

- [ ] `main` stays releasable: unfinished work is not merged, or is behind a feature flag that is off.
- [ ] No secrets: keys live in `.env`, and `.env.example` holds names only.
- [ ] Any new dependency has an Approved decision in `docs/plan/decisions.md`.
- [ ] Changed `backend/pyproject.toml`? Ran `uv lock` and committed `uv.lock` (CI runs `uv lock --check`).
- [ ] CI is green and the branch is up to date with `main` (the ruleset blocks the merge until both are).
