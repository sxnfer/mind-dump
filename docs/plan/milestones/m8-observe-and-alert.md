# M8: Observe and alert

> Read this when: you are working on M8, or need its tasks and done-when list.

**Goal:** you know when the demo breaks before anyone tells you.
**Decisions needed:** D16.
**Tasks:**
- Structured JSON logs with a request id that follows a save from API to worker.
- Metrics: request time, error rate, queue length, job age, model cost per day.
- One target you promise (an SLO), for example: 99% of saves answer in under 300 ms.
- Alerts: API errors, a queue older than 10 minutes, daily cost over the cap.
- A one-page runbook: what to check when each alert fires.
**Concepts:** observability, SLOs, alerting, on-call basics.
**Done when:** you break something on purpose in staging and the alert reaches your email.
