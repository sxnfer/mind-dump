import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let api = APIClient.local
    private lazy var monitor = HealthMonitor(url: api.healthURL)
    private let island = IslandModel()
    private lazy var capture = CaptureModel(api: api, island: island)
    private lazy var ask = AskModel(source: answerSource, island: island)
    private lazy var panel = PanelModel(capture: capture, ask: ask, island: island)
    private lazy var drop = DropModel(panel: panel, island: island)
    private var keys: Any?
    private var window: NotchWindow?
    private var pointer: PointerTracker?
    #if DEBUG
    private var frames: FrameMonitor?
    private var film: FilmRecorder?
    private var script: DebugScript?
    #endif

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NotchWindow(
            rootView: IslandView(island: island, panel: panel, monitor: monitor)
        )
        island.layout = window.placeAtNotch()
        window.orderFrontRegardless()  // show it without making the app active
        // AppKit builds its shared text editor the first time any field takes focus,
        // which stalls the first open by two or three frames. Build it now instead.
        _ = window.fieldEditor(true, for: nil)
        self.window = window

        let pointer = PointerTracker(window: window, island: island)
        pointer.start()
        self.pointer = pointer

        // Drop-and-save: text dragged onto the island. The target follows the island's
        // hot zone, not the whole see-through window.
        window.dropReceiver = drop
        drop.zoneContains = { [weak pointer] point in pointer?.contains(point) ?? false }

        // An open panel takes the keyboard; anything else gives it back to your app.
        island.onPanelChange = { [weak window, weak self] open in
            guard let window else { return }
            window.takesKeyboard = open
            if open { window.makeKey() } else { window.resignKey() }
        }
        // Closing stops an answer still on its way (the backend then saves nothing).
        island.onPanelWillClose = { [weak self] in self?.ask.panelWillClose() }
        ask.reveal.linkView = window.contentView
        listenForPanelKeys()

        #if DEBUG
        if let view = window.contentView { frames = FrameMonitor(view: view) }
        let film = FilmRecorder(window: window)
        self.film = film
        script = DebugScript(window: window, island: island, pointer: pointer, film: film)
        script?.onMeasure = { [weak self] name in self?.frames?.begin(name) }
        script?.drop = drop
        DebugRemote.listen { [weak self] command in self?.debug(command) }
        // A whole answer stream is timed as one span, not line by line.
        ask.onStream = { [weak self] name in
            if let name { self?.frames?.beginSpan(name) } else { self?.frames?.endSpan() }
        }
        #endif
        island.onTransition = { [weak self] name in
            self?.pointer?.islandChanged()
            #if DEBUG
            self?.frames?.begin(name)
            #endif
        }

        // Re-place the window when a display is plugged in, removed or rearranged.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screensChanged),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )

        monitor.start()
    }

    /// Where answers come from: the backend, or in debug builds with `MD_ASK_FIXTURE=1`,
    /// a canned stream that costs nothing (see `AskFixture`).
    private var answerSource: AnswerSource {
        #if DEBUG
        if ProcessInfo.processInfo.environment["MD_ASK_FIXTURE"] == "1" { return AskFixture() }
        #endif
        return api
    }

    /// Panel keys that belong to the panel, not to the text box: Tab switches mode, ⌘N
    /// starts a new question. A local monitor sees them before the text field does
    /// (the field would take Tab to move focus). No permission is needed for our own
    /// window's keys.
    private func listenForPanelKeys() {
        keys = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, self.island.presentation.isPanel else { return event }
            let modifiers = event.modifierFlags.intersection([.command, .option, .control])
            if event.keyCode == 48, modifiers.isEmpty {  // Tab (and Shift-Tab)
                self.panel.toggleMode()
                return nil
            }
            if event.keyCode == 45, modifiers == .command, self.panel.mode == .ask {  // ⌘N
                self.ask.newQuestion()
                return nil
            }
            return event
        }
    }

    @objc private func screensChanged() {
        guard let window else { return }
        island.layout = window.placeAtNotch()
        pointer?.islandChanged()
    }

    #if DEBUG
    private func debug(_ command: String) {
        switch command {
        case "peek":
            island.hoverIsScripted = true
            island.hover(true, scripted: true)
        case "unpeek":
            island.hoverIsScripted = false
            island.hover(false, scripted: true)
        case "open": island.open()
        case "close": island.close()
        case "saved": island.show(IslandActivity(id: "debug", glyph: .check, text: "Saved"))
        case "reading":
            island.show(IslandActivity(id: "debug", glyph: .spinner, text: "Reading page", hold: nil))
        case "ready": island.show(IslandActivity(id: "debug", glyph: .check, text: "Ready"))
        case "failed":
            island.show(IslandActivity(id: "debug", glyph: .warning, text: "Couldn't read page", hold: 3))
        case "film": film?.film()
        case "snap": film?.snap()
        case "script": script?.run()
        default: break
        }
    }
    #endif
}
