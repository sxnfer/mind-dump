import AppKit
import SwiftUI

/// A fixed, see-through panel at the top of the screen that the island is drawn inside.
///
/// It never moves and never resizes. The island's body changes size inside it, driven by
/// SwiftUI springs. (The first version animated this window's frame instead: AppKit then
/// runs its own fixed curve, the content re-lays out at every step, and nothing can be
/// retargeted mid-flight. That is what made it feel stiff.)
///
/// Three more things make it different from a normal window:
/// 1. `.nonactivatingPanel`: it can take your keystrokes without making Mind Dump the
///    active app, so the app you were in stays in front (like Spotlight).
/// 2. `level` above the main menu: it can cover the menu bar, where the notch is.
/// 3. `collectionBehavior`: it shows on every Space and over fullscreen apps.
final class NotchWindow: NSPanel {
    /// Big enough for the open panel, its shoulders and its shadow.
    private static let size = NSSize(
        width: IslandLayout.panelWidth + 2 * (IslandLayout.shadowRoom + 12),
        height: IslandLayout.maxPanelHeight + IslandLayout.shadowRoom
    )

    /// Only an open panel takes keystrokes; a closed island never steals them.
    var takesKeyboard = false

    private(set) var screenFrame: NSRect = .zero

    init(rootView: some View) {
        super.init(
            contentRect: NSRect(origin: .zero, size: Self.size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 3)
        collectionBehavior = [
            .canJoinAllSpaces,     // every desktop (Space)
            .fullScreenAuxiliary,  // allowed over a fullscreen app
            .stationary,           // stays put during Mission Control
            .ignoresCycle,         // skipped by Cmd-` window cycling
        ]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false          // SwiftUI draws the shadow, so it can follow the springs
        isMovable = false
        hidesOnDeactivate = false
        animationBehavior = .none  // AppKit must not animate this window at all
        // Clicks go straight through until the pointer is on the island (AppDelegate).
        ignoresMouseEvents = true
        contentView = NSHostingView(rootView: rootView)
    }

    override var canBecomeKey: Bool { takesKeyboard }
    override var canBecomeMain: Bool { false }

    /// Pins the window top-centre on the screen with a notch (or the main screen) and
    /// returns that screen's notch measurements.
    @discardableResult
    func placeAtNotch() -> IslandLayout {
        guard let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 })
            ?? NSScreen.main
        else { return .fallback }

        let frame = screen.frame
        screenFrame = frame
        setFrame(
            // macOS screen coordinates start at the bottom-left, so the top is maxY.
            NSRect(
                x: frame.midX - Self.size.width / 2, y: frame.maxY - Self.size.height,
                width: Self.size.width, height: Self.size.height
            ),
            display: true
        )

        // On a notched screen, the notch is the gap between the two menu bar areas.
        if let left = screen.auxiliaryTopLeftArea, let right = screen.auxiliaryTopRightArea {
            return IslandLayout(
                notch: CGSize(
                    width: frame.width - left.width - right.width,
                    height: screen.safeAreaInsets.top
                ),
                hasNotch: true
            )
        }
        return IslandLayout(
            notch: CGSize(width: IslandLayout.fallback.notch.width, height: NSStatusBar.system.thickness),
            hasNotch: false
        )
    }

    /// Where a body of this size sits on screen (it hangs from the top, centred).
    func bodyRect(for metrics: IslandMetrics) -> NSRect {
        NSRect(
            x: screenFrame.midX - metrics.width / 2, y: screenFrame.maxY - metrics.height,
            width: metrics.width, height: metrics.height
        )
    }
}
