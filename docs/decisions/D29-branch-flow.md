# D29: Branch flow and hotfixes

Status: Approved, 2026-11-19
Problem: code must get from a laptop to DEV and then PROD without reaching PROD untested, and a broken PROD must be fixable fast and safely.
Without a tool: pushes straight to `main`, nothing stops untested code, and a hotfix is improvised under pressure.
Options: environment branches (`develop` → DEV, `main` → PROD, promote by merging); trunk-based (one `main`, build once, promote the same image).
Chosen: trunk-based, with fix forward for hotfixes.
- One long-lived branch, `main`. Work arrives through short-lived branches and pull requests.
- The gate: every change reaches `main` through a PR. No approvals are required (one owner, and GitHub does not let you approve your own PR). The CI checks `backend (lint, test, image, smoke)` and `app (xcodebuild)` must pass. GitHub push protection blocks known secret formats before they reach the repo. Further security scans are decided in P4.
- Build once, deploy many: each merge to `main` builds one image; DEV gets it first, and PROD gets the same image after an approval (built in P5).
- Hotfix: fix forward. Branch from `main`, PR, CI, merge; the new image goes DEV → approval → PROD. DEV is never skipped.
- `main` is always releasable: unfinished work is not merged, or sits behind a feature flag (a setting in `.env`) that is off. The PR template asks this on every PR.
Why (in San's words): "Trunk-based, so the image tested in DEV is what ships." "We shouldn't skip the dev build, this could be dangerous." "We'll fix forward: a fast-paced, structured and always releasable main. We won't have to work with extra branches and resolving conflicts, so long as we use feature flags."
Cost: `main` must stay releasable every day, so unfinished work needs flags. There is no branch that shows "what is in PROD"; a deploy tag or log answers that instead. Fix forward ships everything on `main`, not only the fix.
We will switch if: other people join and want scheduled batch releases, or `main` often holds work that can't ship. Then: fix from the commit PROD runs (a tagged release branch), or environment branches that still build once.
