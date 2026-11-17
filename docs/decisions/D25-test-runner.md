# D25: Test runner

Status: Approved, 2026-11-17
Problem: backend tests must be easy to write, run on every push, and give clear failures.
Without a tool: the standard library `unittest` needs test classes and `assertEqual` calls, and its failure output is harder to read.
Options: unittest; pytest.
Chosen: pytest, plus httpx (needed by FastAPI's `TestClient`). Both are dev-only and never ship in the image.
Why: plain `assert`, readable failure diffs, and fixtures for shared setup (a test client now, a test database in M1). Most Python teams use it.
Cost: two dev dependencies. Fixtures can feel like magic until you learn how they are found (`conftest.py`).
We will switch if: never likely.
