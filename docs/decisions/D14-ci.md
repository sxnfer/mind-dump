# D14: CI/CD

Status: Approved, 2026-11-17
Problem: every push must be tested and built, so a broken commit is caught before it is merged.
Without a tool: tests run only when someone remembers to run them. Broken code reaches `main`.
Options: GitHub Actions; CircleCI; Azure Pipelines.
Chosen: GitHub Actions.
Why: the code is already on GitHub. Public repos get free Linux and macOS runners, and the Swift app needs macOS to build. No extra account.
Cost: workflows are GitHub-specific YAML. macOS runners are slower and have tighter free limits than Linux.
We will switch if: free minutes run out, or the project moves off GitHub.
