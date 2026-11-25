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
    var takesKeyboard = false {
        // An open panel is not a drop target: its text box takes drops its own way.
        didSet { hosting?.takesDrops = !takesKeyboard }
    }

    private(set) var screenFrame: NSRect = .zero

    /// Takes text dragged onto the island (drop-and-save, `DropModel`).
    weak var dropReceiver: DropReceiving?
    private var hosting: (any DropHosting)?

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
        let hosting = IslandHostingView(rootView: rootView)
        hosting.takesDrops = true
        contentView = hosting
        self.hosting = hosting
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

/// What a closed island accepts as a drop: plain text, and links from a browser. Files and
/// images are not listed, so AppKit turns those away before we see them (most also carry
/// text or a URL; `DropModel` refuses those itself).
private let dropTypes: [NSPasteboard.PasteboardType] = [.string, .URL]

@MainActor
private protocol DropHosting: AnyObject {
    var takesDrops: Bool { get set }
}

/// The window's SwiftUI host, with a drop catcher kept in front of everything in it.
///
/// Why a catcher of our own, in front: AppKit gives a drop to the front-most view under the
/// pointer that registered for its types. The panel's text box stays focused while the
/// panel is closed (taking focus again on every open cost a late frame), so its text
/// editor stays in the window, hidden, right where the drop target opens, and it takes
/// text. A view in front of it wins. While the panel is open, nothing here takes drops,
/// so the box behaves as macOS made it.
///
/// The host registers too, in case AppKit asks `hitTest` (the catcher answers nil there,
/// so clicks pass through to SwiftUI). Both forward to the same receiver.
final class IslandHostingView<Content: View>: NSHostingView<Content>, DropHosting {
    private lazy var catcher: DropCatcher = {
        let view = DropCatcher(frame: bounds)
        view.autoresizingMask = [.width, .height]
        return view
    }()

    var takesDrops = false {
        didSet {
            if catcher.superview == nil { addSubview(catcher) }
            if takesDrops {
                registerForDraggedTypes(dropTypes)
                catcher.registerForDraggedTypes(dropTypes)
            } else {
                unregisterDraggedTypes()
                catcher.unregisterDraggedTypes()
            }
        }
    }

    /// SwiftUI adds its own views (a text field's, a scroll view's) as it needs them; the
    /// catcher stays in front of each one.
    override func didAddSubview(_ subview: NSView) {
        super.didAddSubview(subview)
        guard subview !== catcher, catcher.superview === self else { return }
        addSubview(catcher, positioned: .above, relativeTo: nil)
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        catcher.draggingEntered(sender)
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        catcher.draggingUpdated(sender)
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        catcher.draggingExited(sender)
    }

    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        true
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        catcher.performDragOperation(sender)
    }

    override func draggingEnded(_ sender: NSDraggingInfo) {
        catcher.draggingEnded(sender)
    }
}

/// A see-through view that only receives drags and hands them to `DropModel`. Only a real
/// drag from another app runs these lines; a debug script calls `DropModel` directly.
private final class DropCatcher: NSView {
    private var receiver: DropReceiving? { (window as? NotchWindow)?.dropReceiver }

    /// Clicks, hovers and scrolls pass through to the island underneath.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        receiver?.draggingEntered(sender) ?? []
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        receiver?.draggingUpdated(sender) ?? []
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        receiver?.draggingExited()
    }

    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        true
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        receiver?.performDragOperation(sender) ?? false
    }

    override func draggingEnded(_ sender: NSDraggingInfo) {
        receiver?.draggingEnded()
    }
}
