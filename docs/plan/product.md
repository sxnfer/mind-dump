# Product: what Mind Dump does

> Read this when: you work on a user-facing feature, the design or animation, or anything about who runs the app and who pays.

| Job | User story | What it demands from the backend |
| --- | --- | --- |
| Capture | I drop a link, file, image or note on the notch panel. It says "Saved" at once. | A fast write path. Slow work must not happen while I wait. |
| Process | Later, the app reads the web page, pulls text from the PDF, describes the image and adds tags. | Background work that can fail, retry and never save a duplicate. |
| Find | I type "that article on vector search from last week" and get the item. | Search by meaning and by words, filtered by date and type. |
| Recall | I ask a question. Each claim in the answer links to the exact item, page and passage. | Every chunk keeps its source: item id, page, character position. Hybrid search, then an optional rerank. |
| Discuss | I click a citation and chat about that passage: "explain this simply", "give an example". | Search limited to that one item (a metadata filter: `WHERE item_id = 42`), chat history, answers that stream word by word. |
| Trust | I can see when the answer is not in my items. | A grounding check, and an eval set that measures citation accuracy. |
| Manage | I edit tags, delete an item, or export everything. | Correct deletes (file and database row), and data I own. |
| Live status | The panel shows "Uploading 40%", "Reading page", "Ready". | A way for the server to push status to the app. |

Things the user never sees, but the engineer owns: only the owner sees the items, the bill stays small, one failed item does not block the others, and the owner knows when something breaks.

## Who runs it and who pays

Mind Dump is open source. Each person downloads it from GitHub and runs their own copy on their own Mac. Each copy has one owner.

- The backend runs on the owner's Mac with one command (`docker compose up`). Servers cost the owner nothing.
- The owner puts their own AI model API key in a local settings file ("bring your own key"), or uses a free local model.
- The repo never contains a key. CI and GitHub secret scanning check this.
- You (San) pay only for your own demo deploy on Azure (M7). It scales to zero when nobody uses it. Rough guess: a few pounds up to about £20 a month. This is not measured yet; the budget alert gives the real number.

## Design direction

True black (`#000`) panels that read as part of the display, like the iPhone notch. On an OLED screen, true black pixels are off, so the panel melts into the bezel. No glass, no blur. Panels hug the screen edges near the notch:

- a left panel with search, filter chips (All, Recents, Pinned) and a list,
- a right panel with an overview,
- a Library button and a + button for quick add on each panel.

The panels float over every other app, including fullscreen apps, and open with a global shortcut.

**Animation reference: islandcn.** We do not use its code (it is React, and the app is native Swift). We study its design and rebuild it in SwiftUI:

| islandcn idea | What it does | SwiftUI version |
| --- | --- | --- |
| Four presentations: idle, compact, split, expanded | The island has set sizes, nothing in between | An `enum` of states; the view draws from the current state |
| Spring motion | The shape overshoots a little, then settles | Built-in spring animations at the screen's full refresh rate |
| Shape morph | The black pill grows into a panel | One shape whose size and corner radius animate; content moves between states |
| Activity stack | Several jobs at once (an upload and a timer) | A list of activities; the top one decides what the island shows |
| Long-press, reduced motion | Gestures, and respect for the system setting | Built-in gestures and the system "Reduce motion" flag |

Source: https://github.com/opencoredev/islandcn

**What the source says (read on 2026-11-25).** The table above was written from islandcn's demo. Its code is more precise:

- It uses no animation library. Every spring is given in SwiftUI's own terms (`response`, `dampingFraction`), fitted frame by frame to recordings of the iPhone's island. We use those numbers as they are (`app/MindDump/Island/IslandMotion.swift`).
- Width, height and corner radius each have their own spring, and the corner starts about 20 ms after the height. That is why the shape seems to breathe instead of scaling like a box.
- Most moves barely overshoot (about 1%). Only a pill appearing bounces visibly (about 8%).
- A collapse first aims at the bare notch, then opens out to the pill. Content leaves fast and at once, and arrives late, with a blur.
- It has no hover: a phone has no pointer. Our peek on hover is our own.
