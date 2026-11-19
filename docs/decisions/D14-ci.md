# D14: CI/CD

Status: Approved, 2026-11-17
Problem: every push must be tested and built, so a broken commit is caught before it is merged.
Without a tool: tests run only when someone remembers to run them. Broken code reaches `main`.
Options: GitHub Actions; CircleCI; Azure Pipelines.
Chosen: GitHub Actions.
Why: the code is already on GitHub. Public repos get free Linux and macOS runners, and the Swift app needs macOS to build. No extra account.
Cost: workflows are GitHub-specific YAML. macOS runners are slower and have tighter free limits than Linux.
Ready-made steps (approved 2026-11-17): the workflow calls actions written by others. Each runs code inside CI, like a library.
- `actions/checkout` (GitHub): downloads the repo.
- `astral-sh/setup-uv` (the uv team): installs uv and caches packages.
- `docker/setup-buildx-action` and `docker/build-push-action` (Docker): build the image and keep its layers between runs.
Risk: a hacked action can read what CI can read (the tj-actions/changed-files incident, March 2025). Now: the workflow has read-only access and CI holds no secrets. M9: pin each action to a commit SHA and let Dependabot propose updates.
We will switch if: free minutes run out, or the project moves off GitHub.
