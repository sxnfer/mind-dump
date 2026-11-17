# D25: Test runner

Status: Approved, 2026-11-17
Problem: backend tests must be easy to write, run on every push, and give clear failures.
Without a tool: the standard library `unittest` needs test classes and `assertEqual` calls, and its failure output is harder to read.
Options: unittest; pytest.
Chosen: pytest, plus httpx2 (needed by FastAPI's `TestClient`). Both are dev-only and never ship in the image.
Update 2026-11-17: switched from httpx 0.28.1 to httpx2. Starlette deprecated httpx for its test client. httpx has had no release since 0.28.1, and httpx2 is its successor, maintained by the Pydantic team. San approved the switch.
Why: plain `assert`, readable failure diffs, and fixtures for shared setup (a test client now, a test database in M1). Most Python teams use it.
Cost: two dev dependencies. Fixtures can feel like magic until you learn how they are found (`conftest.py`).
We will switch if: never likely.
