#if DEBUG
import AppKit
import ImageIO
import QuartzCore
import UniformTypeIdentifiers

/// Times every frame of a transition and prints a one-line verdict.
///
/// "Smooth" is otherwise an opinion. A display link fires once per screen refresh (120
/// times a second on a ProMotion Mac). If the main thread is busy, the next tick comes
/// late, and that late tick is a dropped frame you would see as a stutter.
///
/// Run the app from Terminal to read the output:
///     app/build/…/MindDump.app/Contents/MacOS/MindDump
@MainActor
final class FrameMonitor: NSObject {
    private weak var view: NSView?
    private var link: CADisplayLink?
    private var stamps: [CFTimeInterval] = []
    private var name = ""
    private var endsAt: CFTimeInterval = 0
    /// A span (a whole answer stream) is being timed: shorter transitions inside it are
    /// part of it, not timed on their own.
    private var inSpan = false

    init(view: NSView) {
        self.view = view
    }

    /// Start (or restart) timing. Measures for a little longer than the longest spring.
    func begin(_ name: String) {
        guard !inSpan else { return }
        start(name, until: CACurrentMediaTime() + 1.3 * IslandMotion.timeScale)
    }

    /// Time everything from now until `endSpan`, however long that is.
    func beginSpan(_ name: String) {
        inSpan = false
        start(name, until: .infinity)
        inSpan = true
    }

    func endSpan() {
        guard inSpan else { return }
        inSpan = false
        finish()
    }

    private func start(_ name: String, until end: CFTimeInterval) {
        if link != nil { finish() }
        guard let view else { return }
        self.name = name
        stamps = []
        endsAt = end
        let link = view.displayLink(target: self, selector: #selector(tick(_:)))
        link.add(to: .main, forMode: .common)
        self.link = link
    }

    @objc private func tick(_ link: CADisplayLink) {
        stamps.append(link.timestamp)
        if link.timestamp >= endsAt { finish() }
    }

    private func finish() {
        link?.invalidate()
        link = nil
        inSpan = false
        guard stamps.count > 2 else { return }
        let gaps = zip(stamps.dropFirst(), stamps).map { ($0 - $1) * 1000 }
        let sorted = gaps.sorted()
        let median = sorted[sorted.count / 2]
        let worst = sorted.last ?? 0
        // A tick more than half a frame late means at least one refresh was missed.
        // Say when each late tick happened: a stall in the first 150 ms, while the body
        // moves fastest, is seen; the same stall in the slow tail is not.
        var elapsed = 0.0
        var late: [String] = []
        for gap in gaps {
            elapsed += gap
            if gap > median * 1.5 { late.append(String(format: "%.0f ms late at +%.0f ms", gap - median, elapsed - gap)) }
        }
        let line = String(
            format: "[frames] %@: %d frames, median %.2f ms (%.0f Hz), worst %.2f ms, late %d%@",
            name, gaps.count, median, 1000 / median, worst, late.count,
            late.isEmpty ? "" : " (" + late.prefix(8).joined(separator: "; ") + ")"
        )
        print(line)
        fflush(stdout)
    }
}

/// Lets a shell command drive the island, so its states can be reached without a hand
/// on the mouse:  notifyutil -p io.github.sxnfer.MindDump.debug.open
enum DebugRemote {
    static let commands = ["peek", "unpeek", "open", "close", "saved", "reading", "ready", "failed", "film", "snap", "script"]
    private static let prefix = "io.github.sxnfer.MindDump.debug."

    @MainActor private static var handler: ((String) -> Void)?

    @MainActor
    static func listen(_ handler: @escaping (String) -> Void) {
        self.handler = handler
        let center = CFNotificationCenterGetDarwinNotifyCenter()
        for command in commands {
            CFNotificationCenterAddObserver(
                center, nil,
                { _, _, name, _, _ in
                    guard let raw = name?.rawValue as String? else { return }
                    let command = String(raw.dropFirst(DebugRemote.prefix.count))
                    Task { @MainActor in DebugRemote.handler?(command) }
                },
                (prefix + command) as CFString, nil, .deliverImmediately
            )
        }
    }
}

/// Films the app's own window, so a transition can be looked at frame by frame.
///
/// It asks the window server for this window's pixels as they are on screen, shadow and
/// blur included, which is the only honest picture of an animation in flight. An app may
/// always capture its own windows; no Screen Recording permission is involved. Run with
/// `MD_MOTION_SCALE=8` so the frames land in slow motion.
///
///     notifyutil -p io.github.sxnfer.MindDump.debug.film   (then trigger a transition)
@MainActor
final class FilmRecorder {
    private weak var window: NSWindow?
    private var frames: [(time: Double, image: CGImage)] = []
    private var timer: Timer?
    private var startedAt: CFTimeInterval = 0
    private var reel = 0

    init(window: NSWindow) {
        self.window = window
    }

    /// Where the frames go: the app's own temporary folder (inside its sandbox).
    static var folder: URL {
        FileManager.default.temporaryDirectory.appending(path: "film", directoryHint: .isDirectory)
    }

    /// One still, now.
    func snap() {
        guard let image = capture() else { return }
        reel += 1
        write([(0, image)], name: "snap-\(reel)")
    }

    /// `count` frames, evenly spread over the scaled length of the longest transition.
    func film(count: Int = 20, seconds: Double = 0.8) {
        timer?.invalidate()
        frames = []
        startedAt = CACurrentMediaTime()
        let step = seconds * IslandMotion.timeScale / Double(count - 1)
        timer = Timer.scheduledTimer(withTimeInterval: step, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick(count: count) }
        }
        tick(count: count)
    }

    private func tick(count: Int) {
        if let image = capture() {
            frames.append(((CACurrentMediaTime() - startedAt) / IslandMotion.timeScale, image))
        }
        guard frames.count >= count else { return }
        timer?.invalidate()
        timer = nil
        reel += 1
        write(frames, name: "reel-\(reel)")
        frames = []
    }

    /// The window's on-screen pixels. `CGWindowListCreateImage` is hidden from Swift in
    /// current SDKs (ScreenCaptureKit replaces it, but that needs a permission prompt),
    /// so this debug-only code looks the function up by name.
    private func capture() -> CGImage? {
        typealias Function = @convention(c) (CGRect, UInt32, UInt32, UInt32) -> Unmanaged<CGImage>?
        guard
            let window,
            let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "CGWindowListCreateImage")
        else { return nil }
        let function = unsafeBitCast(symbol, to: Function.self)
        // 8 = only this window; 1 | 16 = ignore the window frame, one pixel per point.
        let image = function(.null, 8, UInt32(window.windowNumber), 1 | 16)?.takeRetainedValue()
        // Keep the top strip, where the island lives.
        guard let image else { return nil }
        let height = min(image.height, 470)
        return image.cropping(to: CGRect(x: 0, y: 0, width: image.width, height: height))
    }

    private func write(_ frames: [(time: Double, image: CGImage)], name: String) {
        let folder = Self.folder.appending(path: name, directoryHint: .isDirectory)
        try? FileManager.default.removeItem(at: folder)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var times: [String] = []
        for (index, frame) in frames.enumerated() {
            let url = folder.appending(path: String(format: "f%02d.png", index))
            guard let out = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
            else { continue }
            CGImageDestinationAddImage(out, frame.image, nil)
            CGImageDestinationFinalize(out)
            times.append(String(format: "f%02d %.0f ms", index, frame.time * 1000))
        }
        try? times.joined(separator: "\n").write(to: folder.appending(path: "times.txt"), atomically: true, encoding: .utf8)
        print("[film] \(name): \(frames.count) frame(s) in \(folder.path)")
        fflush(stdout)
    }
}

/// Plays a short script of real input events, so a whole flow can be run the way a person
/// would: move the pointer onto the notch, click, type, press Return.
///
/// Mouse and key events are posted to the app's own event queue, so they travel the same
/// path as real ones (monitors, hit testing, the text field's editor). The pointer is a
/// stand-in, because moving the real one needs Accessibility permission.
///
/// Write lines to `<temporary folder>/script.txt`, then:
///     notifyutil -p io.github.sxnfer.MindDump.debug.script
///
///     pointer notch | away      move the stand-in pointer
///     click                     press and release on the island
///     click away                press somewhere else on the screen
///     type Buy oat milk         one key event per character
///     key return | escape | tab | cmd-n
///     click mode-ask            press a named control (see `debugTarget`): mode-ask,
///                               mode-capture, new-question, source-1, open-1, …
///     scroll -120               scroll wheel over the answer (negative: towards the top)
///     measure typing            time the next 1.3 s under this name
///     wait 0.5                  seconds, in animation time
///     film | snap               start a reel / take one still
@MainActor
final class DebugScript {
    private let window: NotchWindow
    private let island: IslandModel
    private let pointer: PointerTracker
    private let film: FilmRecorder
    private var running: Task<Void, Never>?
    /// Starts the frame monitor by hand, for things that are not transitions (typing).
    var onMeasure: ((String) -> Void)?

    init(window: NotchWindow, island: IslandModel, pointer: PointerTracker, film: FilmRecorder) {
        self.window = window
        self.island = island
        self.pointer = pointer
        self.film = film
    }

    func run() {
        let url = FileManager.default.temporaryDirectory.appending(path: "script.txt")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            print("[script] no script at \(url.path)")
            return
        }
        running?.cancel()
        running = Task {
            for line in text.split(separator: "\n") {
                guard !Task.isCancelled else { return }
                await step(String(line).trimmingCharacters(in: .whitespaces))
            }
            // Hand the pointer back to the real mouse. Without this the stand-in stays
            // where the script left it ("away"), and the island ignores the person.
            pointer.scriptedPointer = nil
            print("[script] done")
            fflush(stdout)
        }
    }

    private func step(_ line: String) async {
        let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
        guard let verb = parts.first, !verb.hasPrefix("#") else { return }
        let rest = parts.count > 1 ? parts[1] : ""
        switch verb {
        case "pointer":
            let body = window.bodyRect(for: island.metrics)
            pointer.scriptedPointer =
                rest == "away" ? NSPoint(x: body.midX, y: body.minY - 300) : NSPoint(x: body.midX, y: body.maxY - 6)
        case "click":
            if rest == "away" {
                let body = window.bodyRect(for: island.metrics)
                pointer.scriptedPointer = NSPoint(x: body.midX, y: body.minY - 300)
                pointer.clickedElsewhere()  // what the global click monitor would call
            } else if rest.isEmpty {
                let body = window.bodyRect(for: island.metrics)
                await click(at: NSPoint(x: body.midX + 30, y: body.midY))
            } else if let point = target(rest) {
                await click(at: point)
            }
        case "scroll":
            scroll(by: Double(rest) ?? -100)
        case "type":
            for character in rest {
                key(String(character), code: 0)
                try? await Task.sleep(for: .milliseconds(18))
            }
        case "key":
            if rest == "return" { key("\r", code: 36) }
            if rest == "escape" { key("\u{1B}", code: 53) }
            if rest == "tab" { key("\t", code: 48) }
            if rest == "cmd-n" { key("n", code: 45, modifiers: .command) }
        case "wait":
            try? await Task.sleep(for: IslandMotion.seconds(Double(rest) ?? 0.5))
        case "film": film.film()
        case "snap": film.snap()
        case "measure": onMeasure?(rest)
        default: print("[script] unknown: \(line)")
        }
    }

    /// The centre of a named control, on screen (see `debugTarget`).
    private func target(_ name: String) -> NSPoint? {
        // Only controls on screen now count: a target that is gone (or was only ever in
        // the launch rehearsal) must not send a click to an old spot, where it would land
        // outside the island and close the panel.
        guard let frame = DebugTargets.frames[name]?.frame, let content = window.contentView else {
            print("[script] no target: \(name)")
            return nil
        }
        // SwiftUI's global frame starts at the window's top-left; AppKit's at the bottom-left.
        let inWindow = NSPoint(x: frame.midX, y: content.bounds.height - frame.midY)
        let point = window.convertPoint(toScreen: inWindow)
        // On screen means inside the island's body, and inside the answer's visible area
        // when the control scrolls with the answer.
        var visible = window.bodyRect(for: island.metrics)
        if name.hasPrefix("source-") || name.hasPrefix("open-"), let scrollView = answerScrollView() {
            let shown = scrollView.convert(scrollView.bounds, to: nil)
            visible = visible.intersection(window.convertToScreen(shown))
        }
        guard visible.contains(point) else {
            print("[script] target out of view: \(name)")
            return nil
        }
        return point
    }

    /// Press and release at a point on screen.
    private func click(at screenPoint: NSPoint) async {
        pointer.scriptedPointer = screenPoint
        let inWindow = window.convertPoint(fromScreen: screenPoint)
        mouse(.leftMouseDown, at: inWindow)
        try? await Task.sleep(for: .milliseconds(70))
        mouse(.leftMouseUp, at: inWindow)
    }

    private func mouse(_ type: NSEvent.EventType, at point: NSPoint) {
        guard
            let event = NSEvent.mouseEvent(
                with: type, location: point, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                context: nil, eventNumber: 0, clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0
            )
        else { return }
        NSApp.postEvent(event, atStart: false)
    }

    /// A scroll wheel turn over the answer, near the bottom of the open panel. (Not a
    /// named target: tracking the scroll view's frame on every scroll step cost frames.)
    private func scroll(by points: Double) {
        let body = window.bodyRect(for: island.metrics)
        let point = NSPoint(x: body.midX, y: body.minY + 90)
        pointer.scriptedPointer = point
        guard
            let cgEvent = CGEvent(
                scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: Int32(points), wheel2: 0, wheel3: 0
            )
        else { return }
        // CGEvent locations start at the top-left of the main screen. Naming our window
        // as the one under the pointer gives the event a position inside it.
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        cgEvent.location = CGPoint(x: point.x, y: top - point.y)
        cgEvent.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: Int64(window.windowNumber))
        cgEvent.setIntegerValueField(
            .mouseEventWindowUnderMousePointerThatCanHandleThisEvent, value: Int64(window.windowNumber)
        )
        guard let event = NSEvent(cgEvent: cgEvent), let scrollView = answerScrollView() else { return }
        // Straight to the answer's scroll view: the wheel event has no window position.
        scrollView.scrollWheel(with: event)
    }

    /// The tallest scroll view in the window: the answer's (the box has a small one).
    private func answerScrollView() -> NSScrollView? {
        func all(_ view: NSView) -> [NSScrollView] {
            if let scrollView = view as? NSScrollView { return [scrollView] }
            return view.subviews.flatMap(all)
        }
        return window.contentView.map(all)?.max { $0.frame.height < $1.frame.height }
    }

    private func key(_ characters: String, code: UInt16, modifiers: NSEvent.ModifierFlags = []) {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard
                let event = NSEvent.keyEvent(
                    with: type, location: .zero, modifierFlags: modifiers,
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, characters: characters, charactersIgnoringModifiers: characters,
                    isARepeat: false, keyCode: code
                )
            else { continue }
            NSApp.postEvent(event, atStart: false)
        }
    }
}
#endif

import SwiftUI

#if DEBUG
/// Where named controls are on screen, so a debug script can click them as a person would.
@MainActor
enum DebugTargets {
    /// Each frame with the view that recorded it, so only that view can remove it.
    static var frames: [String: (owner: UUID, frame: CGRect)] = [:]
}

private struct DebugTargetRecorder: ViewModifier {
    let name: String
    @State private var owner = UUID()
    @Environment(\.isRehearsal) private var isRehearsal

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame in
                guard !isRehearsal else { return }  // the launch rehearsal is never clicked
                DebugTargets.frames[name] = (owner, frame)
            }
            .onDisappear {
                if DebugTargets.frames[name]?.owner == owner { DebugTargets.frames[name] = nil }
            }
    }
}
#endif

extension EnvironmentValues {
    /// True inside the launch rehearsal (see `IslandView.rehearsal`).
    @Entry var isRehearsal = false
}

extension View {
    /// Debug builds remember this view's frame under `name` (for `click <name>` in a debug
    /// script). Release builds do nothing.
    func debugTarget(_ name: String) -> some View {
        #if DEBUG
        modifier(DebugTargetRecorder(name: name))
        #else
        self
        #endif
    }
}
