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
    private(set) var errorMessage: String?
    private(set) var isSaving = false

    private let api: APIClient
    private let island: IslandModel
    private let status: StatusTracker
    private var clearing: Task<Void, Never>?
    private var pendingRetry: (text: String, error: String)?

    init(api: APIClient, island: IslandModel) {
        self.api = api
        self.island = island
        status = StatusTracker(
            fetch: { id in try await api.item(id) },
            show: { activity in island.show(activity) },
            dismiss: { id in island.dismissActivity(id: id) },
            // An earlier item's warning never pulls the open capsule or a drop target away.
            isBusy: { island.presentation.isOpen || island.presentation.isDropTarget }
        )
    }

    /// What Return would save right now.
    var kind: Kind {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return .empty }
        if let url = Self.webURL(in: trimmed) { return .link(url) }
        return .note
    }

    /// The capsule opened: put back the words of a drop that could not be saved. (Slice 1
    /// also offered a link found on the clipboard here; San had it removed on 2026-11-25,
    /// and the app no longer reads the clipboard at all.)
    func panelOpened() {
        if clearing != nil { clearNow() }
        errorMessage = nil
        if let retry = pendingRetry {
            pendingRetry = nil
            text = retry.text
            errorMessage = retry.error  // after the text: a new text clears the error
        }
    }

    /// The capsule closed. The box empties once the content has faded out, not in the
    /// collapse's first frame, where the words would turn into the prompt in plain view
    /// while the capsule goes back into the notch.
    func panelClosed() {
        clearing?.cancel()
        clearing = Task { [weak self] in
            try? await Task.sleep(for: IslandMotion.seconds(0.4))
            guard !Task.isCancelled else { return }
            self?.clearNow()
        }
    }

    private func clearNow() {
        clearing?.cancel()
        clearing = nil
        text = ""
        errorMessage = nil
    }

    func submit() async {
        guard !isSaving else { return }
        let saving = kind
        isSaving = true
        defer { isSaving = false }
        do {
            try await store(saving, note: text.trimmingCharacters(in: .whitespacesAndNewlines))
            // Saved, and the capsule has gone back into the notch, which empties the box
            // (`panelClosed`). Only if it is somehow still open is it emptied here.
            if island.presentation.isOpen { text = "" }
        } catch {
            // Stay open: the words are still in the box, and the capsule says why.
            errorMessage = error.localizedDescription
        }
    }

    /// Save, then tell the island: "Saved" and the live status after it, or "Already
    /// saved" for a link that is. Typed capture and drop-and-save both come through here.
    /// It throws only when nothing was saved, so the caller keeps the words.
    func store(_ kind: Kind, note: String) async throws {
        switch kind {
        case .empty:
            return
        case .link(let url):
            try await announce(isLink: true) { [api] in try await api.saveLink(url) }
        case .note:
            try await announce(isLink: false) { [api] in try await api.saveNote(note) }
        }
    }

    /// A dropped file: uploaded, then the same "Saved", live status and "Already saved"
    /// as a note. The worker reads a PDF's text or describes an image ("Processing").
    /// An image may arrive here as bytes the app made from the file (`ImagePrep`, D42).
    func storeFile(_ body: APIClient.UploadBody, name: String) async throws {
        try await announce(isLink: false) { [api] in try await api.uploadFile(body, name: name) }
    }

    /// The part every save shares: run it, then tell the island.
    private func announce(isLink: Bool, _ save: () async throws -> APIClient.Item) async throws {
        let item: APIClient.Item
        do {
            item = try await save()
        } catch APIClient.APIError.alreadySaved {
            island.show(
                IslandActivity(
                    id: "duplicate", glyph: .symbol("checkmark.circle"),
                    text: "Already saved", tint: .white.opacity(0.7)
                )
            )
            return
        }
        let id = "item-\(item.id)"
        island.show(IslandActivity(id: id, glyph: .check, text: "Saved"))
        follow(item, isLink: isLink)
    }

    /// A drop that could not be saved: the next open puts its words in the box with the
    /// reason under them, so Return retries. Nothing is lost.
    func keepForRetry(_ text: String, error: String) {
        pendingRetry = (text, error)
    }

    /// Live status (D11, polling) for this item, and the ones saved before it that are
    /// still being worked on (see `StatusTracker`).
    private func follow(_ saved: APIClient.Item, isLink: Bool) {
        status.follow(saved, isLink: isLink)
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
