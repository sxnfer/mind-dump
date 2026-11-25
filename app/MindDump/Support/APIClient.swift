import Foundation

/// The app's one door to the backend. The island never builds HTTP requests itself.
struct APIClient: Sendable {
    /// Where `docker compose up` publishes the API (loopback only).
    static let local: APIClient = {
        #if DEBUG
        // Tests point the app at a throwaway backend, never at real saved items.
        if let raw = ProcessInfo.processInfo.environment["MD_API_BASE"], let url = URL(string: raw) {
            return APIClient(baseURL: url)
        }
        #endif
        return APIClient(baseURL: URL(string: "http://127.0.0.1:8000")!)
    }()

    let baseURL: URL

    enum APIError: LocalizedError, Equatable {
        case unreachable            // backend not running, or no answer within the timeout
        case rejected(String)       // 422: the API explains what is wrong with the input
        case alreadySaved           // 409: this link is already saved (migration 003)
        case unexpected(Int)        // any other status code

        var errorDescription: String? {
            switch self {
            case .unreachable: "Backend isn't running. Start it with docker compose up."
            case .rejected(let reason): reason
            case .alreadySaved: "Already saved."
            case .unexpected(let code): "The backend answered \(code)."
            }
        }
    }

    /// The part of an item the island shows: its id and how far the worker has got.
    struct Item: Decodable, Equatable, Sendable {
        enum Status: String, Decodable, Sendable {
            case saved, queued, processing, retrying, ready, error
        }

        let id: Int
        let status: Status
        let error: String?

        /// The worker has finished with it, one way or the other.
        var isSettled: Bool { status == .ready || status == .error || status == .saved }
    }

    var healthURL: URL { baseURL.appending(path: "health") }

    func saveLink(_ url: URL) async throws -> Item {
        try await createItem(["type": "link", "url": url.absoluteString])
    }

    func saveNote(_ body: String) async throws -> Item {
        try await createItem(["type": "note", "body": body])
    }

    /// GET /items/{id}: where the worker has got to with one item.
    func item(_ id: Int) async throws -> Item {
        var request = URLRequest(url: baseURL.appending(path: "items/\(id)"))
        request.timeoutInterval = 4
        let (data, code) = try await send(request)
        guard code == 200, let item = try? JSONDecoder().decode(Item.self, from: data) else {
            throw APIError.unexpected(code)
        }
        return item
    }

    /// POST /items. Success is exactly 201 Created; everything else is an error.
    private func createItem(_ payload: [String: String]) async throws -> Item {
        var request = URLRequest(url: baseURL.appending(path: "items"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 5
        request.httpBody = try JSONEncoder().encode(payload)

        let (data, code) = try await send(request)
        switch code {
        case 201:
            guard let item = try? JSONDecoder().decode(Item.self, from: data) else {
                throw APIError.unexpected(code)
            }
            return item
        case 409:
            throw APIError.alreadySaved
        case 422:
            throw APIError.rejected(Self.firstValidationMessage(in: data) ?? "The backend rejected this.")
        default:
            throw APIError.unexpected(code)
        }
    }

    private func send(_ request: URLRequest) async throws -> (Data, Int) {
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch {
            throw APIError.unreachable
        }
    }

    /// FastAPI's 422 body: {"detail": [{"loc": [...], "msg": "..."}]}
    private static func firstValidationMessage(in data: Data) -> String? {
        guard
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let details = json["detail"] as? [[String: Any]],
            let message = details.first?["msg"] as? String
        else { return nil }
        return message
    }
}
