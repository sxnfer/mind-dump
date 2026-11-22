# Proof track: decide it, check it, defend it

> Read this when: you work on any P-step below, or San asks to practise defending a choice. It runs before and alongside M0 to M3, and ends on 26 November 2026.

**Why this exists.** San is preparing for a Data Engineer role that also owns DevOps. The goal is to think like a data and DevOps engineer: to make the design choices, understand what was built, and defend it. **The agent writes the code. San makes the decisions.** If San can't explain a step, it isn't done, however good the code is.

**Scope.** Backend only (Python, Postgres, Docker, GitHub Actions). The Swift app waits until after the interview. **Interview format (San, 2026-11-22):** show the app live on San's Mac by screen share, then defend the choices. No public demo and no cloud are needed before 26 Nov; Azure (P5 to P7) proves deploys afterwards, and a later public demo would be read-only. Use real documents as test data: carbon registry pages and methodology PDFs are good choices.

## How every P-step runs

1. **Decide (San).** Before any code, the agent sets out the problem and the step's decisions in plain English. San chooses and gives a reason. The agent pushes back on weak reasoning, but never decides for San.
2. **Build (agent).** The agent implements San's choices exactly, in a small PR, and adds the proving test.
3. **Walkthrough (San).** The agent explains the PR in black-box terms: for each part, what goes in, what comes out, and why. San must be able to point to where each decision lives in the code. San approves and merges the PR.
4. **Record (agent).** A decision record in `docs/decisions/` from `TEMPLATE.md`, in San's words.
5. **Defend (San).** The agent asks the step's questions. San answers out loud, without notes, in under a minute each. Then the agent plays a sceptical interviewer and asks one "why not X?" follow-up. A step is done only when both answers are clean. Log it in the defend log.

**How the agent works here.** Don't explain how the code works line by line. Explain what each part does, what it takes in and puts out, and why it exists. No new dependency without an Approved decision.

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

**San decided (D29).** Trunk-based: one `main`, build once, promote the same image. A PR is required, with 0 approvals and the two CI checks. Hotfixes fix forward from `main` through DEV, never skipping it; `main` stays releasable with feature flags.

**Agent builds.** Branch protection on `main` (ruleset "main: require PR and CI"), a PR template (what changed, how it was tested, risk, and a releasable-`main` check), and a hotfix drill run through real PRs.
**Proof.** A direct push to `main` is rejected (screenshot). The hotfix drill PR links.
**Defend.**
- What is the difference between a branch policy and an environment approval?
- Walk through a Friday PROD hotfix with fix forward. What makes it safe to ship everything on `main`?
- Why did you choose trunk-based over environment branches, and when would environment branches be the better choice?
- Name two risks of environment branches.

## P1: Collect links safely

**San decided (D31, D32, with D28, D5, D7, D33).** Business key: the normalised URL (drop the fragment and named tracking parameters, keep the trailing slash), and a repeat save gets 409. History: versions with `valid_from` / `valid_to`, a new one only when the text hash changes, rebuilt from raw when the extractor changes. Raw files go to a local folder behind a `Storage` interface. HTML text comes from BeautifulSoup. Fetching uses `httpx2`. The demo shows `GET /items/{id}?as_of=<date>`.

**San decides.**
- The **business key** for a link. What counts as "the same URL"? (Upper or lower case? Tracking parameters? Trailing slash?)
- Saving a known URL: upsert, skip, or reject? Why?
- Raw landing: save the fetched HTML before parsing, or not? Named by what?
- History: when a page changes, overwrite only, or keep versions? If versions, when does a new one start?

**Agent builds.** Your choices, plus tests for them. Built on branch `p1-collect-links`: `urls.py` (business key), `storage.py` (D5), `extract.py` (D7), `fetcher.py` (D33), `pages.py` (land, record, version, rebuild), migrations 004 and 005, `GET /items/{id}?as_of=` and `/versions`, and `python -m mind_dump.refetch` until the P3 worker.
**Proof.** Tests: saving the same URL twice gives 1 item; a changed page updates the item and adds 1 version; an unchanged page adds 0 versions; a query returns what the page said on a past date.
**Defend.**
- Why is a re-run safe? Name the property.
- Content hash vs business key: when does each one fit?
- "What did this page say on 1 November?" Which table answers it, and why keep the raw files too?

## P2: Files

**San decides.**
- How a duplicate file is recognised (name, size, or content hash?).
- What happens on a duplicate upload: return the existing item (200) or reject it (409)?

**Agent builds.** Upload stored under its content hash, with your duplicate behaviour.
**Proof.** Tests: same file twice gives 1 item and 1 stored file; deleting an item leaves no file behind.
**Defend.** Why is a content hash right for files, but wrong for project records that change?

## P3: Jobs that survive a crash

**San decides.**
- How a worker knows what's done: one watermark for the run, or a status per job?
- How rewriting chunks stays idempotent: upsert on (item_id, chunk_index), or delete-and-insert in one transaction? (Hint: what happens when a new chunker makes fewer chunks?)
- Which fetch errors to retry, how many times, and how long to wait between tries.

**Agent builds.** A jobs table with statuses, safe claiming of jobs so two workers never take the same one, your write strategy, and your retry rules.
**Proof.** Kill a worker mid-job, restart it, and the item has exactly one set of chunks. A 404 is not retried; a 503 is.
**Defend.**
- The worker dies after writing chunks but before marking the job done. What happens on restart, and why is it still correct?
- Checkpoint or idempotent write: which is the safety net?
- Which errors do you retry, and which don't you? Why?

## P4: CI, checks before merge

**San decides.**
- What must pass before code may enter `main`: lint? tests against a real database? migrations from an empty database? secret and security scans?
- What is slow enough to run only after merge instead?

**Agent builds.** A GitHub Actions workflow with your checks, made a required check in the P0 branch protection.
**Proof.** A PR with a failing test can't merge (screenshot). A deliberate semantic conflict (a function renamed on one branch, the old name called on another) merges without a Git conflict, and CI catches it. Each scan's log shows it really ran.
**Defend.**
- Why can a merge with no conflicts still break the code? What catches it?
- What must be true before code is allowed into `main`, and why each check?
- How does this map to Azure DevOps? (Workflow ≈ YAML pipeline, required check ≈ build validation policy, runner ≈ build agent.)

## P5: CD, build once and deploy many

**San decides.**
- Rebuild per environment, or build one image and promote it?
- Who approves a PROD deploy, and where does that rule live?
- When migrations run: before the new version starts, or after?

**Agent builds.** One Docker image per merge, tagged with the commit SHA. DEV deploys it; PROD reuses the same image behind an approval.
**Proof.** The same image digest in the DEV and PROD deploy logs. A PROD deploy waiting for approval (screenshot).
**Defend.**
- What goes wrong if each environment rebuilds the code?
- Branch policy vs environment approval: show where each one lives in this setup.
- A migration fails halfway through a deploy. What state is the app in, and what do you do?

## P6 (stretch): Infra as code on Azure

**San decides.** Which Azure services, and how secrets reach the app (CI variables, app settings, or Key Vault with a managed identity?).
**Agent builds.** Terraform for DEV, reusing patterns from RAG Galaxy's `infra/`.
**Defend.** Why define infrastructure as code? How does a secret get from Key Vault into the running app?

## P7 (stretch): Monitoring

**San decides.** The one failure you most need to hear about, and how you'd hear about it.
**Agent builds.** Structured logs, OpenTelemetry traces into Application Insights, and that one alert.
**Defend.** How would you find out a pipeline broke before a user does?

---

## Defend log

One line per question once San answers it cleanly without notes, including the follow-up.

| Date | Step | Question | Clean? |
| --- | --- | --- | --- |
