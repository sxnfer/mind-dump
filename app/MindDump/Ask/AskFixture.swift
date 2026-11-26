#if DEBUG
import Foundation

/// A canned answer stream for building and measuring the Ask UI without a model call.
///
/// Start the app with `MD_ASK_FIXTURE=1` and every question gets a scripted stream
/// with real-world timing: a pause to think, a search, then the answer in uneven pieces
/// (some split in the middle of a `[c:…]` marker), then `final`. Like the backend, it
/// announces the passages the answer may cite ("passages") after the search and, in a
/// follow-up, at the start, before any text cites them. Which script plays
/// depends on a word in the question:
///
///     long     a long answer that passes the capsule's maximum height
///     pdf      an answer citing 19 passages of one 13-page PDF (quotes of about 900
///              characters): one source row that holds many passages
///     nothing  an answer not found in the items (not grounded)
///     fail     a few sentences, then an "error" event mid-stream
///     limit    a 429 before the stream starts (rate limit)
///     key      a 503 before the stream starts (no Claude key)
///     anything else: a short grounded answer citing three passages of two items (two
///              passages of the Kettlebrook manual, one of the grinder note), so it
///              shows two sources
struct AskFixture: AnswerSource {
    func answer(
        _ text: String, conversation: Int?,
        onEvent: @escaping @MainActor @Sendable (APIClient.AskEvent) -> Void
    ) async throws {
        let question = text.lowercased()
        if question.contains("limit") { throw APIClient.AskError.tooMany(retryAfter: 23) }
        if question.contains("key") { throw APIClient.AskError.noKey }

        let script: Script =
            question.contains("long") ? .long
            : question.contains("pdf") ? .manyPassages
            : question.contains("nothing") ? .notFound
            : question.contains("fail") ? .failing
            : .short

        try await pause(0.5)
        // A follow-up: the passages of earlier turns may be cited again, so they come first.
        if conversation != nil { await onEvent(.passages(script.passages)) }
        await onEvent(.status(.thinking))
        try await pause(0.7)
        await onEvent(.status(.searching(question.contains("long") ? "Lisbon trip plan" : "grinder espresso setting")))
        try await pause(0.6)
        await onEvent(.passages(script.passages))  // what the search handed over
        if question.contains("long") {
            await onEvent(.status(.reading(5)))
            try await pause(0.5)
        }
        await onEvent(.status(.thinking))
        try await pause(0.6)

        var generator = SystemRandomNumberGenerator()
        var rest = Substring(script.streamed)
        while !rest.isEmpty {
            // Pieces of 3 to 28 characters, 15 to 90 ms apart, with the odd longer
            // stall: the shape of a real Claude stream.
            let size = Int.random(in: 3...28, using: &generator)
            let piece = rest.prefix(size)
            rest = rest.dropFirst(piece.count)
            await onEvent(.delta(String(piece)))
            if script == .failing && rest.count < script.streamed.count / 2 {
                try await pause(0.4)
                await onEvent(.failed(status: 503, detail: "Claude is temporarily unavailable. Nothing was saved; try again."))
                return
            }
            let stall = Int.random(in: 0..<14, using: &generator) == 0
            try await pause(stall ? Double.random(in: 0.25...0.6) : Double.random(in: 0.015...0.09))
        }
        try await pause(0.3)
        await onEvent(.final(script.final(conversation: conversation ?? 900)))
    }

    private func pause(_ seconds: Double) async throws {
        try await Task.sleep(for: .milliseconds(Int(seconds * 1000)))
    }

    private enum Script: Equatable {
        case short, long, notFound, failing, manyPassages

        /// The many-passages answer: 19 passages of one PDF, cited on these pages, in this
        /// order (as a real answer about a 13-page PDF did on 2026-11-26).
        static let manyPages = [1, 1, 4, 2, 3, 4, 4, 5, 6, 7, 8, 8, 9, 12, 12, 13, 13, 10, 11]
        static func manyChunk(_ index: Int) -> Int { 101 + index }

        /// The passages the search hands over: every one the answer cites, and a couple
        /// it does not. The long answer's [c:99] is never announced (and the backend then
        /// removes it), as for a marker the model made up.
        var passages: [APIClient.Passage] {
            let pairs: [(Int, Int)] =
                switch self {
                case .short, .failing: [(5, 5), (31, 12), (34, 12), (40, 13)]
                case .long: [(6, 6), (7, 7), (8, 8)]
                case .notFound: [(6, 6), (8, 8)]
                case .manyPassages: Self.manyPages.indices.map { (Self.manyChunk($0), 50) } + [(8, 8)]
                }
            return pairs.map { APIClient.Passage(chunkId: $0.0, itemId: $0.1) }
        }

        /// What the stream sends. The long one cites a passage the backend then rejects
        /// ([c:99]), so the final text differs from the streamed one, as it can for real.
        var streamed: String {
            switch self {
            case .short, .failing:
                "Set the Kettlebrook K2 to step 6 for espresso [c:5]. For an AeroPress go much coarser, to step 14, "
                    + "and for a French press to step 24 [c:5].\n\nClean the burrs every two weeks with grinder "
                    + "tablets [c:31], then grind a few beans to clear the dust before the first cup [c:34]. Oily "
                    + "beans leave more behind, so clean more often if you use them [c:31][c:34]."
            case .long:
                "Your Lisbon trip runs from 12 to 16 March, and you are staying in Alfama [c:6]. The plan has three "
                    + "fixed points.\n\nFirst, the day trip to Sintra is on the 14th. You take the train from Rossio "
                    + "station, which leaves every 20 minutes and takes about 40 minutes [c:6]. Go early: the palaces "
                    + "fill up by late morning, and the walk up to Pena is steep [c:99].\n\nSecond, dinner is booked at "
                    + "Taberna do Mar on the 15th at 8 pm [c:6]. Keep that evening free of other plans, because the "
                    + "booking was hard to get.\n\nThird, the reading list fits the trip well. Piranesi by Susanna "
                    + "Clarke is short enough for the flights, and The Overstory by Richard Powers suits slow mornings "
                    + "in Alfama [c:7]. Designing Data-Intensive Applications is better left at home; your note says to "
                    + "read chapters 5 to 7 first, and that needs a desk [c:7].\n\nNothing in your items covers the "
                    + "flights or where in Alfama you are staying, so check those before you go. The bike and garden "
                    + "notes are unrelated to the trip and are not used here."
            case .manyPassages:
                // Two to four markers per sentence, all of one item.
                "The handbook sets out how a carbon project goes from design to issued credits [c:101][c:102]. "
                    + "It starts with the baseline and the project boundary [c:103][c:104][c:105], then the "
                    + "monitoring plan and how often each value is measured [c:106][c:107][c:108].\n\nVerification "
                    + "comes next: an independent body checks the monitoring report against the plan [c:109][c:110]"
                    + "[c:111], and only then are credits issued to the registry [c:112][c:113]. A share of every "
                    + "issuance goes into the buffer pool, which covers later reversals such as fire [c:114][c:115]"
                    + "[c:116][c:117]. The last chapters cover renewals and the fees at each step [c:118][c:119]."
            case .notFound:
                "Your items don't say what the weather in Lisbon is like in March. They cover the trip dates, the "
                    + "Sintra day trip and a dinner booking, but nothing about the weather or what to pack."
            }
        }

        func final(conversation: Int) -> APIClient.Answer {
            let grinder = APIClient.Citation(
                marker: "[c:5]", chunkId: 5, itemId: 5, type: "note", title: nil, url: nil, page: nil,
                quote: "Kettlebrook K2 coffee grinder settings: espresso on step 6, AeroPress on step 14, French "
                    + "press on step 24. Clean the burrs every two weeks with grinder tablets."
            )
            let manual = APIClient.Citation(
                marker: "[c:31]", chunkId: 31, itemId: 12, type: "link", title: "Kettlebrook K2 manual",
                url: "https://example.com/kettlebrook/k2-manual", page: 4,
                quote: "Coffee oils build up on the burrs. Clean them every two weeks, or more often with oily beans."
            )
            let manualRinse = APIClient.Citation(
                marker: "[c:34]", chunkId: 34, itemId: 12, type: "link", title: "Kettlebrook K2 manual",
                url: "https://example.com/kettlebrook/k2-manual", page: 9,
                quote: "After cleaning, grind about ten grams of beans and throw them away, to clear the tablet dust."
            )
            let trip = APIClient.Citation(
                marker: "[c:6]", chunkId: 6, itemId: 6, type: "note", title: "Lisbon trip plan", url: nil, page: nil,
                quote: "Lisbon trip plan, 12 to 16 March: stay in Alfama, day trip to Sintra on the 14th by train "
                    + "from Rossio, dinner booked at Taberna do Mar on the 15th at 8 pm."
            )
            let books = APIClient.Citation(
                marker: "[c:7]", chunkId: 7, itemId: 7, type: "file", title: "Winter reading.pdf", url: nil, page: 2,
                quote: "Reading list for winter: The Overstory by Richard Powers, Piranesi by Susanna Clarke, and "
                    + "Designing Data-Intensive Applications by Martin Kleppmann (chapters 5 to 7 first)."
            )
            switch self {
            case .short, .failing:
                return APIClient.Answer(
                    conversationId: conversation, text: streamed, citations: [grinder, manual, manualRinse],
                    grounded: true, incomplete: false
                )
            case .long:
                // The backend removed [c:99] (never handed to the model) and tidied the space.
                let text = streamed.replacingOccurrences(of: " [c:99]", with: "")
                return APIClient.Answer(
                    conversationId: conversation, text: text, citations: [trip, books],
                    grounded: true, incomplete: true
                )
            case .manyPassages:
                let base =
                    "Each project defines its boundary, baseline and leakage before any credit can be issued. "
                    + "The monitoring plan names every parameter, its source, the instrument used, how often it "
                    + "is measured and how the data are archived. Uncertainty is handled conservatively: where a "
                    + "value is uncertain, the lower estimate of emission reductions is used. The verification "
                    + "body samples the raw records, visits the site, and checks the calculations line by line "
                    + "against the approved methodology. Findings are raised as corrective action requests, and "
                    + "the project must close each one before the verifier signs the statement. "
                let citations = Self.manyPages.enumerated().map { index, page in
                    APIClient.Citation(
                        marker: "[c:\(Self.manyChunk(index))]", chunkId: Self.manyChunk(index), itemId: 50,
                        type: "file", title: "Carbon project handbook.pdf", url: nil, page: page,
                        quote: "Section \(index + 1), page \(page). " + base + base.prefix(240 + 20 * (index % 4))
                    )
                }
                return APIClient.Answer(
                    conversationId: conversation, text: streamed, citations: citations,
                    grounded: true, incomplete: false
                )
            case .notFound:
                return APIClient.Answer(
                    conversationId: conversation, text: streamed, citations: [], grounded: false, incomplete: false
                )
            }
        }
    }
}
#endif
