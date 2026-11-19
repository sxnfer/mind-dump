import AppKit
import SwiftUI

/// A borderless, non-activating panel that sits over the menu bar at the notch.
///
/// Three things make it different from a normal window:
/// 1. `.nonactivatingPanel`: clicking it does not steal focus from the app you type in.
/// 2. `level` above the main menu: it can cover the menu bar, where the notch is.
/// 3. `collectionBehavior`: it shows on every Space and over fullscreen apps.
final class NotchPanel: NSPanel {
    /// Extra width on each side of the notch, where the status dot sits.
    private static let earWidth: CGFloat = 36
    /// Size used on screens without a notch (an external display).
    private static let fallbackNotchWidth: CGFloat = 180

    init(rootView: some View) {
        super.init(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 1)
        collectionBehavior = [
            .canJoinAllSpaces,     // every desktop (Space)
            .fullScreenAuxiliary,  // allowed over a fullscreen app
            .stationary,           // stays put during Mission Control
            .ignoresCycle,         // skipped by Cmd-` window cycling
        ]
        // The window itself is clear; SwiftUI draws the true black shape with
        // rounded bottom corners, so it blends into the notch.
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        isMovable = false
        hidesOnDeactivate = false
        contentView = NSHostingView(rootView: rootView)
    }

    // M0 has nothing to type into. Typing arrives in M1, with the capture field.
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    /// Puts the panel top-centre on the screen with a notch, or on the main screen.
    func placeAtNotch() {
        guard let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 })
            ?? NSScreen.main
        else { return }

        let frame = screen.frame
        let height = max(screen.safeAreaInsets.top, NSStatusBar.system.thickness)

        // On a notched screen, the notch is the gap between the two menu bar areas.
        var notchWidth = Self.fallbackNotchWidth
        if let left = screen.auxiliaryTopLeftArea, let right = screen.auxiliaryTopRightArea {
            notchWidth = frame.width - left.width - right.width
        }
        let width = notchWidth + 2 * Self.earWidth

        // macOS screen coordinates start at the bottom-left, so the top is maxY.
        setFrame(
            NSRect(x: frame.midX - width / 2, y: frame.maxY - height, width: width, height: height),
            display: true
        )
    }
}
