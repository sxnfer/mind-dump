import AppKit
import Observation

/// One box that takes anything you type or paste, and sorts it: a web address becomes a
/// link, anything else a note. The view only draws this; it holds no logic.
///
/// The sorting happens here, in the app, because the app already knows which it is the
/// moment you stop typing, and can say so before you press Return. (M6 leaves "sort in
/// the app or behind the API" open for San; this is the app-side answer.)
@MainActor
@Observable
final class CaptureModel {
    enum Kind: Equatable {
        case empty
        case note
        case link(URL)
    }

    var text = "" {
        didSet { if text != oldValue { errorMessage = nil } }
    }
    /// A web link found on the clipboard when the panel opened. Offered, never taken
    /// silently: Return saves it only while the box is empty.
    private(set) var clipboardLink: URL?
    private(set) var errorMessage: String?
    private(set) var isSaving = false

    private let api: APIClient
    private let island: IslandModel
    private var tracking: Task<Void, Never>?

    init(api: APIClient, island: IslandModel) {
        self.api = api
        self.island = island
    }

    /// What Return would save right now.
    var kind: Kind {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return clipboardLink.map(Kind.link) ?? .empty
        }
        if let url = Self.webURL(in: trimmed) { return .link(url) }
        return .note
    }

    /// The panel opened: look at the clipboard once. Never in the background.
    func panelOpened() {
        errorMessage = nil
        clipboardLink = NSPasteboard.general.string(forType: .string).flatMap(Self.webURL(in:))
    }

    func panelClosed() {
        text = ""
        errorMessage = nil
        clipboardLink = nil
    }

    func submit() async {
        guard !isSaving else { return }
        let saving = kind
        isSaving = true
        defer { isSaving = false }
        do {
            let item: APIClient.Item
            switch saving {
            case .empty:
                return
            case .link(let url):
                item = try await api.saveLink(url)
            case .note:
                item = try await api.saveNote(text.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            text = ""
            let id = "item-\(item.id)"
            island.show(IslandActivity(id: id, glyph: .check, text: "Saved"))
            follow(item, activityID: id, isLink: saving != .note)
        } catch APIClient.APIError.alreadySaved {
            text = ""
            island.show(
                IslandActivity(
                    id: "duplicate", glyph: .symbol("checkmark.circle"),
                    text: "Already saved", tint: .white.opacity(0.7)
                )
            )
        } catch {
            // Stay open: the words are still in the box, and the panel says why.
            errorMessage = error.localizedDescription
        }
    }

    /// Live status (D11, polling): ask the backend how the worker is getting on, and keep
    /// the pill honest. A note is usually done before the first ask; a web page takes a
    /// second or two; a failure is shown, not swallowed.
    private func follow(_ saved: APIClient.Item, activityID: String, isLink: Bool) {
        tracking?.cancel()
        guard !saved.isSettled else { return }
        tracking = Task { [api, island] in
            // Let "Saved" be read before anything replaces it.
            try? await Task.sleep(for: IslandMotion.seconds(0.9))
            var showedProgress = false
            for _ in 0..<120 {
                guard !Task.isCancelled else { return }
                guard let item = try? await api.item(saved.id) else { return }
                switch item.status {
                case .ready, .saved:
                    if showedProgress {
                        island.show(IslandActivity(id: activityID, glyph: .check, text: "Ready"))
                    }
                    return
                case .error:
                    island.show(
                        IslandActivity(
                            id: activityID, glyph: .warning,
                            text: isLink ? "Couldn't read page" : "Couldn't process", hold: 3
                        )
                    )
                    return
                case .queued, .processing, .retrying:
                    if !showedProgress {
                        showedProgress = true
                        island.show(
                            IslandActivity(
                                id: activityID, glyph: .spinner,
                                text: item.status == .retrying ? "Retrying" : (isLink ? "Reading page" : "Processing"),
                                hold: nil
                            )
                        )
                    }
                }
                try? await Task.sleep(for: .milliseconds(500))
            }
            island.dismissActivity(id: activityID)
        }
    }

    /// A single http(s) address and nothing else. "see https://x.com later" is a note.
    static func webURL(in text: String) -> URL? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            !trimmed.isEmpty, !trimmed.contains(where: \.isWhitespace),
            let url = URL(string: trimmed),
            let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
            url.host() != nil
        else { return nil }
        return url
    }
}
