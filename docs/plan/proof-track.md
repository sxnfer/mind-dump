# Proof track: build it, explain it, defend it

> Read this when: you work on any P-step below, or prepare interview notes. It runs before and alongside M0 to M3, and ends on 26 November 2026.

**Why this exists.** San is preparing for a Data Engineer role that also owns DevOps. Each concept studied must become a feature in Mind Dump, a test that proves it works, and a short answer San can give without notes. If San can't explain a step, it isn't done.

**Scope.** Backend only (Python, Postgres, Docker, GitHub Actions). The Swift app waits until after the interview. Use real documents as test data: carbon registry pages and methodology PDFs are good choices.

**How every P-step ends.** Each step is done only when all four exist:

1. **Build:** the feature, merged through a PR.
2. **Prove:** a test or a screenshot that shows the property holds.
3. **Record:** a decision record in `docs/decisions/` (what, options, why, cost, when we'd switch).
4. **Defend:** San answers the step's question out loud, without notes, in under a minute.

**How the agent works here.** San writes the code. The agent gives hints first, reviews, and asks the defend question before marking a step done. No new dependency without an Approved decision.

---

## Priority

| Step | Topic | Needed by the interview? |
| --- | --- | --- |
| P0 | Branch flow and gates (DevOps 1) | Must |
| P1 | Collect links safely: keys, upsert, raw landing, history | Must |
| P2 | Files: content hash and duplicate behaviour | Must |
| P3 | Jobs: checkpoint, idempotent processing, retries | Must |
| P4 | CI: checks before merge (DevOps 2) | Must |
| P5 | CD: build once, deploy many, approval to PROD (DevOps 3) | Should |
| P6 | Infra as code on Azure (DevOps 4) | Stretch |
| P7 | Monitoring and one real alert (DevOps 5) | Stretch |

---

## P0: Branch flow and gates

**Build.** Two long-lived branches: `develop` (deploys to DEV) and `main` (deploys to PROD). Features branch from `develop`. Protect both: PR required, 1 approval (use a second account or self-review rules), required status checks once P4 exists. Add a PR template: what changed, how it was tested, risk.
**Prove.** A direct push to `main` is rejected (screenshot). Run one **hotfix drill**: branch `hotfix/x` from `main`, PR into `main`, then merge `main` back into `develop`. Keep the PR links.
**Record.** Why environment branches here, and when trunk-based development would be better.
**Defend.**
- What is the difference between a branch policy and an environment approval?
- Walk through a Friday PROD hotfix. Why must it be merged back?
- Name two risks of environment branches.

## P1: Collect links safely

**Build.**
- `items` has a **business key** for links: a normalised URL (lowercase host, no tracking parameters, no trailing slash), with a unique index.
- Save is an **upsert** on that key (`INSERT ... ON CONFLICT (url_key) DO UPDATE`).
- **Raw landing:** every fetched page is saved first as `raw/<sha256>.html` through the `Storage` interface, then parsed.
- **History:** `item_versions` (item_id, content_sha256, valid_from, valid_to). Add a new row only when the content hash changes; close the old row's `valid_to`.

**Prove.** Tests: saving the same URL twice gives 1 item; a changed page updates the item and adds 1 version; an unchanged page adds 0 versions; a query returns what the page said on a past date.
**Record.** Business key vs content hash, and why links use both.
**Defend.**
- Why is a re-run safe? Name the property.
- Content hash vs business key: when does each one fit?
- "What did this page say on 1 November?" Which table answers it, and why keep the raw files too?

## P2: Files

**Build.** `POST /items/upload` stores the file under its sha256. A repeat upload is detected by checksum. Choose the behaviour: return the existing item (200) or reject (409).
**Prove.** Tests: same file twice gives 1 item and 1 stored file; deleting an item leaves no file behind.
**Record.** The duplicate behaviour you chose, and why.
**Defend.** Why is a content hash right for files, but wrong for project records that change?

## P3: Jobs that survive a crash

**Build.**
- `jobs` table with status (queued, running, done, failed, dead) and attempts. Status is the **per-item checkpoint**.
- Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`.
- Writing chunks is **idempotent**: delete the item's chunks and insert the new set **in one transaction**.
- Fetching a link: timeout on every request; retry with a growing wait on timeouts, 429 and 5xx; do not retry 404; after 3 failures the job is `dead`.

**Prove.** The M3 test: kill a worker mid-job, restart, and the item has exactly one set of chunks. A test that a 404 is not retried, and a 503 is.
**Record.** Delete-and-insert vs upsert on (item_id, chunk_index), and the orphan-chunk trap.
**Defend.**
- The worker dies after writing chunks but before marking the job done. What happens on restart, and why is it still correct?
- Checkpoint or idempotent write: which is the safety net?
- Which errors do you retry, and which don't you? Why?

## P4: CI, checks before merge

**Build.** A GitHub Actions workflow on every PR to `develop` and `main`:
- lint;
- tests against a real Postgres (a service container);
- all migrations applied to an empty database from scratch;
- secret scan (gitleaks) and Python security scan (Bandit), and **check in the logs that each one really ran**.

Make the workflow a **required check** in the P0 branch protection.
**Prove.** Open a PR with a failing test and show it can't merge (screenshot). Create a semantic conflict on purpose (rename a function on one branch, call the old name on another), merge, and show CI catches it.
**Record.** What runs before merge, what runs after, and why.
**Defend.**
- Why can a merge with no conflicts still break the code? What catches it?
- What must be true before code is allowed into `develop`?
- How does this map to Azure DevOps? (Workflow ≈ YAML pipeline, required check ≈ build validation policy, runner ≈ build agent.)

## P5: CD, build once and deploy many

**Build.** On merge to `develop`: build one Docker image, tag it with the commit SHA, push it to GitHub Container Registry, deploy to DEV. Promoting to PROD reuses **the same image**; it is never rebuilt. Add a GitHub **environment** `production` with a required reviewer. Migrations run as a deploy step, before the new version starts.
**Prove.** The same image digest appears in the DEV and PROD deploy logs. A PROD deploy waits for approval (screenshot).
**Record.** Why build once, and where migrations run in the deploy.
**Defend.**
- What goes wrong if each environment rebuilds the code?
- Branch policy vs environment approval: show where each one lives in your setup.
- A migration fails halfway through a deploy. What state is the app in, and what do you do?

## P6 (stretch): Infra as code on Azure

**Build.** Terraform for the DEV environment: Container App or App Service, Postgres Flexible Server, Key Vault. The app reads secrets from Key Vault through a managed identity, so no secret is in code or CI variables. Reuse patterns from RAG Galaxy's `infra/`.
**Defend.** Why define infrastructure as code? How does a secret get from Key Vault into the running app?

## P7 (stretch): Monitoring

**Build.** Structured JSON logs with a request ID and job ID. OpenTelemetry traces into Application Insights. One alert that matters: a job reaches `dead`.
**Defend.** How would you find out a pipeline broke before a user does?

---

## Defend log

Keep one line per question once San can answer it without notes. Date it.

| Date | Step | Question | Answered cleanly? |
| --- | --- | --- | --- |
