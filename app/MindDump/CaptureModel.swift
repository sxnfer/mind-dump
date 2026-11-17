import AppKit
import Observation

/// What the panel is doing right now. The view only draws this; it holds no logic.
@MainActor
@Observable
final class CaptureModel {
    enum Phase: Equatable {
        case idle               // collapsed at the notch
        case link(URL)          // clipboard holds a web link: offer to save it
        case note               // typing a note
        case saving
        case saved
        case failed(String)
    }

    private(set) var phase: Phase = .idle
    var noteText = ""

    /// Set by the panel: its notch geometry, so the view can line up with the notch.
    var notchWidth: CGFloat = 180
    var barHeight: CGFloat = 32

    /// The panel listens to this to grow, shrink and take or give back keyboard focus.
    var onExpandedChange: ((Bool) -> Void)?

    private let api: APIClient
    private var lastAttempt: Phase = .note

    init(api: APIClient) {
        self.api = api
    }

    var isExpanded: Bool { phase != .idle }

    func plusTapped() {
        isExpanded ? close() : open()
    }

    /// Reads the clipboard only now, when you press +. Never in the background.
    func open() {
        if let url = Clipboard.webURL() {
            phase = .link(url)
        } else {
            phase = .note
        }
        onExpandedChange?(true)
    }

    func writeNoteInstead() {
        phase = .note
    }

    func close() {
        phase = .idle
        noteText = ""
        onExpandedChange?(false)
    }

    func retry() {
        phase = lastAttempt
    }

    func save() async {
        let attempt = phase
        do {
            switch attempt {
            case .link(let url):
                lastAttempt = attempt
                phase = .saving
                try await api.saveLink(url)
            case .note:
                let body = noteText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !body.isEmpty else { return }  // the API would say 422; skip the trip
                lastAttempt = .note
                phase = .saving
                try await api.saveNote(body)
            default:
                return
            }
            phase = .saved
            noteText = ""
            try? await Task.sleep(for: .seconds(1.2))
            if phase == .saved { close() }
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }
}

/// Finds a web link on the clipboard, if that is all the clipboard holds.
enum Clipboard {
    @MainActor
    static func webURL() -> URL? {
        guard
            let text = NSPasteboard.general.string(forType: .string)?
                .trimmingCharacters(in: .whitespacesAndNewlines),
            !text.contains(where: \.isWhitespace),
            let url = URL(string: text),
            let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
            url.host() != nil
        else { return nil }
        return url
    }
}
