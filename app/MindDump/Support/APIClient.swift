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
        case alreadySaved           // 409: this link, or these exact file bytes, already saved
        case tooLarge               // 413: a file over the backend's 25 MB limit
        case unsupportedType        // 415: not a PDF, PNG or JPEG, judged by its first bytes
        case unreadableFile         // the app could not read a file to upload (moved, no access)
        case unexpected(Int)        // any other status code

        var errorDescription: String? {
            switch self {
            case .unreachable: "Backend isn't running. Start it with docker compose up."
            case .rejected(let reason): reason
            case .alreadySaved: "Already saved."
            case .tooLarge: "Files can be at most 25 MB."
            case .unsupportedType: "Only PDF, PNG and JPEG files can be saved."
            case .unreadableFile: "Couldn't read the file."
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

    /// A file item's stored bytes (`GET /items/{id}/file`), which the browser shows in
    /// place: a PDF opens at `page` when one is given (`#page=N`), an image as itself.
    func fileURL(itemID: Int, page: Int? = nil) -> URL {
        let url = baseURL.appending(path: "items/\(itemID)/file")
        guard let page, var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        parts.fragment = "page=\(page)"
        return parts.url ?? url
    }

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

    /// POST /items/upload: the file's raw bytes as the body, its name in a header (D34).
    ///
    /// The body is streamed from disk by URLSession, off the main thread, so a 20 MB file
    /// is never held in memory whole and the island keeps animating. The backend decides
    /// the type from the first bytes and dedupes by SHA-256 (409 for the same bytes).
    func uploadFile(_ file: URL, name: String) async throws -> Item {
        var request = URLRequest(url: baseURL.appending(path: "items/upload"))
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(Self.contentDisposition(filename: name), forHTTPHeaderField: "Content-Disposition")
        // Seconds without any progress, not for the whole upload.
        request.timeoutInterval = 30

        // A file dragged in from Finder is readable because the drag granted it (see
        // `DropModel.payload(from:)`). If the URL is security-scoped as well, open the
        // scope for exactly as long as the upload reads it.
        let scoped = file.startAccessingSecurityScopedResource()
        defer { if scoped { file.stopAccessingSecurityScopedResource() } }

        let data: Data
        let code: Int
        do {
            let (body, response) = try await URLSession.shared.upload(for: request, fromFile: file)
            data = body
            code = (response as? HTTPURLResponse)?.statusCode ?? 0
        } catch let error as URLError where error.code == .fileDoesNotExist || error.code == .noPermissionsToReadFile {
            // Not the backend's fault: the file moved, or the sandbox would not let us read it.
            throw APIError.unreadableFile
        } catch {
            throw APIError.unreachable
        }
        switch code {
        case 201:
            guard let item = try? JSONDecoder().decode(Item.self, from: data) else {
                throw APIError.unexpected(code)
            }
            return item
        case 409: throw APIError.alreadySaved
        case 413: throw APIError.tooLarge
        case 415: throw APIError.unsupportedType
        case 422: throw APIError.rejected(Self.detailMessage(in: data) ?? "The backend rejected this file.")
        default: throw APIError.unexpected(code)
        }
    }

    /// `attachment; filename="report.pdf"`. A header value must be plain ASCII, so a name
    /// with any other letter (or a quote) goes in the RFC 2231 form, `filename*=UTF-8''…`,
    /// which the backend's parser reads (`uploads.safe_filename`).
    static func contentDisposition(filename: String) -> String {
        let plain = filename.unicodeScalars.allSatisfy { $0.isASCII && $0.value >= 0x20 && $0 != "\"" && $0 != "\\" }
        if plain { return "attachment; filename=\"\(filename)\"" }
        // RFC 5987's attr-char, near enough: ASCII letters, digits and a few marks.
        let allowed = CharacterSet(
            charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        )
        let encoded = filename.addingPercentEncoding(withAllowedCharacters: allowed) ?? "file"
        return "attachment; filename*=UTF-8''\(encoded)"
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

    /// An error's `detail` when it is a plain sentence (the upload route's own errors), or
    /// the first validation message.
    private static func detailMessage(in data: Data) -> String? {
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = json["detail"] as? String
        {
            return detail
        }
        return firstValidationMessage(in: data)
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
