import Foundation
import Observation

/// Asks the backend "are you alive?" on a fixed beat and publishes the answer.
///
/// This is the app-side twin of Docker's HEALTHCHECK. SwiftUI views that read
/// `status` redraw when it changes (that is what `@Observable` does).
@MainActor
@Observable
final class HealthMonitor {
    enum Status: Equatable {
        case unknown  // no answer yet
        case up       // last check got HTTP 200
        case down     // error, timeout, or any other status code
    }

    private(set) var status: Status = .unknown

    private let url: URL
    private let interval: Duration
    private let session: URLSession
    private var loop: Task<Void, Never>?

    init(url: URL, interval: Duration = .seconds(5), timeout: TimeInterval = 2) {
        self.url = url
        self.interval = interval
        let config = URLSessionConfiguration.ephemeral  // no cookies, no disk cache
        config.timeoutIntervalForRequest = timeout      // a hung backend counts as down
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            let clock = ContinuousClock()
            while !Task.isCancelled {
                guard let self else { return }
                // Schedule start-to-start, so a slow check does not stretch the beat.
                // That keeps "turns red within 5 s" true.
                let next = clock.now + self.interval
                await self.check()
                try? await Task.sleep(until: next, clock: clock)
            }
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
    }

    func check() async {
        do {
            let (_, response) = try await session.data(from: url)
            let code = (response as? HTTPURLResponse)?.statusCode
            status = code == 200 ? .up : .down
        } catch {
            status = .down
        }
    }
}
