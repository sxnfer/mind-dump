import AppKit
import Observation
import SwiftUI

/// Ask mode: one question, a streamed answer with numbered references, then its sources.
/// The capsule shows the latest question and answer; a follow-up continues the same
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

    /// One numbered source under the answer: one item (a note, a link, a file), with
    /// every passage of it the answer cited, in the order first cited.
    struct Source: Identifiable, Equatable {
        let number: Int
        var citations: [APIClient.Citation]
        var id: Int { number }

        /// The first passage cited: the item's title, type and link are the same in all.
        var citation: APIClient.Citation { citations[0] }

        /// The pages cited, once each and in order, with runs of pages as a range:
        /// "p. 4, 9", "p. 4, 9–11", "p. 1–13". Short, so one header line holds it. Nil
        /// when none has a page.
        var pageLabel: String? {
            let pages = Array(Set(citations.compactMap(\.page))).sorted()
            guard var start = pages.first else { return nil }
            var runs: [String] = []
            var end = start
            for page in pages.dropFirst() {
                if page == end + 1 {
                    end = page
                    continue
                }
                runs.append(start == end ? "\(start)" : "\(start)–\(end)")
                start = page
                end = page
            }
            runs.append(start == end ? "\(start)" : "\(start)–\(end)")
            return "p. " + runs.joined(separator: ", ")
        }

        /// An open row shows at most this many passages: a long PDF can be cited 19
        /// times, and every quote at once is a wall of text (and a long layout).
        static let shownPassages = 6

        /// The passages an open row shows, and how many it leaves out.
        var shownCitations: ArraySlice<APIClient.Citation> { citations.prefix(Self.shownPassages) }
        var hiddenCount: Int { max(0, citations.count - Self.shownPassages) }

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
    private(set) var phase: Phase = .idle {
        didSet { if phase != oldValue { fitCard() } }
    }
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
    /// keyboard back to the box: a click inside the capsule takes it away otherwise, and
    /// the next question would be typed into nothing.
    private(set) var focusRequests = 0
    /// The sources came in once already: a reopened capsule shows them without the stagger.
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
    /// outside SwiftUI's own update forces it to lay out the whole capsule there and then,
    /// a second time in the same frame (measured: 15 to 25 ms, two dropped frames). The
    /// status line's removal carries its own animation instead.
    private func hideStatus() {
        guard status != nil else { return }
        status = nil
    }

    // MARK: Intent

    func submit() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        // Only an open capsule asks: keys that reach a hidden capsule (a script's posted
        // events, a stray Return) must not send a question nobody can see.
        guard island.presentation.isOpen, !text.isEmpty, !isBusy else { return }
        let conversation = conversationID
        island.capsuleContentWillChange("ask")
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
                #if DEBUG
                print("[ask] stream cancelled")
                fflush(stdout)
                #endif
                return
            } catch {
                self?.fail(error)
            }
        }
    }

    /// "New question" (or ⌘N): forget the conversation and start clean.
    func newQuestion() {
        guard island.presentation.isOpen else { return }  // see `submit`
        guard question != nil || errorMessage != nil else {
            draft = ""  // nothing asked yet: a clean box is all "new" can mean
            return
        }
        #if DEBUG
        print("[ask] new question\(isBusy ? ": stops the answer on its way" : "")")
        fflush(stdout)
        #endif
        task?.cancel()
        task = nil
        island.capsuleContentWillChange("new-question")
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
        island.capsuleContentWillChange("source-expand")
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

    /// The capsule is about to close. An answer on its way keeps coming: closing is not
    /// "stop". The user may have closed it to look at something else, or switched apps
    /// (which closes it too), and expects the answer to be there when they come back. San
    /// chose this on 2026-11-27; it replaced slice 2's "cancel on close", which made the
    /// user ask again. Only New (⌘N) or quitting the app stop an answer (a new question cannot
    /// be sent while one is on its way).
    ///
    /// While the capsule is closed nobody can read the answer, so nothing is drawn: the
    /// pour stops and the arriving text waits in `reveal` (see `AnswerReveal.hold`). The
    /// status, the phase and the final answer are still recorded as they come.
    ///
    /// This runs before the collapse starts and changes nothing on screen, so the
    /// collapse's first frame has no extra work (slice 2's known issue came from work done
    /// at that moment).
    func panelWillClose() {
        reveal.hold()
        #if DEBUG
        if isBusy {
            print("[ask] close: answer kept, still on its way (\(phase), \(reveal.debugCounts))")
        } else if question != nil {
            print("[ask] close: answer kept (\(phase), \(reveal.debugCounts))")
        } else {
            print("[ask] close: nothing asked")
        }
        fflush(stdout)
        // A stream is timed as one span until the close, so the close is timed on its own.
        onStream?(nil)
        #endif
    }

    /// The capsule is about to open: what arrived while it was closed shows at once, laid
    /// out once, before the open's first frame, so the capsule opens at its full height.
    /// An unfinished answer then goes on pouring at its normal pace; a finished one is
    /// whole, with its sources, which come in without their stagger (nobody saw them come).
    func panelWillOpen() {
        #if DEBUG
        let before = reveal.debugCounts
        #endif
        // A finished answer completes here, not on a later turn of the main queue: its
        // sources are then part of the open's first layout, not a resize after it.
        if answer != nil, phase == .answering { sourcesHaveEntered = true }
        reveal.resume()
        #if DEBUG
        if question != nil || errorMessage != nil {
            print("[ask] reopen: \(phase), \(before) before, \(reveal.debugCounts) now\(errorMessage == nil ? "" : ", error shown")")
            fflush(stdout)
        }
        #endif
    }

    // MARK: The card's height

    /// How the card follows its content's height. Decided here only, by the phase, so
    /// every way a turn starts or ends (an answer, a failure, New, a new question)
    /// follows the same rule.
    ///
    /// - Waiting for words (thinking, searching, reading): the card fits exactly, so the
    ///   space under the status line is the space at its sides. Growing in steps here
    ///   only rounded the status line up to the next step: an empty band under it.
    /// - Words flowing: the card grows in steps (`IslandModel.growsInSteps`), from the
    ///   first words. That is where the steps pay: fewer resizes of the glass per line.
    /// - Done, failed, replaced or nothing asked: an exact fit again.
    ///
    /// A follow-up is no exception: the card shrinks from the last answer to its status
    /// line, then grows with the new words (San's choice on 2026-11-26: an even fit while
    /// thinking matters more than holding the old height).
    private func fitCard() {
        island.growsInSteps = phase == .answering
    }

    // MARK: The stream

    private func handle(_ event: APIClient.AskEvent) {
        // A cancelled stream may still have an event in flight: drop it.
        guard isBusy, task != nil else { return }
        switch event {
        case .status(let next):
            // Words, then a search: those words were a preamble ("Let me look…"), not
            // the answer. The backend's final text leaves them out, so they go now.
            // (Words received, not only words shown: while the capsule is closed they wait.)
            if case .thinking = next {} else if reveal.hasReceived, answer == nil {
                withAnimation(contentSpring) { reveal.reset(keepingPassages: true) }
            }
            // Plain assignment: the status line animates its own words (see `hideStatus`).
            status = next
        case .passages(let passages):
            // Before any text that cites them: the numbers are by item from the start.
            reveal.learn(passages.map { ($0.chunkId, $0.itemId) })
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
            // The checked citations name every cited passage's item. Normally the stream
            // said so already; a marker it never announced is folded into its item here.
            reveal.learn(final.citations.compactMap { c in c.markerID.map { ($0, c.itemId) } })
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
        island.capsuleContentWillChange("sources")
        // Plain assignment: each row brings itself in (`Entrance`), the notices fade in
        // on their own, and the body follows the new height on its spring.
        sources = answer.grounded ? Self.numbered(answer.citations, by: reveal.number(ofChunk:)) : []
        phase = .done
    }

    private func fail(_ error: Error) {
        task = nil
        onStream?(nil)
        let message = error.localizedDescription
        island.capsuleContentWillChange("ask-error")
        withAnimation(contentSpring) {
            status = nil
            if draft.isEmpty, let question { draft = question }
            if !reveal.hasReceived {
                // Nothing arrived yet: put the last answer back, as if this one never left.
                restoreLastTurn()
            } else {
                // Words came: keep them, dimmed, so it is clear they are unfinished. (While
                // the capsule is closed, they show when it opens.)
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
            reveal.show(lastTurn.answer.text, passages: Self.items(of: lastTurn.answer.citations))
            sources =
                lastTurn.answer.grounded
                ? Self.numbered(lastTurn.answer.citations, by: reveal.number(ofChunk:)) : []
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

    /// One source per item, in the order their numbers first appear in the text, each
    /// with its passages in the order first cited (the backend lists citations that way).
    /// An item the text never mentions (rare) goes at the end with the next free number.
    private static func numbered(_ citations: [APIClient.Citation], by number: (Int) -> Int?) -> [Source] {
        var seen = Set<Int>()
        var next = (citations.compactMap { $0.markerID.flatMap(number) }.max() ?? 0) + 1
        var row: [Int: Int] = [:]  // item id to its index in `sources`
        var sources: [Source] = []
        for citation in citations {
            guard let id = citation.markerID, seen.insert(id).inserted else { continue }
            if let index = row[citation.itemId] {
                sources[index].citations.append(citation)
                continue
            }
            let shown = number(id) ?? { defer { next += 1 }; return next }()
            row[citation.itemId] = sources.count
            sources.append(Source(number: shown, citations: [citation]))
        }
        return sources.sorted { $0.number < $1.number }
    }

    /// Chunk id to item id, for every passage an answer cites.
    private static func items(of citations: [APIClient.Citation]) -> [Int: Int] {
        var items: [Int: Int] = [:]
        for c in citations { if let id = c.markerID { items[id] = c.itemId } }
        return items
    }

    /// Changes inside the capsule (a new turn, sources, an open row) ride the island's resize spring.
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
            // A second passage of item 2, so an open row with several quotes is built too.
            APIClient.Citation(
                marker: "[c:3]", chunkId: 3, itemId: 2, type: "link", title: nil, url: "https://example.com/a",
                page: 5, quote: quote
            ),
        ]
        let text = "Use step 6 for espresso [c:1].\n\nAeroPress sits at step 14 [c:1][c:2][c:3]."
        let final = APIClient.Answer(
            conversationId: 0, text: text, citations: citations, grounded: true, incomplete: true
        )
        question = "Which grinder step for espresso?"
        answer = final
        reveal.show(text, passages: Self.items(of: citations))
        sources = Self.numbered(citations, by: reveal.number(ofChunk:))
        expanded = [2]
        sourcesHaveEntered = false
        phase = .done
    }
}
