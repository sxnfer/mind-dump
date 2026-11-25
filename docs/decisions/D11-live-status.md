# D11: Live status

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; San to review)
Problem: saving answers at once, and the worker reads the page seconds later (D6). The island must show what is happening to the thing you just saved: "Reading page", then "Ready", or that it failed.
Without a tool: the island says "Saved" and nothing more. A page that could not be read fails silently, and you only find out when a search does not find it.
Options: the app asks the API on a timer (polling); the API pushes events over one long request (Server-Sent Events); a two-way socket (WebSockets).
Chosen: polling, for the one item just saved.
- After a save the app waits 0.9 s (so "Saved" can be read), then asks `GET /items/{id}` every 0.5 s until the item is `ready` or `error`, for at most a minute.
- The pill only changes when there is something to say: a note that is done before the first ask goes straight from "Saved" to gone.
Why: the app follows one item for a few seconds, so a timer costs two or three small requests. It needs no new endpoint and no open connection to keep alive, and it works through a restart of the backend. Server-Sent Events earn their place when many items change at once (a batch upload with progress), which the island does not show yet.
Cost: up to half a second of delay before the pill changes, and a request every half second while something is processing. No progress within a step ("Uploading 40%") is possible this way.
We will switch if: the island shows several items at once, or upload progress. Then Server-Sent Events from the API, which already streams answers that way (M5).
