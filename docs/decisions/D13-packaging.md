# D13: Packaging

Status: Approved, 2026-11-17
Problem: the backend must run the same way on San's Mac, in CI, on a stranger's Mac and in Azure.
Without a tool: every machine needs the right Python version and libraries installed by hand. "Works on my machine" bugs appear. For example, San's Mac has the system Python 3.9 while the code targets 3.12.
Options: Docker; no container yet (a local virtual environment).
Chosen: Docker.
Why: one image is built once and runs anywhere Docker runs. CI tests the same image we ship. Azure runs containers directly (M7), and `docker compose up` is how others will run the app (D18). San already knows Docker.
Cost: Docker Desktop must be running. Builds add a few seconds. On a Mac, containers run inside a small Linux VM, so file access is slower than native.
We will switch if: never likely for the backend. The Mac app is not containerised; it is a native app (D19).
