import AppKit
import UniformTypeIdentifiers

/// Drop-and-save: text, or one file, dragged from any app onto the island is saved when
/// you let go, with no click and no key press. The lowest-friction way in.
///
/// Three plain methods carry the whole flow: `entered`, `exited`, `dropped`. Both callers
/// use them: AppKit's drag destination (the extension at the bottom, a thin adapter) and
/// the debug script (`drag`, `dragfile`, `dragexit`, `drop`, `dropfile`). So the script
/// exercises everything except reading the drag pasteboard and AppKit's own calls.
///
/// The saving itself is `CaptureModel`'s, the same code as typed capture: the same
/// "Saved" pill, the same live status, the same "Already saved".
@MainActor
final class DropModel {
    /// What a release would save, or why it would not.
    enum Payload: Equatable {
        /// Text, trimmed, and whether it is a note or a link.
        case text(String, CaptureModel.Kind)
        /// One file the backend can take (judged by its name, type and size only).
        case file(DroppedFile)
        /// A drag the island turns down, with the reason it shows while the drag is over
        /// it. A release is refused, so the drag flies back.
        case refused(Refusal, name: String?)
    }

    /// A file on disk, as far as the island looks at it while hovering: its place, name
    /// and size. Its bytes are read only after a release, and only by the upload.
    struct DroppedFile: Equatable {
        let url: URL
        /// As the user knows it (`IMG_0042.HEIC`); the drop target shows this name.
        let name: String
        /// nil when the size could not be read; the backend's 413 is then the check.
        let size: Int?
        /// An image, which may be converted or shrunk before upload (`ImagePrep`, D42).
        /// Otherwise a PDF, sent as it is.
        var isImage = false
    }

    enum Refusal: Equatable {
        case unsupportedType, tooLarge, empty, severalFiles

        /// Shown in the drop target. Plain words, short enough for one line.
        var message: String {
            switch self {
            // Any image the Mac can open is taken (D42), so the words name the kind, not a
            // list of formats that would not fit on one line.
            case .unsupportedType: "Only PDFs and images"
            case .tooLarge: "Too big: 25 MB at most"
            case .empty: "This file is empty"
            case .severalFiles: "One file at a time"
            }
        }
    }

    /// The backend's own limit (`MAX_UPLOAD_BYTES` in `uploads.py`), checked here first so
    /// a PDF that is too big is refused before a byte is sent. An image is never refused
    /// for its size: what is sent is at most 7.5 MB (`ImagePrep.maxBytes`).
    static let maxFileBytes = 25 * 1024 * 1024

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
    /// Real drags only: a drag we already turned down with nothing to show (a file
    /// promise, an image from a browser), so the pasteboard is not read again on every move.
    private var refusedDrag: Int?

    init(panel: PanelModel, island: IslandModel) {
        self.panel = panel
        self.island = island
    }

    /// The note-or-link rule is Capture's (`webURL(in:)`); there is no second one here.
    static func payload(text: String) -> Payload? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return .text(trimmed, CaptureModel.webURL(in: trimmed).map(CaptureModel.Kind.link) ?? .note)
    }

    /// The files a drag carries. One supported file (a PDF, or any image the Mac can open)
    /// is a payload; anything else is a refusal with its reason. Only metadata is read
    /// here (type, size, is it a folder), never the contents: this runs while the drag is
    /// still moving.
    static func payload(files urls: [URL]) -> Payload? {
        guard let url = urls.first else { return nil }
        guard urls.count == 1 else { return .refused(.severalFiles, name: nil) }
        // The file system hands names back decomposed ("e" + accent); save the form people
        // type, so a search for "résumé" finds it.
        let name = url.lastPathComponent.precomposedStringWithCanonicalMapping
        let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .contentTypeKey, .fileSizeKey])
        if values?.isDirectory == true { return .refused(.unsupportedType, name: name) }
        // The system's idea of its type, or failing that its extension. The backend
        // decides for certain from the first bytes (a 415 if the name lied).
        let type = values?.contentType ?? UTType(filenameExtension: url.pathExtension)
        guard let type else { return .refused(.unsupportedType, name: name) }
        let isImage = !type.conforms(to: .pdf) && ImagePrep.canOpen(type)
        guard isImage || type.conforms(to: .pdf) else { return .refused(.unsupportedType, name: name) }
        let size = values?.fileSize
        if !isImage, let size, size > maxFileBytes { return .refused(.tooLarge, name: name) }
        if size == 0 { return .refused(.empty, name: name) }
        return .file(DroppedFile(url: url, name: name, size: size, isImage: isImage))
    }

    // MARK: The flow

    /// A drag arrived. Returns true when a release would save it. A refusal still shows
    /// the target, with its reason, and returns false. Nothing shows when there is
    /// nothing to save, the capsule is open (its box takes drops its own way), or a drop
    /// is still saving.
    @discardableResult
    func entered(_ payload: Payload?, watchButton: Bool = false) -> Bool {
        guard let payload, !isSaving, !island.presentation.isOpen else { return false }
        current = payload
        island.showDropTarget(Self.target(for: payload))
        if watchButton { startWatchdog() }
        if case .refused = payload { return false }
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
        if case .refused = payload {
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
        // shown. If it is slow (a big file, or a photo being converted), say so instead of
        // holding a target that looks unanswered.
        let slow = Task { [island] in
            try? await Task.sleep(for: IslandMotion.seconds(0.3))
            if !Task.isCancelled { island.dropTargetSaving() }
        }
        defer {
            slow.cancel()
            isSaving = false
        }
        switch payload {
        case .text(let text, let kind):
            do {
                try await capture.store(kind, note: text)
            } catch {
                // Nothing was saved. Open the capsule with the words in the box and the
                // reason under them, so Return retries. Never a silent loss, never a false
                // "Saved".
                slow.cancel()
                panel.openCapture(keeping: text, error: error.localizedDescription)
            }
        case .file(let file):
            do {
                let upload = try await Self.prepare(file)
                try await capture.storeFile(upload.body, name: upload.name)
            } catch {
                // Nothing was saved, and the file is still where it was, so nothing is lost.
                // The capsule stays shut (a text box cannot hold a file); a warning pill
                // says why, then folds away.
                slow.cancel()
                island.show(
                    IslandActivity(id: "file-failed", glyph: .warning, text: Self.pillText(for: error), hold: 3)
                )
            }
        case .refused:
            return  // `dropped` never gets here
        }
    }

    /// A PDF goes as it is. An image is made to fit Claude first (D42), away from the main
    /// thread: converting a 12-megapixel HEIC takes a moment, and the island must keep
    /// animating meanwhile.
    private static func prepare(_ file: DroppedFile) async throws -> ImagePrep.Prepared {
        guard file.isImage else { return ImagePrep.Prepared(body: .file(file.url), name: file.name, change: nil) }
        let (url, name) = (file.url, file.name)
        #if DEBUG
        let started = ContinuousClock.now
        #endif
        let prepared = try await Task.detached(priority: .userInitiated) {
            try ImagePrep.prepare(url, name: name)
        }.value
        #if DEBUG
        let took = (ContinuousClock.now - started).formatted(.units(allowed: [.milliseconds]))
        print("[image] \(prepared.change.map(String.init(describing:)) ?? "sent unchanged") in \(took)")
        fflush(stdout)
        #endif
        return prepared
    }

    /// A failed upload in a few words (a pill holds about 22 characters).
    private static func pillText(for error: Error) -> String {
        // The file could not be decoded (damaged, or not really an image): nothing was sent.
        if error as? ImagePrep.Failure == .unreadable { return "Couldn't read the image" }
        return switch error as? APIClient.APIError {
        case .unreachable: "Backend isn't running"
        case .tooLarge: "Too big: 25 MB at most"
        case .unsupportedType: "Only PDFs and images"
        case .rejected: "This file is empty"  // the upload's only 422
        case .unreadableFile: "Couldn't read the file"
        default: "Couldn't save the file"
        }
    }

    private static func target(for payload: Payload) -> DropTarget {
        switch payload {
        case .text(_, .link(let url)):
            let path = url.path() == "/" ? "" : url.path()
            return DropTarget(kind: .link, detail: (url.host() ?? url.absoluteString) + path)
        case .text(let text, _):
            // The first words, on one line.
            let words = text.split(whereSeparator: \.isWhitespace).prefix(24).joined(separator: " ")
            return DropTarget(kind: .note, detail: words)
        case .file(let file):
            return DropTarget(kind: .file, detail: file.name)
        case .refused(let refusal, let name):
            return DropTarget(kind: .refused(refusal.message), detail: name ?? "")
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
        // A refusal shows its reason but answers "no operation", so a release flies back.
        let taken = entered(Self.payload(from: info.draggingPasteboard), watchButton: true)
        // Turned down with nothing shown: do not read the pasteboard again on every move.
        if !taken, current == nil { refusedDrag = info.draggingSequenceNumber }
        return taken ? .copy : []
    }

    func draggingUpdated(_ info: NSDraggingInfo) -> NSDragOperation {
        // The window is bigger than the island: a drag inside it but off the island is
        // not a target.
        guard isOnIsland(info) else {
            exited()
            return []
        }
        guard let current else { return draggingEntered(info) }
        if case .refused = current { return [] }
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

    /// Files first: Finder puts a file's name or path on the drag as text too, and the
    /// person meant the file. Then text, or a link from a browser. Images that are not
    /// files (a browser's image drag, a file promise from Photos) are a later slice.
    static func payload(from pasteboard: NSPasteboard) -> Payload? {
        // Reading the file URLs is what gives a sandboxed app access to a dragged file:
        // the URL on a drag pasteboard carries a sandbox extension, which the system
        // grants to this process when the URL is read here.
        let files = pasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true])
            as? [URL] ?? []
        if !files.isEmpty { return payload(files: files) }
        let types = pasteboard.types ?? []
        let isPromiseOrImage = types.contains { type in
            type.rawValue.contains("promised-file") || type.rawValue.contains("FilePromise")
                || UTType(type.rawValue)?.conforms(to: .image) == true
        }
        if isPromiseOrImage { return nil }
        // A link dragged from a browser carries the address as a URL (and its title as text).
        if let url = (pasteboard.readObjects(forClasses: [NSURL.self]) as? [URL])?.first, !url.isFileURL,
            let payload = payload(text: url.absoluteString), case .text(_, .link) = payload
        {
            return payload
        }
        return pasteboard.string(forType: .string).flatMap(payload(text:))
    }
}
