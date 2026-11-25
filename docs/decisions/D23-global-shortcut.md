# D23: Global shortcut

Status: Approved by San, 2026-11-25
Problem: opening the capsule needs the mouse at the notch. From inside another app that is a reach and a click before you can type.
Without it: a mouse trip for every capture or question, which is the friction the island exists to remove.
Options: macOS's hot key API (`RegisterEventHotKey`), which registers one key combination with the system; or a key event monitor that watches every key press, which needs the Accessibility permission.
Chosen: the hot key API, with Option-Shift-Space as the default. It opens the capsule from any app with the keyboard in the box, and pressed again it closes it.
Why: no permission prompt, and the app never sees keys other than its own combination. A monitor would see everything you type, which a second brain should not ask for. It works inside the app's sandbox. Option-Shift-Space is not a macOS default; Option-Space is taken by other launchers (ChatGPT's app uses it). (San chose both from Claude's recommendation; his own reasons are not recorded yet.)
Cost: the API is from the old Carbon framework, in C, so it needs a small wrapper and a C callback. The combination is fixed in code until a settings screen exists. If another app already holds it, registration fails and the app must say so.
We will switch if: Apple removes the API, or a shortcut the user can record in a settings screen is needed, which may be easier with a different approach.

Not a new dependency: Carbon's HIToolbox ships with macOS.
