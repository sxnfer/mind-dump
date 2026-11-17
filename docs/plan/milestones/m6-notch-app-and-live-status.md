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
- Measure smoothness: record open and close at the screen's full refresh rate; check for dropped frames in Instruments.
**Concepts:** API contracts, server push, animation performance.
**Done when:** a 30-second recording shows capture, live status and a cited answer with no visible stutter.
**Gate 2:** ask the panel a question and get answers with sources.
