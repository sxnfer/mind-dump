# D24: Python version and dependency manager

Status: Approved, 2026-11-17
Problem: the same Python version and the same version of every library (including the ones we never named) must run on San's Mac, in CI and in the Docker image.
Without a tool: "pip install fastapi" gives different versions on different days; a changed hidden dependency breaks the app with no code change. San's Mac has Python 3.9; the container targets 3.12.
Options: pip + requirements.txt (+ pip-tools); Poetry; uv.
Chosen: uv, with `pyproject.toml` (what we ask for) and `uv.lock` (exactly what we got, with checksums), committed to git.
Why: one fast tool installs Python 3.12, creates the virtual environment and keeps the lockfile. `uv sync --frozen` installs exactly the lockfile everywhere and fails if the lockfile is out of date.
Cost: a newer tool; teams that use plain pip must learn its commands. The lockfile format is uv-specific.
We will switch if: uv stops being maintained, or a deployment target cannot run it (then export the lock to `requirements.txt` with `uv export`).
