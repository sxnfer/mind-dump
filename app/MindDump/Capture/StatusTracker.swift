import Foundation

/// Live status (D11, polling) for every item saved lately, not only the last one.
///
/// After a save, the backend's worker still has work to do: fetch a page, read a PDF,
/// describe an image. This asks the backend how each item is getting on, every half
/// second, until it is done or failed (or a minute has passed), and keeps the pill
/// honest. Think of a cloakroom attendant who watches every ticket handed out, not only
/// the last one: a coat that went missing is reported even if more coats came in since.
///
/// The pill has room for one thing, so the rules are:
/// - The newest item shows its progress, as before: "Reading page" or "Processing", then
///   "Ready", or its failure.
/// - An older item shows only a failure ("Couldn't read page"). Its success needs no
///   word: nothing was lost. A failure is never dropped: it waits while the capsule is
///   open or a drag is over the island (so it never pulls either away from the user),
///   and while another warning is still being read.
/// - While a warning is being read, the newest item's next pill waits for it.
/// - At most `maxTracked` items are followed at once. A sixth save stops following the
///   oldest: five is more than a burst of drops, and each one is a request every half
///   second to the local backend, so the cap only guards against a runaway.
@MainActor
final class StatusTracker {
    /// How many items are followed at once.
    static let maxTracked = 5

    /// Seconds. Tests make them short.
    struct Timing {
        /// Before the first look: "Saved" is read before anything replaces it. Slowed
        /// down by `MD_MOTION_SCALE` with the rest of the island's motion.
        var firstLook: Double = 0.9
        /// Between looks.
        var interval: Double = 0.5
        /// How many looks before giving up (about a minute).
        var looks = 120
        /// How long a warning pill stays (`IslandActivity.hold`).
        var warningHold: Double = 3
        /// How often a waiting warning checks whether it may show.
        var recheck: Double = 0.3
    }

    private let fetch: @MainActor (Int) async throws -> APIClient.Item
    private let show: @MainActor (IslandActivity) -> Void
    private let dismiss: @MainActor (String) -> Void
    /// The capsule is open, or a drag is over the island: an older item's warning waits.
    private let isBusy: @MainActor () -> Bool
    private let timing: Timing

    /// The items followed now, oldest first.
    private var tracked: [(id: Int, task: Task<Void, Never>)] = []
    /// The item saved last. Only it shows progress, even after it is done.
    private var newest: Int?
    /// Until when a warning pill is being read.
    private var warningUntil = ContinuousClock.now

    init(
        timing: Timing = Timing(),
        fetch: @escaping @MainActor (Int) async throws -> APIClient.Item,
        show: @escaping @MainActor (IslandActivity) -> Void,
        dismiss: @escaping @MainActor (String) -> Void,
        isBusy: @escaping @MainActor () -> Bool
    ) {
        self.timing = timing
        self.fetch = fetch
        self.show = show
        self.dismiss = dismiss
        self.isBusy = isBusy
    }

    /// The ids followed now, oldest first.
    var trackedIDs: [Int] { tracked.map(\.id) }

    /// Start following a saved item. It becomes the newest.
    func follow(_ saved: APIClient.Item, isLink: Bool) {
        // The user just saved something: its "Saved" pill replaced any warning on screen.
        warningUntil = .now
        newest = saved.id
        guard !saved.isSettled else { return }
        tracked.removeAll { $0.id == saved.id }
        while tracked.count >= Self.maxTracked {
            let oldest = tracked.removeFirst()
            oldest.task.cancel()
            #if DEBUG
            print("[status] item \(oldest.id): no longer followed (\(Self.maxTracked) newer items)")
            fflush(stdout)
            #endif
        }
        let task = Task { [weak self] in
            await self?.watch(saved.id, isLink: isLink)
            self?.tracked.removeAll { $0.id == saved.id }
        }
        tracked.append((saved.id, task))
    }

    /// Stop following everything (the debug self-test cleans up with this).
    func stopAll() {
        tracked.forEach { $0.task.cancel() }
        tracked = []
    }

    private func isNewest(_ id: Int) -> Bool { newest == id }

    private func watch(_ id: Int, isLink: Bool) async {
        let activityID = "item-\(id)"
        await sleep(IslandMotion.timeScale * timing.firstLook)
        var showedProgress = false
        for _ in 0..<timing.looks {
            guard !Task.isCancelled else { return }
            guard let item = try? await fetch(id) else { return }
            guard !Task.isCancelled else { return }
            switch item.status {
            case .ready, .saved:
                // Only the newest says "Ready", and only if it said it was working.
                if showedProgress, isNewest(id) {
                    await waitForWarning()
                    if isNewest(id) { show(IslandActivity(id: activityID, glyph: .check, text: "Ready")) }
                }
                return
            case .error:
                let warning = IslandActivity(
                    id: activityID, glyph: .warning,
                    text: isLink ? "Couldn't read page" : "Couldn't process", hold: timing.warningHold
                )
                if isNewest(id) {
                    show(warning)  // as before: the newest's own failure shows at once
                } else {
                    await waitUntilFree()
                    guard !Task.isCancelled else { return }
                    #if DEBUG
                    print("[status] item \(id): an earlier item failed; its warning shows")
                    fflush(stdout)
                    #endif
                    show(warning)
                }
                warningUntil = .now + .seconds(IslandMotion.timeScale * timing.warningHold)
                return
            case .queued, .processing, .retrying:
                if !showedProgress, isNewest(id) {
                    showedProgress = true
                    await waitForWarning()
                    guard !Task.isCancelled, isNewest(id) else { break }
                    show(
                        IslandActivity(
                            id: activityID, glyph: .spinner,
                            text: item.status == .retrying ? "Retrying" : (isLink ? "Reading page" : "Processing"),
                            hold: nil
                        )
                    )
                }
            }
            await sleep(timing.interval)
        }
        dismiss(activityID)
    }

    /// While a warning pill is being read, wait.
    private func waitForWarning() async {
        while !Task.isCancelled, ContinuousClock.now < warningUntil {
            await sleep(timing.recheck)
        }
    }

    /// An older item's warning: wait until the island is free and no other warning shows.
    private func waitUntilFree() async {
        while !Task.isCancelled, isBusy() || ContinuousClock.now < warningUntil {
            await sleep(timing.recheck)
        }
    }

    private func sleep(_ seconds: Double) async {
        try? await Task.sleep(for: .milliseconds(Int(seconds * 1000)))
    }
}
