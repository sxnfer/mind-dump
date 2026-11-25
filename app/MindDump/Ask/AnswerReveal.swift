import AppKit
import Observation
import QuartzCore
import SwiftUI

/// Turns a bursty stream of answer text into a steady flow on screen.
///
/// The network hands over text in uneven pieces: three words, a pause, half a sentence.
/// Shown as they come, the text would lurch. So pieces go into a buffer, and a display
/// link (one tick per screen refresh) lets characters out at a smooth rate. The rate
/// adapts: when the buffer is long it speeds up, when it is short it slows down, and it
/// changes gradually, so it never jumps. Think of a tap filling a glass at an even pour,
/// whatever the water pressure behind it.
///
/// Three more jobs:
/// - Citation markers like `[c:12]` never show. A marker is let out whole or not at all,
///   and a half-arrived `[c:1` at the end of the buffer waits for the rest.
/// - Each marker becomes a small reference number (1, 2, 3 by first appearance).
/// - Only the last line or two change while text flows. Finished lines are built once and
///   frozen, so each tick lays out a few dozen characters, not the whole answer.
@MainActor
@Observable
final class AnswerReveal {
    /// One line of the answer (the text between two newlines).
    struct Line: Identifiable, Equatable {
        let id: Int
        var text: AttributedString
        /// An empty line that is finished: drawn as a paragraph gap.
        var isGap: Bool
        /// An empty line still waiting for its first character: drawn as nothing.
        var isPending: Bool
        /// Where the line ends in the text (the index of its newline, or the end).
        var end: Int
    }

    /// Lines that will not change again.
    private(set) var frozen: [Line] = []
    /// The line (or two) still arriving or still fading in.
    private(set) var live: [Line] = []
    /// Bumps when the text is thrown away, so the view can cross-fade the new one in.
    private(set) var generation = 0

    /// True once any character is on screen. Kept as its own property, so a view that
    /// only asks "is there text?" is not redrawn on every tick.
    private(set) var hasText = false
    var isEmpty: Bool { !hasText }

    /// Chunk id to the number shown for it, by first appearance in the text.
    @ObservationIgnored private(set) var references: [Int: Int] = [:]
    /// Called once when the stream has ended and every character is on screen.
    @ObservationIgnored var onDrained: (() -> Void)?
    /// The view whose screen the display link follows. The link is made here, paused,
    /// so the first answer does not pay for making it.
    @ObservationIgnored weak var linkView: NSView? {
        didSet {
            guard ticker == nil else { return }
            ticker = DisplayTicker(view: linkView) { [weak self] link in self?.tick(link) }
            ticker?.isPaused = true
        }
    }

    @ObservationIgnored private var received: [Character] = []
    @ObservationIgnored private var revealed = 0
    /// When each shown character appeared, so it can fade in over `fade` seconds.
    @ObservationIgnored private var shownAt: [CFTimeInterval] = []
    /// Index of the first character of the first live line.
    @ObservationIgnored private var liveStart = 0
    @ObservationIgnored private var inputDone = false
    @ObservationIgnored private var rate: Double = 0  // characters per second, smoothed
    @ObservationIgnored private var carry: Double = 0  // the fraction of a character owed
    @ObservationIgnored private var lastTick: CFTimeInterval = 0
    @ObservationIgnored private var ticker: DisplayTicker?
    @ObservationIgnored private var running = false

    /// How long a new character takes to fade in. Short: it reads as ink, not as an effect.
    private static let fade: CFTimeInterval = 0.2
    /// The slowest pour, in characters per second. About fast reading speed.
    private static let minRate: Double = 60
    /// How far behind the stream the screen may run: the rate is set to drain the buffer
    /// in about this long. Shorter once the stream has ended.
    private static let lagWhileStreaming: Double = 0.55
    private static let lagAfterEnd: Double = 0.35

    // MARK: Input

    /// More text from the stream.
    func append(_ piece: String) {
        received.append(contentsOf: piece)
        startTicking()
    }

    /// The stream ended with the backend's checked text. Usually that is exactly what
    /// was streamed. If it differs (a marker it removed, spacing it tidied), the part
    /// already on screen is rebuilt from the checked text; the rest flows on as usual.
    func finish(with text: String) {
        let final = Array(text)
        let common = zip(received, final).prefix(while: { $0 == $1 }).count
        if revealed > common {
            // Map the reveal point into the new text: same distance from the end.
            let point = max(common, min(final.count, revealed - (received.count - final.count)))
            received = final
            rebuild(upTo: point, keepingUpTo: common, now: CACurrentMediaTime())
        } else {
            received = final
        }
        inputDone = true
        startTicking()
    }

    /// Show a whole text at once, with no flow (a reopened panel, a restored answer).
    func show(_ text: String) {
        stop()
        received = Array(text)
        inputDone = true
        rebuild(upTo: received.count, keepingUpTo: 0, now: CACurrentMediaTime())
        freezeFinishedLines(now: .infinity)
        renderLive(now: .infinity)
        freezeAll()
    }

    /// Stop pouring, and leave the text as it is (the panel is closing).
    func pause() {
        stop()
    }

    /// Let everything out now (the panel is closing; there is no one to pour for).
    func flush() {
        guard running || revealed < received.count else { return }
        revealed = received.count
        shownAt = Array(repeating: 0, count: revealed)
        freezeFinishedLines(now: .infinity)
        renderLive(now: .infinity)
        stop()
        if inputDone {
            freezeAll()
            drained()
        }
    }

    /// Forget the text (a new question, or a preamble the model wrote before searching).
    func reset() {
        stop()
        received = []
        revealed = 0
        shownAt = []
        liveStart = 0
        inputDone = false
        rate = 0
        carry = 0
        references = [:]
        frozen = []
        live = []
        hasText = false
        generation += 1
    }

    /// The answer as plain words, with references as [1], [2] (for copy and VoiceOver).
    var plainText: String {
        var numbers = references
        return Self.parse(received[0..<revealed], references: &numbers).map(\.text).joined()
    }

    // MARK: The tick

    /// The display link is made once and then paused and resumed: the pour stops and
    /// starts every time the network pauses, and a new link each time is wasted work.
    private func startTicking() {
        guard !running else { return }
        running = true
        lastTick = CACurrentMediaTime()
        if ticker == nil {
            ticker = DisplayTicker(view: linkView) { [weak self] link in self?.tick(link) }
        }
        ticker?.isPaused = false
    }

    private func stop() {
        running = false
        ticker?.isPaused = true
    }

    private func tick(_ link: CADisplayLink) {
        guard running else { return }
        let now = link.targetTimestamp
        // Slow motion (MD_MOTION_SCALE) slows the pour too, so a film shows it frame by frame.
        let dt = min(max(now - lastTick, 0), 0.1) / IslandMotion.timeScale
        lastTick = now

        let end = safeEnd()
        let backlog = end - revealed
        if backlog > 0 {
            // Aim to drain the buffer in `lag` seconds, never slower than reading speed,
            // and ease toward that rate so a burst of text speeds the pour up smoothly.
            let lag = inputDone ? Self.lagAfterEnd : Self.lagWhileStreaming
            let wanted = max(Self.minRate, Double(backlog) / lag)
            rate += (wanted - rate) * min(1, dt / 0.25)
            carry += rate * dt
            var steps = Int(carry)
            carry -= Double(steps)
            while steps > 0 && revealed < end {
                // A marker comes out whole, as one step: it is one small number on screen.
                let next = Self.markerEnd(in: received, at: revealed) ?? (revealed + 1)
                guard next <= end else { break }
                while revealed < next {
                    shownAt.append(now)
                    revealed += 1
                }
                steps -= 1
            }
        } else {
            carry = 0
        }

        freezeFinishedLines(now: now)
        renderLive(now: now)

        let fading = shownAt.last.map { now - $0 < Self.fade * IslandMotion.timeScale } ?? false
        if revealed >= received.count && !fading {
            // Nothing left to pour or fade. While the stream is open, sleep until the
            // next piece arrives; once it has ended, say so.
            stop()
            if inputDone {
                freezeAll()
                drained()
            }
        }
    }

    /// The answer is complete: its last line will not change either.
    private func freezeAll() {
        for line in live where !line.isPending {
            frozen.append(Line(id: line.id, text: line.text, isGap: line.isGap, isPending: false, end: line.end))
        }
        live = []
        liveStart = received.count
    }

    /// Tell the owner on the next turn of the main queue, not from inside the tick: what
    /// it does next (sources coming in) is an animated change, and made from inside a
    /// display-link callback, SwiftUI applied it without measuring the new height.
    private func drained() {
        guard let done = onDrained else { return }
        onDrained = nil
        DispatchQueue.main.async { done() }
    }

    /// How far the screen may go: the end of the buffer, minus a marker that has only
    /// half arrived ("…batch [c:" waiting for "1]").
    private func safeEnd() -> Int {
        guard !inputDone else { return received.count }
        let tailStart = max(received.count - 12, revealed)
        guard let open = received[tailStart...].lastIndex(of: "[") else { return received.count }
        let rest = received[open...]
        if rest.contains("]") { return received.count }
        return Self.couldBeMarker(rest) ? open : received.count
    }

    // MARK: Lines

    /// A line is frozen once its newline is shown and its last character has faded in.
    private func freezeFinishedLines(now: CFTimeInterval) {
        while let newline = received[liveStart..<revealed].firstIndex(of: "\n") {
            let lastShown = newline > liveStart ? shownAt[newline - 1] : shownAt[newline]
            guard now - lastShown >= Self.fade * IslandMotion.timeScale else { break }
            let slice = received[liveStart..<newline]
            frozen.append(
                Line(
                    id: frozen.count,
                    text: Self.attributed(slice, shownAt: nil, now: now, references: &references),
                    isGap: slice.isEmpty, isPending: false, end: newline
                )
            )
            liveStart = newline + 1
        }
    }

    /// Rebuild the live lines from `liveStart` to the reveal point, with the fade.
    private func renderLive(now: CFTimeInterval) {
        var lines: [Line] = []
        var start = liveStart
        var id = frozen.count
        while true {
            let newline = received[start..<revealed].firstIndex(of: "\n")
            let stop = newline ?? revealed
            let slice = received[start..<stop]
            lines.append(
                Line(
                    id: id,
                    text: Self.attributed(slice, shownAt: shownAt, now: now, references: &references),
                    isGap: slice.isEmpty && newline != nil,
                    isPending: slice.isEmpty && newline == nil,
                    end: stop
                )
            )
            guard let newline else { break }
            start = newline + 1
            id += 1
        }
        if lines != live { live = lines }
        let any = !frozen.isEmpty || lines.contains { !$0.text.characters.isEmpty }
        if any != hasText { hasText = any }
    }

    /// Rebuild every line up to `point` (the checked text replaced the streamed one).
    /// Rebuild the lines up to `point`, fully drawn. Frozen lines that end before
    /// `keepingUpTo` are the same in the new text, so they stay as they are: only the
    /// lines from the first change on are laid out again.
    private func rebuild(upTo point: Int, keepingUpTo common: Int, now: CFTimeInterval) {
        frozen = Array(frozen.prefix { $0.end < common })
        liveStart = frozen.last.map { $0.end + 1 } ?? 0
        references = [:]
        _ = Self.parse(received[0..<liveStart], references: &references)
        revealed = point
        shownAt = Array(repeating: 0, count: point)
        freezeFinishedLines(now: .infinity)
        renderLive(now: now)
    }

    // MARK: Parsing and styling

    private enum Piece {
        case text(String, start: Int)
        case reference(Int, start: Int)

        var text: String {
            switch self {
            case .text(let string, _): string
            case .reference(let number, _): "[\(number)]"
            }
        }
    }

    /// The colour of the answer's words, and the one quiet accent for references.
    static let ink = Color.white.opacity(0.9)
    static let accent = Color(red: 0.62, green: 0.72, blue: 1.0).opacity(0.78)

    /// Characters to pieces: plain runs and references. A space before a reference is
    /// dropped, so "batch [c:1]." reads "batch¹." with the number tucked against the word.
    private static func parse(_ chars: ArraySlice<Character>, references: inout [Int: Int]) -> [Piece] {
        var pieces: [Piece] = []
        var run = ""
        var runStart = chars.startIndex
        var index = chars.startIndex
        while index < chars.endIndex {
            if let end = markerEnd(in: chars, at: index), let id = Int(String(chars[(index + 3)..<(end - 1)])) {
                if run.hasSuffix(" ") { run.removeLast() }
                if !run.isEmpty { pieces.append(.text(run, start: runStart)) }
                run = ""
                let number = references[id] ?? (references.count + 1)
                references[id] = number
                pieces.append(.reference(number, start: index))
                index = end
                runStart = end
            } else {
                if run.isEmpty { runStart = index }
                run.append(chars[index])
                index += 1
            }
        }
        if !run.isEmpty { pieces.append(.text(run, start: runStart)) }
        return pieces
    }

    /// A line as styled text. With `shownAt`, characters that appeared in the last
    /// `fade` seconds are drawn partly transparent: new words ink in instead of popping.
    /// Opacity is rounded to tenths, so a fading tail is at most a few runs.
    private static func attributed(
        _ chars: ArraySlice<Character>, shownAt: [CFTimeInterval]?, now: CFTimeInterval,
        references: inout [Int: Int]
    ) -> AttributedString {
        var result = AttributedString()
        let fade = Self.fade * IslandMotion.timeScale
        func opacity(at index: Int) -> Double {
            guard let shownAt, index < shownAt.count else { return 1 }
            return (min(1, max(0, (now - shownAt[index]) / fade)) * 10).rounded() / 10
        }
        var previousWasReference = false
        for piece in parse(chars, references: &references) {
            switch piece {
            case .text(let string, let start):
                var index = start
                var buffer = ""
                var level = opacity(at: index)
                for character in string {
                    let next = opacity(at: index)
                    if next != level && !buffer.isEmpty {
                        result.append(styled(buffer, level))
                        buffer = ""
                    }
                    level = next
                    buffer.append(character)
                    index += 1
                }
                if !buffer.isEmpty { result.append(styled(buffer, level)) }
                previousWasReference = false
            case .reference(let number, let start):
                // Two references in a row ("¹,²") need a separator, or they read as 12.
                var text = previousWasReference ? ",\(number)" : "\(number)"
                if !previousWasReference { text = "\u{2009}" + text }  // a thin space
                var reference = AttributedString(text)
                reference.font = .system(size: 9.5, weight: .semibold).monospacedDigit()
                reference.baselineOffset = 4.5
                reference.foregroundColor = accent.opacity(opacity(at: start))
                result.append(reference)
                previousWasReference = true
            }
        }
        return result
    }

    private static func styled(_ string: String, _ opacity: Double) -> AttributedString {
        var text = AttributedString(string)
        text.foregroundColor = Color.white.opacity(0.9 * opacity)
        return text
    }

    /// If a whole marker `[c:<digits>]` starts at `index`, the index just past it.
    private static func markerEnd<C: RandomAccessCollection>(in chars: C, at index: Int) -> Int?
    where C.Element == Character, C.Index == Int {
        guard index + 3 < chars.endIndex, chars[index] == "[", chars[index + 1] == "c", chars[index + 2] == ":"
        else { return nil }
        var cursor = index + 3
        while cursor < chars.endIndex, chars[cursor].isASCII, chars[cursor].isNumber { cursor += 1 }
        guard cursor > index + 3, cursor < chars.endIndex, chars[cursor] == "]" else { return nil }
        return cursor + 1
    }

    /// "[", "[c", "[c:", "[c:12": the start of a marker whose end has not arrived yet.
    private static func couldBeMarker(_ rest: ArraySlice<Character>) -> Bool {
        let pattern: [Character] = ["[", "c", ":"]
        for (offset, character) in rest.enumerated() {
            if offset < 3 {
                if character != pattern[offset] { return false }
            } else if !(character.isASCII && character.isNumber) {
                return false
            }
        }
        return rest.count <= 10
    }
}

/// A display link with a closure: it fires once per refresh of the screen the view is on
/// (120 times a second on a ProMotion display), in step with SwiftUI's own frames.
@MainActor
final class DisplayTicker: NSObject {
    private var link: CADisplayLink?
    private let action: (CADisplayLink) -> Void

    init(view: NSView?, action: @escaping (CADisplayLink) -> Void) {
        self.action = action
        super.init()
        let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main
        link = view?.displayLink(target: self, selector: #selector(fire(_:)))
            ?? screen?.displayLink(target: self, selector: #selector(fire(_:)))
        link?.add(to: .main, forMode: .common)
    }

    @objc private func fire(_ link: CADisplayLink) {
        action(link)
    }

    var isPaused: Bool {
        get { link?.isPaused ?? true }
        set { link?.isPaused = newValue }
    }
}
