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
        // A click anywhere else closes the open capsule: on release, and only if the pointer
        // did not travel. A press that becomes a drag (text dragged towards the island) must
        // not close it.
        add(NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { [weak self] _ in
            Task { @MainActor in self?.pressedAt = NSEvent.mouseLocation }
        })
        add(NSEvent.addGlobalMonitorForEvents(matching: .leftMouseUp) { [weak self] _ in
            Task { @MainActor in self?.releasedElsewhere() }
        })
        // Escape closes it too, wherever the keyboard focus is inside the capsule.
        add(NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard event.keyCode == 53, let self, self.island.presentation.isOpen else { return event }
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

    /// Where a press in another app began, to tell a click from a drag on release.
    private var pressedAt: NSPoint?

    private func releasedElsewhere() {
        guard let start = pressedAt else { return }
        pressedAt = nil
        let end = NSEvent.mouseLocation
        guard hypot(end.x - start.x, end.y - start.y) < 6 else { return }
        clickedElsewhere()
    }

    /// Is this screen point on the island? Drop-and-save asks, during a real drag.
    func contains(_ point: NSPoint) -> Bool {
        hotZone.contains(point)
    }

    func clickedElsewhere() {
        guard island.presentation.isOpen, !hotZone.contains(pointer) else { return }
        island.close()
    }

    /// The island's rect with a margin. Two sizes on purpose (hysteresis): easy to enter
    /// at the notch, and once grown you must leave the grown shape, so the hover does not
    /// flap when the body moves under a still pointer.
    ///
    /// While the capsule is out, the zone is two rects, not one box around both: the notch
    /// (a click there closes) and the capsule reaching up to it. One box would cover the
    /// menu bar beside the notch, and swallow clicks on its menus.
    private var hotZone: HotZone {
        let body = window.bodyRect(for: island.metrics)
        let top = { (rect: NSRect) in
            // Reach past the top edge: at the very top row the pointer's y equals the
            // screen's maxY, which a rect does not contain.
            NSRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height + 24)
        }
        switch island.presentation {
        case .idle:
            return HotZone(rects: [top(body.insetBy(dx: -10, dy: 0).offsetBy(dx: 0, dy: -5))])
        case .peek, .compact:
            return HotZone(rects: [top(body.insetBy(dx: -12, dy: 0).offsetBy(dx: 0, dy: -8))])
        case .open, .dropTarget:
            // A drop target should be easy to hit and must not flap: a wider margin.
            let margin: CGFloat = island.presentation.isOpen ? 14 : 24
            let glass = window.capsuleRect(for: island.capsule)
            // From the bottom of the margin up to the notch, so the gap between is inside.
            let capsule = NSRect(
                x: glass.minX - margin, y: glass.minY - margin,
                width: glass.width + 2 * margin, height: body.minY - glass.minY + margin
            )
            return HotZone(rects: [top(body.insetBy(dx: -margin, dy: 0)), capsule])
        }
    }
}

/// One or more screen rects that count as "on the island".
private struct HotZone {
    let rects: [NSRect]

    func contains(_ point: NSPoint) -> Bool {
        rects.contains { $0.contains(point) }
    }
}
