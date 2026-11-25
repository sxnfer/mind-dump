# D40: The CI gate, checks before merge

Status: Approved, 2026-11-25 (decided by San)
Problem: D14 and D29 make lint, tests, the image build and the smoke test pass before a merge. Some breakage still gets through: a pull request that was green against an old `main`, a lockfile that no longer matches `pyproject.toml`, a migration that only works on a database that already exists, a key in a commit, a known vulnerable package or base image. Every action is also referenced by a tag, which its owner (or an attacker) can move.
Without a tool: each of these is caught by a person remembering to look, or by a user. A leaked key in a public repo is found by bots in minutes.

Options, one set per choice:
1. Stale green (a PR tested against an old `main`): require the branch to be up to date with `main` before merging; rely on CI running again on `main` after the merge; a merge queue.
2. Secret scan: gitleaks; TruffleHog; GitHub's built-in secret scanning and push protection only (on already, D29).
3. Dependency audit: `uv audit` (built into uv, experimental in 0.13.7); osv-scanner; pip-audit.
4. Image scan: Grype; Trivy pinned by digest; osv-scanner.
5. When a scan blocks: block on any finding; block only when a fix exists; nightly only.
6. Actions: pin each to a full commit hash; keep tags.

Facts San was shown:
- CI takes about two minutes per pull request: backend job about 110 s, app job about 50 s (medians of six runs).
- `uv audit` needs no new tool, but it is experimental: its output can change in any uv release.
- In March 2026 Trivy's releases and action tags were hijacked (GHSA-69fq-xp46-6x23). Users who pinned by digest were not affected.
- A scan that reads a database that changes every day can fail with no code change. The "fix exists" rule and the nightly run answer that.

Chosen:
1. Require a pull request's branch to be up to date with `main` before it merges (a ruleset setting, changed after this record merges).
2. Gitleaks (MIT) as a plain binary, pinned by the SHA-256 of its release file, not through `gitleaks/gitleaks-action` (a different licence since v2). It scans the commits a pull request adds; the nightly run scans the full history.
3. `uv audit` on `uv.lock`, read by a small script (`backend/scripts/audit_gate.py`) because the command has no "only fixable" flag.
4. Grype (Anchore, Apache-2.0), run from its image pinned by digest, on the image the job builds.
5. Block only when a fix exists: a dependency finding with a fixed version, or a high or critical image finding with a fix. Findings with no fix do not block; a nightly workflow (`nightly-scans.yml`) runs the same scans without that filter and fails, so GitHub emails San. Any accepted risk is a dated ignore entry with a reason (`--ignore <ID>` in the workflow for `uv audit`, `.grype.yaml` for Grype).
6. Pin every action to a full commit hash, with the version in a comment.
Also in the gate: `uv lock --check`, and migrations run twice on an empty database (run 1 applies every file, run 2 applies none). The real-model evals stay out of CI, and no model keys go into GitHub.
Why (San): not recorded yet; San to add.
Cost: a pull request waits for a third job (`scans (secrets, dependencies, image)`), which runs in parallel with the other two. "Up to date" means updating a branch and waiting for CI again whenever `main` moves. Pinned actions and tools get no fixes until someone updates the hash. `uv audit`'s JSON may change in a uv upgrade; the gate script then fails loudly and needs a change. Gitleaks gets security patches only (its README calls it feature complete). The nightly image scan fails while the Debian base image has high findings with no fix (82 on 2026-11-25), until they are fixed or accepted in `.grype.yaml`.
We will switch if: `uv audit` changes shape often or stays experimental for long (then osv-scanner or pip-audit); gitleaks stops getting patches (then TruffleHog); merges wait on "update branch" too often (then a merge queue).
