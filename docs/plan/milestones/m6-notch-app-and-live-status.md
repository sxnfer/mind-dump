# M6: Notch app and live status

> Read this when: you are working on M6, or need its tasks and done-when list.

**Goal:** the full notch experience.
**Decisions needed:** D11, D23.
**Tasks:**
- The islandcn-style states in SwiftUI: idle, compact, split, expanded, with spring motion and one morphing black shape.
- Respect the system "Reduce motion" setting.
- Live status per item: polling first, then Server-Sent Events from the API.
- Left panel: search, filter chips, list. Right panel: overview. Library and + buttons.
- Global shortcut to open the panel.
- One-box capture: one input that takes anything (type, paste or drop) and sorts it for the user into a note, link or file. It replaces M1's clipboard check and its "Write a note instead" choice. Open design question for San: sort in the app or behind the API? (Raised by San on 2026-11-20 after testing M1; parked until after 26 Nov, because the proof track is backend only.)
- Measure smoothness: record open and close at the screen's full refresh rate; check for dropped frames in Instruments.
**Concepts:** API contracts, server push, animation performance.
**Done when:** a 30-second recording shows capture, live status and a cited answer with no visible stutter.
**Gate 2:** ask the panel a question and get answers with sources.
