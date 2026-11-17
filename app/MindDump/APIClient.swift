import Foundation

/// The app's one door to the backend. The panel never builds HTTP requests itself.
struct APIClient: Sendable {
    /// Where `docker compose up` publishes the API (loopback only).
    static let local = APIClient(baseURL: URL(string: "http://127.0.0.1:8000")!)

    let baseURL: URL

    enum APIError: LocalizedError, Equatable {
        case unreachable            // backend not running, or no answer within the timeout
        case rejected(String)       // 422: the API explains what is wrong with the input
        case unexpected(Int)        // any other status code

        var errorDescription: String? {
            switch self {
            case .unreachable: "Backend isn't running. Start it with docker compose up."
            case .rejected(let reason): reason
            case .unexpected(let code): "The backend answered \(code)."
            }
        }
    }

    var healthURL: URL { baseURL.appending(path: "health") }

    func saveLink(_ url: URL) async throws {
        try await createItem(["type": "link", "url": url.absoluteString])
    }

    func saveNote(_ body: String) async throws {
        try await createItem(["type": "note", "body": body])
    }

    /// POST /items. Success is exactly 201 Created; everything else is an error.
    private func createItem(_ payload: [String: String]) async throws {
        var request = URLRequest(url: baseURL.appending(path: "items"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 5
        request.httpBody = try JSONEncoder().encode(payload)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw APIError.unreachable
        }

        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        switch code {
        case 201:
            return
        case 422:
            throw APIError.rejected(Self.firstValidationMessage(in: data) ?? "The backend rejected this.")
        default:
            throw APIError.unexpected(code)
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
