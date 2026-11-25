import AppKit

/// Watches the pointer in screen coordinates and tells the island when it is over it.
///
/// Why not SwiftUI's `onHover`: the window is see-through and ignores the mouse, so that
/// the menu bar and the apps beneath stay clickable. A window that ignores the mouse gets
/// no hover events. So this asks the system where the pointer is, compares it with where
/// the island is, and switches the window's "ignore the mouse" flag on and off.
///
/// Mouse monitors need no Accessibility permission (keyboard monitors would).
@MainActor
final class PointerTracker {
    private let window: NotchWindow
    private let island: IslandModel
    private var monitors: [Any] = []
    /// Debug scripts move this instead of the real pointer. nil means use the real one.
    var scriptedPointer: NSPoint? {
        didSet { pointerMoved() }
    }

    private var pointer: NSPoint { scriptedPointer ?? NSEvent.mouseLocation }

    init(window: NotchWindow, island: IslandModel) {
        self.window = window
        self.island = island
    }

    func start() {
        let moved: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged]
        // Global: the pointer is over another app. Local: it is over our window.
        add(NSEvent.addGlobalMonitorForEvents(matching: moved) { [weak self] _ in
            Task { @MainActor in self?.pointerMoved() }
        })
        add(NSEvent.addLocalMonitorForEvents(matching: moved) { [weak self] event in
            self?.pointerMoved()
            return event
        })
        // A click anywhere else closes an open panel.
        add(NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { [weak self] _ in
            Task { @MainActor in self?.clickedElsewhere() }
        })
        // Escape closes it too, wherever the keyboard focus is inside the panel.
        add(NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard event.keyCode == 53, let self, self.island.presentation.isPanel else { return event }
            self.island.close()
            return nil
        })
        pointerMoved()
    }

    private func add(_ monitor: Any?) {
        if let monitor { monitors.append(monitor) }
    }

    /// Re-check after the island changes size, even if the pointer did not move.
    func islandChanged() {
        pointerMoved()
    }

    private func pointerMoved() {
        let inside = hotZone.contains(pointer)
        // Only take clicks while the pointer is on the island.
        if window.ignoresMouseEvents == inside { window.ignoresMouseEvents = !inside }
        island.hover(inside)
    }

    func clickedElsewhere() {
        guard island.presentation.isPanel, !hotZone.contains(pointer) else { return }
        island.close()
    }

    /// The island's rect with a margin. Two sizes on purpose (hysteresis): easy to enter
    /// at the notch, and once grown you must leave the grown shape, so the hover does not
    /// flap when the body moves under a still pointer.
    private var hotZone: NSRect {
        let body = window.bodyRect(for: island.metrics)
        let rect: NSRect =
            switch island.presentation {
            case .idle: body.insetBy(dx: -10, dy: 0).offsetBy(dx: 0, dy: -5)
            case .peek, .compact: body.insetBy(dx: -12, dy: 0).offsetBy(dx: 0, dy: -8)
            case .panel: body.insetBy(dx: -14, dy: 0).offsetBy(dx: 0, dy: -14)
            }
        // Reach past the top edge: at the very top row the pointer's y equals the
        // screen's maxY, which a rect does not contain.
        return NSRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height + 24)
    }
}
