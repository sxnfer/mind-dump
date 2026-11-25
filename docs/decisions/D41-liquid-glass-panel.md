# D41: Liquid Glass panel and the macOS 26 floor

Status: Approved by San, 2026-11-25
Problem: the open panel's layout needed a rework. San wants it smooth, minimal and premium, and wants Apple's Liquid Glass. Liquid Glass exists only on macOS 26 and later, and the app targeted macOS 14.
Without it: the panel stays a flat black sheet with a mode switch, a box and a hint. Glass on a black body has almost nothing to bend, so it would not read as glass.
Options (layout): six directions were drawn in Capture, Ask and Drop (A black body with glass controls, B glass sheet under a black tab, C one glass capsule that grows, D wings left and right of the notch, E thread with a docked input, F one box with action rows).
Options (platform): raise the deployment target to macOS 26; or keep macOS 14 and fall back to flat fills on older Macs.
Chosen: layout C, "One line". A single Liquid Glass capsule sits just under the notch, apart from it. A round button (or Tab) flips Capture and Ask. For an answer the same capsule grows into a card. A text drag brings the capsule up as the drop target. The notch itself stays true black: the hover tab and the status pills do not change. It replaces the old panel outright; there is no switch back. The deployment target moves to macOS 26.0.
Why: C has the fewest parts, and a capsule that grows into a card is what the glass morphing API (`GlassEffectContainer`, `glassEffectID`) is made for. The notch is black hardware, so anything that touches it must be black; a glass shape has to sit apart from it. One platform floor means one code path to build and test. (San chose both from Claude's recommendation; his own reasons are not recorded yet.)
Cost: people on macOS 14 or 15 can no longer build the app. No room in the capsule for older turns of a conversation. Only APIs marked macOS 26.0 may be used, because CI builds with Xcode 26 while San's Mac has Xcode 27. No fallback layout for the demo on 26 Nov.
We will switch if: the glass cannot stay readable over bright windows, or it cannot hold 120 Hz while an answer streams; then B (a calmer sheet) or A (black body) are the next to try.

Not a new dependency: Liquid Glass is part of SwiftUI and AppKit on macOS 26.
