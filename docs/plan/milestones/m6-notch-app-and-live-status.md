# M6: Notch app and live status

> Read this when: you are working on M6, or need its tasks and done-when list.

**Goal:** the full notch experience.
**Decisions needed:** D11, D23.
**Tasks:**
- [x] The island in SwiftUI: idle, peek (pointer over the notch), compact (a status pill) and expanded (the panel), with one black body whose width, height and corner each follow their own spring (slice 1, `app/MindDump/Island/`). The springs are islandcn's measurements of the iPhone's island (`IslandMotion.swift`). Not yet: split (two activities at once).
- [x] Respect the system "Reduce motion" setting: the shape eases for 0.2 s with no overshoot, and content cross-fades with no blur or scale (slice 1).
- [x] Live status per item, by polling (D11): the pill goes "Saved", "Reading page", "Ready", or says the page could not be read (slice 1). Server-Sent Events wait until the island shows several items.
- Left panel: search, filter chips, list. Right panel: overview. Library and + buttons.
- Global shortcut to open the panel.
- [x] One-box capture, typed or pasted (slice 1): one input sorts a web address into a link and anything else into a note, in the app, and says which before Return. Not yet: dropping a file. One-box capture: one input that takes anything (type, paste or drop) and sorts it for the user into a note, link or file. It replaces M1's clipboard check and its "Write a note instead" choice. Open design question for San: sort in the app or behind the API? (Raised by San on 2026-11-20 after testing M1; parked until after 26 Nov, because the proof track is backend only.)
- [x] Measure smoothness (slice 1): debug builds time every frame of every transition and print the late ones (`Support/Debug.swift`); `MD_MOTION_SCALE=8` gives slow motion, and the app can film its own window. Measured on a 120 Hz display: no late frame in any transition, except one late frame about 240 ms into the first open after launch (AppKit's first text focus). Instruments not used yet.
**Concepts:** API contracts, server push, animation performance.
**Done when:** a 30-second recording shows capture, live status and a cited answer with no visible stutter.
**Gate 2:** ask the panel a question and get answers with sources.
