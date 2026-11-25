import Foundation

/// Where answers come from. The real one is the backend (`APIClient`); debug builds can
/// swap in a canned stream (`AskFixture`), so the answer UI can be built and measured
/// without paying for a model call each time.
protocol AnswerSource: Sendable {
    /// Ask `text`. With a conversation id it is a follow-up in that conversation.
    /// Calls `onEvent` for each event, in order, and returns when the stream ends.
    func answer(
        _ text: String, conversation: Int?,
        onEvent: @escaping @MainActor @Sendable (APIClient.AskEvent) -> Void
    ) async throws
}

/// Ask and follow-up (M5's `POST /ask` and `POST /conversations/{id}/messages`), read as
/// Server-Sent Events.
///
/// Why a stream: an answer takes several seconds (search, read, write). A plain request
/// would show nothing until the end. With SSE the backend sends small events as it goes
/// ("searching", a few words, a few more words, "final"), over one open response.
extension APIClient: AnswerSource {
    /// What the agent is doing before (and between) the words.
    enum AskStatus: Equatable, Sendable {
        case thinking
        case searching(String?)
        case reading(Int?)
    }

    /// One passage an answer cites (D38). A snapshot: it outlives the chunk.
    struct Citation: Decodable, Equatable, Sendable {
        let marker: String  // as in the text, e.g. [c:12]
        let chunkId: Int?
        let itemId: Int
        let type: String  // note, link or file
        let title: String?
        let url: String?
        let page: Int?
        let quote: String

        /// The number inside the marker. The marker keeps it even when the chunk is gone.
        var markerID: Int? { Int(marker.dropFirst(3).dropLast()) }
    }

    /// The `final` event: the answer as the backend checked and saved it.
    struct Answer: Decodable, Equatable, Sendable {
        let conversationId: Int
        let text: String  // with [c:<chunk id>] markers; invalid ones already removed
        let citations: [Citation]
        let grounded: Bool  // false: no valid citation, so not from the user's items
        let incomplete: Bool  // true: the agent ran out of steps
    }

    enum AskEvent: Sendable {
        case status(AskStatus)
        case delta(String)
        case final(Answer)
        case failed(status: Int, detail: String)
    }

    /// Why an answer did not come, in words for the panel.
    enum AskError: LocalizedError, Equatable {
        case noKey
        case dailyLimit
        case tooMany(retryAfter: Int?)
        case conversationGone
        case failed(String)

        var errorDescription: String? {
            switch self {
            case .noKey:
                "Answers need a Claude key. Add ANTHROPIC_API_KEY to .env and restart the backend."
            case .dailyLimit:
                "Today's spending limit is reached. Answers start again at 00:00 UTC."
            case .tooMany(let seconds):
                seconds.map { "Too many questions. Try again in \($0) s." } ?? "Too many questions. Try again shortly."
            case .conversationGone:
                "This conversation is gone. Press ⌘N and ask again."
            case .failed(let detail):
                detail
            }
        }

        /// A status code and the backend's `detail`, turned into one of the cases above.
        static func from(status: Int, detail: String?, retryAfter: Int? = nil) -> AskError {
            switch status {
            case 503 where detail?.contains("ANTHROPIC_API_KEY") ?? false: .noKey
            case 429 where detail?.contains("daily cap") ?? false: .dailyLimit
            case 429: .tooMany(retryAfter: retryAfter)
            case 404: .conversationGone
            default: .failed(detail ?? "The backend answered \(status). Nothing was saved; try again.")
            }
        }
    }

    func answer(
        _ text: String, conversation: Int?,
        onEvent: @escaping @MainActor @Sendable (AskEvent) -> Void
    ) async throws {
        if let conversation {
            try await stream(path: "conversations/\(conversation)/messages", body: ["text": text], onEvent: onEvent)
        } else {
            try await stream(path: "ask", body: ["question": text], onEvent: onEvent)
        }
    }

    private func stream(
        path: String, body: [String: String],
        onEvent: @escaping @MainActor @Sendable (AskEvent) -> Void
    ) async throws {
        var request = URLRequest(
            url: baseURL.appending(path: path).appending(queryItems: [URLQueryItem(name: "stream", value: "true")])
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        // The longest silence allowed between two pieces of the stream, not the whole answer.
        request.timeoutInterval = 90
        request.httpBody = try JSONEncoder().encode(body)

        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await URLSession.shared.bytes(for: request)
        } catch {
            if Self.isCancellation(error) { throw CancellationError() }
            throw APIError.unreachable
        }

        let http = response as? HTTPURLResponse
        let code = http?.statusCode ?? 0
        guard code == 200 else {
            // Refused before the stream began (no key, over a limit): a plain JSON body.
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count > 64_000 { break }
            }
            let retryAfter = http?.value(forHTTPHeaderField: "Retry-After").flatMap(Int.init)
            throw AskError.from(status: code, detail: Self.detail(in: data), retryAfter: retryAfter)
        }

        // SSE is lines: "event: delta", then "data: {...}", then a blank line. `.lines`
        // drops blank lines, so an event is complete at its data line (the backend sends
        // exactly one data line per event).
        var event = "message"
        var ended = false
        do {
            for try await line in bytes.lines {
                if line.hasPrefix("event:") {
                    event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
                } else if line.hasPrefix("data:") {
                    let payload = Data(line.dropFirst(5).drop(while: { $0 == " " }).utf8)
                    if let parsed = Self.parse(event: event, payload: payload) {
                        await onEvent(parsed)
                        if case .final = parsed { ended = true }
                        if case .failed = parsed { ended = true }
                    }
                    event = "message"
                }
            }
        } catch {
            if Self.isCancellation(error) { throw CancellationError() }
            throw AskError.failed("Lost the connection to the backend. Nothing was saved; try again.")
        }
        if !ended {
            throw AskError.failed("The answer stopped before it finished. Nothing was saved; try again.")
        }
    }

    private static func parse(event: String, payload: Data) -> AskEvent? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else { return nil }
        switch event {
        case "status":
            switch json["state"] as? String {
            case "searching": return .status(.searching(json["query"] as? String))
            case "reading": return .status(.reading(json["item_id"] as? Int))
            default: return .status(.thinking)
            }
        case "delta":
            return (json["text"] as? String).map(AskEvent.delta)
        case "final":
            guard let answer = try? decoder.decode(Answer.self, from: payload) else {
                return .failed(status: 0, detail: "The answer came back in a shape the app can't read.")
            }
            return .final(answer)
        case "error":
            return .failed(status: json["status"] as? Int ?? 0, detail: json["detail"] as? String ?? "The answer failed.")
        default:
            return nil
        }
    }

    /// FastAPI's error body: {"detail": "..."}.
    private static func detail(in data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
    }

    private static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
}
