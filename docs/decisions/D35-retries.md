# D35: Retries

Status: Approved, 2026-11-24
Problem: fetching a page fails sometimes. Some failures pass (a timeout, a busy server), and some never will (a page that doesn't exist). The worker must tell them apart, so it doesn't give up too early or hammer a site forever.
Without a tool: no retries, so one network blip leaves an item unprocessed until someone notices. Or retry everything, so a 404 is asked again and again and gets the same answer each time.
Options: no retries; retry every failure; retry only temporary failures. How many tries, and how long to wait between them.
Chosen: retry only temporary failures, at most 3 tries in all, waiting longer each time.
- Temporary (retry): timeouts, connection errors, 429 Too Many Requests, and any 5xx.
- Permanent (no retry): every other 4xx, such as 404 and 410. The fetch is still recorded (D28), and the job ends `dead` at once with the reason.
- Waits: 1 minute before the second try, 5 minutes before the third (growing wait, so a struggling site gets room to recover).
- After the third failure the job is `dead`, and the item shows the error.
- A try counts when it is claimed, not when it fails, so a job that crashes the worker every time still runs out of tries instead of looping forever.
Why (San): asking again only helps when the answer can change. A 503 or a timeout often passes; a 404 doesn't. A growing wait is polite to a site that is already struggling, and three tries cover a short outage without delaying a dead item for long.
Cost: the list of temporary statuses is a rule we maintain. A page that is down for longer than the waits ends `dead`, and needs a manual re-run.
We will switch if: many items end `dead` from outages that later pass. Then add more tries or longer waits, or honour the site's `Retry-After` header.
