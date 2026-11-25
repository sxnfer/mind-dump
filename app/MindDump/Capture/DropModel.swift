import AppKit
import UniformTypeIdentifiers

/// Drop-and-save: text dragged from any app onto the island is saved when you let go, with
/// no click and no key press. The lowest-friction way in.
///
/// Three plain methods carry the whole flow: `entered`, `exited`, `dropped`. Both callers
/// use them: AppKit's drag destination (the extension at the bottom, a thin adapter) and
/// the debug script (`drag`, `dragexit`, `drop`). So the script exercises everything except
/// reading the drag pasteboard and AppKit's own calls.
///
/// The saving itself is `CaptureModel.store`, the same code as typed capture: the same
/// "Saved" pill, the same live status, the same "Already saved".
@MainActor
final class DropModel {
    /// What a release would save: the text, trimmed, and whether it is a note or a link.
    struct Payload: Equatable {
        let text: String
        let kind: CaptureModel.Kind
    }

    private let panel: PanelModel
    private let island: IslandModel
    private var capture: CaptureModel { panel.capture }

    /// What the drag over the island carries. nil when no drag is over it.
    private(set) var current: Payload?
    private var isSaving = false
    private var watchdog: Task<Void, Never>?
    /// Real drags only: is this screen point on the island? (The pointer tracker's hot zone,
    /// which is generous while the target shows.)
    var zoneContains: ((NSPoint) -> Bool)?
    /// Real drags only: a drag we already turned down (a file, an image), so the pasteboard
    /// is not read again on every move.
    private var refusedDrag: Int?

    init(panel: PanelModel, island: IslandModel) {
        self.panel = panel
        self.island = island
    }

    /// The note-or-link rule is Capture's (`webURL(in:)`); there is no second one here.
    static func payload(text: String) -> Payload? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return Payload(text: trimmed, kind: CaptureModel.webURL(in: trimmed).map(CaptureModel.Kind.link) ?? .note)
    }

    // MARK: The flow

    /// A drag arrived. Returns false when the island will not take it: nothing to save,
    /// the capsule is open (its box takes drops its own way), or a drop is still saving.
    @discardableResult
    func entered(_ payload: Payload?, watchButton: Bool = false) -> Bool {
        guard let payload, !isSaving, !island.presentation.isOpen else { return false }
        current = payload
        island.showDropTarget(Self.target(for: payload))
        if watchButton { startWatchdog() }
        return true
    }

    /// The drag left, or was cancelled. Nothing is saved; the island goes back.
    func exited() {
        watchdog?.cancel()
        guard current != nil, !isSaving else { return }
        current = nil
        island.hideDropTarget()
    }

    /// Released over the island: save at once. Returns false if it was not taken.
    @discardableResult
    func dropped(_ payload: Payload?) -> Bool {
        watchdog?.cancel()
        guard let payload = payload ?? current, !isSaving, !island.presentation.isOpen else {
            exited()
            return false
        }
        current = nil
        isSaving = true
        island.dropTargetReleased()
        Task { await save(payload) }
        return true
    }

    private func save(_ payload: Payload) async {
        // Usually the backend answers in a few milliseconds and "Saved" is the next thing
        // shown. If it is slow, say so instead of holding a target that looks unanswered.
        let slow = Task { [island] in
            try? await Task.sleep(for: IslandMotion.seconds(0.3))
            if !Task.isCancelled { island.dropTargetSaving() }
        }
        defer {
            slow.cancel()
            isSaving = false
        }
        do {
            try await capture.store(payload.kind, note: payload.text)
        } catch {
            // Nothing was saved. Open the capsule with the words in the box and the reason
            // under them, so Return retries. Never a silent loss, never a false "Saved".
            slow.cancel()
            panel.openCapture(keeping: payload.text, error: error.localizedDescription)
        }
    }

    private static func target(for payload: Payload) -> DropTarget {
        switch payload.kind {
        case .link(let url):
            let path = url.path() == "/" ? "" : url.path()
            return DropTarget(isLink: true, detail: (url.host() ?? url.absoluteString) + path)
        case .note, .empty:
            // The first words, on one line.
            let words = payload.text.split(whereSeparator: \.isWhitespace).prefix(24).joined(separator: " ")
            return DropTarget(isLink: false, detail: words)
        }
    }

    /// Real drags only. If AppKit never says the drag left (the window stopped taking the
    /// mouse mid-drag, or the drag was cancelled in a way it did not report), the target
    /// must not stay. Every drag ends with the left button up, so look at the button a few
    /// times a second, only while a drag is over the island. Two looks in a row, so a
    /// release has time to reach `performDragOperation` first.
    private func startWatchdog() {
        watchdog?.cancel()
        watchdog = Task { [weak self] in
            var upCount = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(200))
                guard let self, !Task.isCancelled else { return }
                upCount = NSEvent.pressedMouseButtons & 1 == 0 ? upCount + 1 : 0
                if upCount >= 2 {
                    self.exited()
                    return
                }
            }
        }
    }
}

// MARK: - Real drags (AppKit)

/// What the island's hosting view forwards from AppKit (see `IslandHostingView`).
@MainActor
protocol DropReceiving: AnyObject {
    func draggingEntered(_ info: NSDraggingInfo) -> NSDragOperation
    func draggingUpdated(_ info: NSDraggingInfo) -> NSDragOperation
    func draggingExited()
    func performDragOperation(_ info: NSDraggingInfo) -> Bool
    func draggingEnded()
}

/// Only a real drag from another app runs this part. It turns AppKit's calls into the
/// three methods above, and it is the only place that reads the drag pasteboard: only
/// while a drag is inside this window, never in the background, and never logged.
extension DropModel: DropReceiving {
    func draggingEntered(_ info: NSDraggingInfo) -> NSDragOperation {
        guard isOnIsland(info), refusedDrag != info.draggingSequenceNumber else { return [] }
        guard entered(Self.payload(from: info.draggingPasteboard), watchButton: true) else {
            refusedDrag = info.draggingSequenceNumber
            return []
        }
        return .copy
    }

    func draggingUpdated(_ info: NSDraggingInfo) -> NSDragOperation {
        // The window is bigger than the island: a drag inside it but off the island is
        // not a target.
        guard isOnIsland(info) else {
            exited()
            return []
        }
        if current == nil { return draggingEntered(info) }
        return .copy
    }

    func draggingExited() {
        exited()
    }

    func performDragOperation(_ info: NSDraggingInfo) -> Bool {
        guard isOnIsland(info) else { return false }
        return dropped(Self.payload(from: info.draggingPasteboard))
    }

    func draggingEnded() {
        exited()  // does nothing after a drop: that is saving
    }

    private func isOnIsland(_ info: NSDraggingInfo) -> Bool {
        guard let window = info.draggingDestinationWindow else { return false }
        let point = window.convertPoint(toScreen: info.draggingLocation)
        return zoneContains?(point) ?? false
    }

    /// Text, or a link from a browser. Files and images are a later slice: refuse them,
    /// even when they also carry a name or an address as text.
    static func payload(from pasteboard: NSPasteboard) -> Payload? {
        let types = pasteboard.types ?? []
        let isFileOrImage = types.contains { type in
            type == .fileURL
                || type.rawValue.contains("promised-file") || type.rawValue.contains("FilePromise")
                || UTType(type.rawValue)?.conforms(to: .image) == true
        }
        if isFileOrImage { return nil }
        // A link dragged from a browser carries the address as a URL (and its title as text).
        if let url = (pasteboard.readObjects(forClasses: [NSURL.self]) as? [URL])?.first, !url.isFileURL,
            let payload = payload(text: url.absoluteString), case .link = payload.kind
        {
            return payload
        }
        return pasteboard.string(forType: .string).flatMap(payload(text:))
    }
}
