#if DEBUG
import Foundation

/// A canned answer stream for building and measuring the Ask UI without a model call.
///
/// Start the app with `MD_ASK_FIXTURE=1` and every question gets a scripted stream
/// with real-world timing: a pause to think, a search, then the answer in uneven pieces
/// (some split in the middle of a `[c:…]` marker), then `final`. Which script plays
/// depends on a word in the question:
///
///     long     a long answer that passes the capsule's maximum height
///     nothing  an answer not found in the items (not grounded)
///     fail     a few sentences, then an "error" event mid-stream
///     limit    a 429 before the stream starts (rate limit)
///     key      a 503 before the stream starts (no Claude key)
///     anything else: a short grounded answer with two sources
struct AskFixture: AnswerSource {
    func answer(
        _ text: String, conversation: Int?,
        onEvent: @escaping @MainActor @Sendable (APIClient.AskEvent) -> Void
    ) async throws {
        let question = text.lowercased()
        if question.contains("limit") { throw APIClient.AskError.tooMany(retryAfter: 23) }
        if question.contains("key") { throw APIClient.AskError.noKey }

        try await pause(0.5)
        await onEvent(.status(.thinking))
        try await pause(0.7)
        await onEvent(.status(.searching(question.contains("long") ? "Lisbon trip plan" : "grinder espresso setting")))
        try await pause(0.6)
        if question.contains("long") {
            await onEvent(.status(.reading(5)))
            try await pause(0.5)
        }
        await onEvent(.status(.thinking))
        try await pause(0.6)

        let script: Script =
            question.contains("long") ? .long
            : question.contains("nothing") ? .notFound
            : question.contains("fail") ? .failing
            : .short
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
        case short, long, notFound, failing

        /// What the stream sends. The long one cites a passage the backend then rejects
        /// ([c:99]), so the final text differs from the streamed one, as it can for real.
        var streamed: String {
            switch self {
            case .short, .failing:
                "Set the Kettlebrook K2 to step 6 for espresso [c:5]. For an AeroPress go much coarser, to step 14, "
                    + "and for a French press to step 24 [c:5].\n\nClean the burrs every two weeks with grinder "
                    + "tablets, or the espresso starts to taste flat [c:5][c:31]."
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
                    conversationId: conversation, text: streamed, citations: [grinder, manual],
                    grounded: true, incomplete: false
                )
            case .long:
                // The backend removed [c:99] (never handed to the model) and tidied the space.
                let text = streamed.replacingOccurrences(of: " [c:99]", with: "")
                return APIClient.Answer(
                    conversationId: conversation, text: text, citations: [trip, books],
                    grounded: true, incomplete: true
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
