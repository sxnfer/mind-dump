#if DEBUG
import Foundation

/// Checks of the island's logic that need no screen and no backend, run inside a debug
/// build by the script verb `selftest` (the app has no test target). Each check prints one
/// line, `[selftest] pass: …` or `[selftest] FAIL: …`, and a last line counts them. They
/// call the same code the app runs; only the backend and the clock are stand-ins.
@MainActor
enum SelfTest {
    private static var passed = 0
    private static var failed = 0

    static func run() async {
        passed = 0
        failed = 0
        flushDropsHalfMarkers()
        rejectionWords()
        passagesInPageOrder()
        await trackerKeepsEarlierFailures()
        await trackerWaitsWhileBusy()
        trackerCap()
        print("[selftest] done: \(passed) passed, \(failed) failed")
        fflush(stdout)
    }

    private static func check(_ ok: Bool, _ name: String, _ detail: @autoclosure () -> String = "") {
        if ok { passed += 1 } else { failed += 1 }
        let more = detail()
        print("[selftest] \(ok ? "pass" : "FAIL"): \(name)\(more.isEmpty ? "" : " (\(more))")")
        fflush(stdout)
    }

    // MARK: F1: a failure never shows half a marker

    /// A stream that fails right after a piece ending inside a marker: the flush shows
    /// the words, never "[", "[c", "[c:" or "[c:3". A whole marker is a small number.
    private static func flushDropsHalfMarkers() {
        let words = "Clean the burrs every two weeks with grinder tablets "
        for tail in ["[", "[c", "[c:", "[c:3", "[c:31"] {
            for held in [false, true] {
                let reveal = AnswerReveal()
                reveal.learn([(chunk: 31, item: 12)])
                if held { reveal.hold() }  // the capsule was closed when it failed
                reveal.append(words + tail)
                reveal.flush()
                if held { reveal.resume() }
                let shown = reveal.debugShownText
                check(
                    shown == words && !reveal.debugShowsRawMarker,
                    "flush ending in \"\(tail)\"\(held ? ", capsule closed" : "")",
                    "shown \(shown.count) characters, ends \"\(shown.suffix(6))\""
                )
            }
        }
        let reveal = AnswerReveal()
        reveal.learn([(chunk: 31, item: 12)])
        reveal.append(words + "[c:31]")
        reveal.flush()
        let shown = reveal.debugShownText
        check(
            shown == "Clean the burrs every two weeks with grinder tablets\u{2009}1" && reveal.plainText.hasSuffix("tablets[1]"),
            "flush ending in a whole \"[c:31]\" shows it as a reference",
            "ends \"\(shown.suffix(10))\", plain \"\(reveal.plainText.suffix(10))\""
        )
        // A "[" that is not a marker stays.
        let other = AnswerReveal()
        other.append("See [note")
        other.flush()
        check(other.debugShownText == "See [note", "flush keeps a \"[\" that is not a marker")
    }

    // MARK: N2: plain words for a 422

    private static func rejectionWords() {
        let tooLong = Data(
            #"{"detail":[{"type":"string_too_long","loc":["body","body"],"msg":"String should have at most 20000 characters","input":"x","ctx":{"max_length":20000}}]}"#
                .utf8
        )
        let note = APIClient.rejectionMessage(in: tooLong, kind: "note")
        check(note == "This note is too long to save (over 20,000 characters).", "422 too long, a note", note)
        let other = Data(#"{"detail":[{"type":"missing","loc":["body","url"],"msg":"Field required"}]}"#.utf8)
        let generic = APIClient.rejectionMessage(in: other, kind: "link")
        check(
            generic == "The backend couldn't save this link. Check it and try again." && !generic.contains("Field"),
            "another 422 gets a plain sentence", generic
        )
        let garbage = APIClient.rejectionMessage(in: Data("not json".utf8), kind: "note")
        check(garbage == "The backend couldn't save this note. Check it and try again.", "a 422 with no body", garbage)
    }

    // MARK: N4: an opened source lists its passages in page order

    private static func passagesInPageOrder() {
        let pages = [1, 1, 4, 2, 3, 4, 4, 5, 6, 7, 8, 8, 9, 12, 12, 13, 13, 10, 11]
        let citations = pages.enumerated().map { index, page in
            APIClient.Citation(
                marker: "[c:\(101 + index)]", chunkId: 101 + index, itemId: 50, type: "file",
                title: "Handbook.pdf", url: nil, page: page, quote: "q"
            )
        }
        let sources = AskModel.numbered(citations, by: { _ in 1 })
        let order = sources.first?.citations.compactMap(\.page) ?? []
        check(sources.count == 1 && order == pages.sorted(), "passages by page", order.map(String.init).joined(separator: ","))
        // Same page: by place on the page (chunk ids run in text order).
        let samePage = sources.first?.citations.filter { $0.page == 4 }.compactMap(\.chunkId) ?? []
        check(samePage == samePage.sorted(), "same page by place", samePage.map(String.init).joined(separator: ","))
        // No pages: the order first cited stays.
        let pairs: [(chunk: Int, item: Int)] = [(7, 3), (5, 3), (9, 3)]
        let notes = pairs.map { chunk, item in
            APIClient.Citation(
                marker: "[c:\(chunk)]", chunkId: chunk, itemId: item, type: "note", title: nil, url: nil, page: nil,
                quote: "q"
            )
        }
        let kept = AskModel.numbered(notes, by: { _ in 1 }).first?.citations.compactMap(\.chunkId) ?? []
        check(kept == [7, 5, 9], "no pages: order first cited", kept.map(String.init).joined(separator: ","))
    }

    // MARK: N3: live status for several items

    private static let fast = StatusTracker.Timing(
        firstLook: 0.02, interval: 0.05, looks: 60, warningHold: 0.25, recheck: 0.02
    )

    /// Two links saved one after the other; the first fails. Its failure shows, after
    /// the second's progress; the second's "Ready" waits until the warning was read.
    private static func trackerKeepsEarlierFailures() async {
        var shown: [String] = []
        var looks: [Int: Int] = [:]
        let tracker = StatusTracker(
            timing: fast,
            fetch: { id in
                looks[id, default: 0] += 1
                let n = looks[id]!
                // Item 1 fails on its fourth look; item 2 works for six looks, then is ready.
                let status: APIClient.Item.Status =
                    id == 1 ? (n >= 4 ? .error : .retrying) : (n >= 7 ? .ready : .processing)
                return APIClient.Item(id: id, status: status, error: nil)
            },
            show: { shown.append("\($0.id) \($0.text)") },
            dismiss: { shown.append("\($0) dismissed") },
            isBusy: { false }
        )
        tracker.follow(APIClient.Item(id: 1, status: .queued, error: nil), isLink: true)
        tracker.follow(APIClient.Item(id: 2, status: .queued, error: nil), isLink: true)
        try? await Task.sleep(for: .seconds(1.5))
        let expected = ["item-2 Reading page", "item-1 Couldn't read page", "item-2 Ready"]
        check(shown == expected, "two links, the first fails: its warning shows", shown.joined(separator: " | "))
        check(tracker.trackedIDs.isEmpty, "both followed to the end", "\(tracker.trackedIDs)")
    }

    /// An earlier item's failure while the capsule is open waits until it closes.
    private static func trackerWaitsWhileBusy() async {
        var shown: [(String, Bool)] = []
        let busy = Flag()
        let tracker = StatusTracker(
            timing: fast,
            fetch: { id in APIClient.Item(id: id, status: id == 1 ? .error : .processing, error: nil) },
            show: { shown.append(($0.text, busy.on)) },
            dismiss: { _ in },
            isBusy: { busy.on }
        )
        tracker.follow(APIClient.Item(id: 1, status: .queued, error: nil), isLink: true)
        tracker.follow(APIClient.Item(id: 2, status: .queued, error: nil), isLink: false)
        try? await Task.sleep(for: .seconds(0.4))
        let whileBusy = shown.contains { $0.0 == "Couldn't read page" }
        busy.on = false
        try? await Task.sleep(for: .seconds(0.3))
        let after = shown.first { $0.0 == "Couldn't read page" }
        check(!whileBusy && after?.1 == false, "an earlier failure waits for the capsule to close")
        tracker.stopAll()
    }

    /// The capsule's open state, as the stand-in for the island sees it.
    private final class Flag {
        var on = true
    }

    /// A sixth item stops the oldest one being followed.
    private static func trackerCap() {
        let tracker = StatusTracker(
            timing: StatusTracker.Timing(firstLook: 30),
            fetch: { id in APIClient.Item(id: id, status: .processing, error: nil) },
            show: { _ in }, dismiss: { _ in }, isBusy: { false }
        )
        for id in 1...6 { tracker.follow(APIClient.Item(id: id, status: .queued, error: nil), isLink: true) }
        check(tracker.trackedIDs == [2, 3, 4, 5, 6], "at most \(StatusTracker.maxTracked) followed", "\(tracker.trackedIDs)")
        tracker.stopAll()
    }
}
#endif
