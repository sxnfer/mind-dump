import AppKit
import Observation
import SwiftUI

/// Ask mode: one question, a streamed answer with numbered references, then its sources.
/// The panel shows the latest question and answer; a follow-up continues the same
/// conversation on the backend. The view only draws this; it holds no logic.
@MainActor
@Observable
final class AskModel {
    enum Phase: Equatable {
        case idle        // nothing asked yet (or "New question")
        case waiting     // sent; no words yet
        case answering   // words are flowing
        case done        // the answer is complete, sources shown
    }

    /// One numbered source under the answer.
    struct Source: Identifiable, Equatable {
        let number: Int
        let citation: APIClient.Citation
        var id: Int { number }

        /// What the row is called: the title, else the web address's host, else the
        /// first words of the passage.
        var title: String {
            if let title = citation.title?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty {
                return title
            }
            if let host = citation.url.flatMap(URL.init(string:))?.host() { return host }
            let words = citation.quote.split(whereSeparator: \.isWhitespace).prefix(8)
            return words.joined(separator: " ") + (words.count == 8 ? "…" : "")
        }

        var link: URL? {
            guard citation.type == "link" else { return nil }
            return citation.url.flatMap(URL.init(string:))
        }

        var glyph: String {
            switch citation.type {
            case "link": "link"
            case "file": "doc"
            default: "note.text"
            }
        }
    }

    var draft = "" {
        didSet { if draft != oldValue, phase == .idle || phase == .done { errorMessage = nil } }
    }
    private(set) var phase: Phase = .idle
    /// The question on screen, above its answer.
    private(set) var question: String?
    /// What the agent is doing, while it works.
    private(set) var status: APIClient.AskStatus?
    /// The answer's words as they flow (see `AnswerReveal`).
    let reveal = AnswerReveal()
    private(set) var answer: APIClient.Answer?
    private(set) var sources: [Source] = []
    private(set) var errorMessage: String?
    /// The stream failed after some words were shown: they stay, dimmed.
    private(set) var partialFailed = false
    /// Bumps when a new turn replaces the old one, so the view can cross-fade them.
    private(set) var turnID = 0
    /// Source rows that are open (showing their passage).
    private(set) var expanded: Set<Int> = []
    /// Bumps when a click in the answer (a source row, New question) should hand the
    /// keyboard back to the box: a click inside the panel takes it away otherwise, and
    /// the next question would be typed into nothing.
    private(set) var focusRequests = 0
    /// The sources came in once already: a reopened panel shows them without the stagger.
    @ObservationIgnored var sourcesHaveEntered = false

    /// The next Return continues this conversation.
    private(set) var conversationID: Int?

    var isBusy: Bool { phase == .waiting || phase == .answering }

    /// Debug only: a stream starts or ends, so the frame monitor can time all of it.
    @ObservationIgnored var onStream: ((String?) -> Void)?

    private let source: AnswerSource
    private let island: IslandModel
    @ObservationIgnored private var task: Task<Void, Never>?
    /// The last finished turn, put back if a newer one fails before any words show.
    @ObservationIgnored private var lastTurn: (question: String, answer: APIClient.Answer)?

    init(source: AnswerSource, island: IslandModel) {
        self.source = source
        self.island = island
    }

    /// Words are coming: they say more than "Thinking" does, so the status line goes
    /// (a later search brings it back). It goes when the first piece arrives, a frame
    /// before the first characters show, so the two changes land in different frames.
    ///
    /// No `withAnimation` here or anywhere on the stream's path: an animated change made
    /// outside SwiftUI's own update forces it to lay out the whole panel there and then,
    /// a second time in the same frame (measured: 15 to 25 ms, two dropped frames). The
    /// status line's removal carries its own animation instead.
    private func hideStatus() {
        guard status != nil else { return }
        status = nil
    }

    // MARK: Intent

    func submit() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        // Only an open panel asks: keys that reach a hidden panel (a script's posted
        // events, a stray Return) must not send a question nobody can see.
        guard island.presentation.isPanel, !text.isEmpty, !isBusy else { return }
        let conversation = conversationID
        island.panelContentWillChange("ask")
        withAnimation(contentSpring) {
            draft = ""
            errorMessage = nil
            partialFailed = false
            question = text
            status = .thinking
            phase = .waiting
            answer = nil
            sources = []
            expanded = []
            turnID += 1
            reveal.reset()
        }
        sourcesHaveEntered = false
        onStream?("stream")
        task = Task { [weak self, source] in
            do {
                try await source.answer(text, conversation: conversation) { event in
                    self?.handle(event)
                }
            } catch is CancellationError {
                return
            } catch {
                self?.fail(error)
            }
        }
    }

    /// "New question" (or ⌘N): forget the conversation and start clean.
    func newQuestion() {
        guard island.presentation.isPanel else { return }  // see `submit`
        guard question != nil || errorMessage != nil else {
            draft = ""  // nothing asked yet: a clean box is all "new" can mean
            return
        }
        task?.cancel()
        task = nil
        island.panelContentWillChange("new-question")
        withAnimation(contentSpring) {
            draft = ""
            question = nil
            status = nil
            answer = nil
            sources = []
            expanded = []
            errorMessage = nil
            partialFailed = false
            conversationID = nil
            phase = .idle
            turnID += 1
            reveal.reset()
        }
        lastTurn = nil
        focusRequests += 1
        onStream?(nil)
    }

    /// Open or close one source's passage.
    func toggle(_ source: Source) {
        focusRequests += 1
        island.panelContentWillChange("source-expand")
        // Plain assignment; the list animates the change itself (`SourcesList`). An
        // animated change made from out here left the scroll view's geometry stale,
        // so the row could not be scrolled into view.
        if expanded.contains(source.number) {
            expanded.remove(source.number)
        } else {
            expanded.insert(source.number)
        }
    }

    func open(_ source: Source) {
        guard let url = source.link else { return }
        focusRequests += 1
        NSWorkspace.shared.open(url)
    }

    /// The panel is about to close. An answer still on its way is cancelled: the
    /// backend saves nothing unless it sends "final", so the conversation is as it was.
    /// The question goes back in the box, one Return from asking again.
    ///
    /// This runs before the collapse starts, and changes nothing on screen then: the
    /// text stops flowing (no new characters while the panel blurs out), and everything
    /// that moves layout (restoring the last answer, or showing the rest of a finished
    /// one) waits until the panel has faded out. Doing it in the collapse's first frame
    /// made that frame 13 to 38 ms late.
    func panelWillClose() {
        guard isBusy else { return }
        reveal.pause()
        if answer == nil {
            task?.cancel()
            task = nil
            onStream?(nil)
        }
        let question = question
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7 * IslandMotion.timeScale) { [weak self] in
            guard let self, self.isBusy else { return }
            if self.answer != nil {
                self.reveal.flush()  // complete: show all of it next time
                return
            }
            guard self.task == nil else { return }  // a new ask started meanwhile
            if self.draft.isEmpty, let question { self.draft = question }
            self.restoreLastTurn()
        }
    }

    // MARK: The stream

    private func handle(_ event: APIClient.AskEvent) {
        // A cancelled stream may still have an event in flight: drop it.
        guard isBusy, task != nil else { return }
        switch event {
        case .status(let next):
            // Words, then a search: those words were a preamble ("Let me look…"), not
            // the answer. The backend's final text leaves them out, so they go now.
            if case .thinking = next {} else if !reveal.isEmpty, answer == nil {
                withAnimation(contentSpring) { reveal.reset() }
            }
            // Plain assignment: the status line animates its own words (see `hideStatus`).
            status = next
        case .delta(let text):
            // Plain assignment, for the same reason as `hideStatus`: the hint line that
            // reads the phase animates itself.
            if phase == .waiting { phase = .answering }
            hideStatus()
            reveal.append(text)
        case .final(let final):
            answer = final
            conversationID = final.conversationId
            withAnimation(.easeOut(duration: 0.2 * IslandMotion.timeScale)) {
                status = nil
                if phase == .waiting { phase = .answering }
            }
            reveal.onDrained = { [weak self] in self?.complete() }
            reveal.finish(with: final.text)
        case .failed(let code, let detail):
            task?.cancel()
            fail(APIClient.AskError.from(status: code, detail: detail))
        }
    }

    /// Every word is on screen: bring in the sources.
    private func complete() {
        guard let answer, let question else { return }
        lastTurn = (question, answer)
        onStream?(nil)
        island.panelContentWillChange("sources")
        // Plain assignment: each row brings itself in (`Entrance`), the notices fade in
        // on their own, and the body follows the new height on its spring.
        sources = answer.grounded ? Self.numbered(answer.citations, by: reveal.references) : []
        phase = .done
    }

    private func fail(_ error: Error) {
        task = nil
        onStream?(nil)
        let message = error.localizedDescription
        island.panelContentWillChange("ask-error")
        withAnimation(contentSpring) {
            status = nil
            if draft.isEmpty, let question { draft = question }
            if reveal.isEmpty {
                // Nothing shown yet: put the last answer back, as if this one never left.
                restoreLastTurn()
            } else {
                // Words were shown: keep them, dimmed, so it is clear they are unfinished.
                reveal.flush()
                partialFailed = true
                phase = .done
            }
            errorMessage = message
        }
    }

    private func restoreLastTurn() {
        status = nil
        partialFailed = false
        turnID += 1
        if let lastTurn {
            question = lastTurn.question
            answer = lastTurn.answer
            reveal.show(lastTurn.answer.text)
            sources = lastTurn.answer.grounded ? Self.numbered(lastTurn.answer.citations, by: reveal.references) : []
            sourcesHaveEntered = true
            phase = .done
        } else {
            question = nil
            answer = nil
            sources = []
            reveal.reset()
            phase = .idle
        }
    }

    /// Citations in the order their numbers first appear in the text. A citation the
    /// text never mentions (rare) goes at the end with the next free number.
    private static func numbered(_ citations: [APIClient.Citation], by references: [Int: Int]) -> [Source] {
        var seen = Set<Int>()
        var next = (references.values.max() ?? 0) + 1
        var sources: [Source] = []
        for citation in citations {
            guard let id = citation.markerID, !seen.contains(id) else { continue }
            seen.insert(id)
            let number = references[id] ?? { defer { next += 1 }; return next }()
            sources.append(Source(number: number, citation: citation))
        }
        return sources.sorted { $0.number < $1.number }
    }

    /// In-panel changes (a new turn, sources, an open row) ride the island's resize spring.
    private var contentSpring: Animation {
        island.reduceMotion ? IslandMotion.reduced.body : IslandMotion.resize.body
    }

    // MARK: Rehearsal

    /// A finished answer, built at launch out of sight (see `IslandView.rehearsal`), so
    /// the first real answer builds nothing new mid-animation.
    func fillForRehearsal() {
        let quote = "Kettlebrook K2 settings: espresso on step 6, AeroPress on step 14."
        let citations = [
            APIClient.Citation(
                marker: "[c:1]", chunkId: 1, itemId: 1, type: "note", title: "Grinder settings", url: nil,
                page: nil, quote: quote
            ),
            APIClient.Citation(
                marker: "[c:2]", chunkId: 2, itemId: 2, type: "link", title: nil, url: "https://example.com/a",
                page: 3, quote: quote
            ),
        ]
        let text = "Use step 6 for espresso [c:1].\n\nAeroPress sits at step 14 [c:1][c:2]."
        let final = APIClient.Answer(
            conversationId: 0, text: text, citations: citations, grounded: true, incomplete: true
        )
        question = "Which grinder step for espresso?"
        answer = final
        reveal.show(text)
        sources = Self.numbered(citations, by: reveal.references)
        expanded = [1]
        sourcesHaveEntered = false
        phase = .done
    }
}
