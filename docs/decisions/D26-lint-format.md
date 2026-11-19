# D26: Lint and format

Status: Approved, 2026-11-17
Problem: some bugs (unused imports, undefined names) can be found without running code, and style differences waste review time.
Without a tool: typos reach CI or production; reviews argue about spacing and import order; diffs get noisy.
Options: none; flake8 + black + isort; ruff.
Chosen: ruff, for both `ruff check` (lint) and `ruff format` (format). CI fails if either reports a problem. Dev-only.
Why: one fast tool replaces three, configured in `pyproject.toml`.
Cost: one dev dependency and a rule set to tune.
We will switch if: we need a rule ruff does not support.
