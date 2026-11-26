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
    /// Option-Shift-Space from any app (D23).
    private let hotKey = GlobalHotKey()
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

        // The keyboard rule lives in `IslandModel.hasKeyboard`; this only carries it out.
        island.onOpenChange = { [weak self] open, keyboard in self?.applyKeyboard(open: open, keyboard: keyboard) }
        // A capsule that opened by itself waits; a press inside it (not on the notch, which
        // closes it) makes it the user's. The caret then goes to the end of the kept words.
        window.onWaitingPress = { [weak self] point in
            guard let self, let window = self.window else { return }
            if point.y < window.bodyRect(for: self.island.metrics).minY { self.island.claim() }
        }
        window.onClaimEnded = { [weak self] in self?.caretToEnd() }
        listenForKeyboardLoss(window)
        // Closing stops an answer still on its way (the backend then saves nothing).
        island.onWillClose = { [weak self] in self?.ask.panelWillClose() }
        ask.reveal.linkView = window.contentView
        listenForPanelKeys()
        hotKey.onPress = { [weak self] in self?.shortcutPressed() }
        hotKey.register()

        #if DEBUG
        if let view = window.contentView { frames = FrameMonitor(view: view) }
        let film = FilmRecorder(window: window)
        self.film = film
        script = DebugScript(window: window, island: island, pointer: pointer, film: film)
        script?.onMeasure = { [weak self] name in self?.frames?.begin(name) }
        script?.drop = drop
        script?.onHotKey = { [weak self] edge in self?.hotKey.handle(edge) }
        DebugRemote.listen { [weak self] command in self?.debug(command) }
        print("[motion] tuning \(IslandMotion.tuning.rawValue)")
        // A whole answer stream is timed as one span, not line by line.
        ask.onStream = { [weak self] name in
            if let name { self?.frames?.beginSpan(name) } else { self?.frames?.endSpan() }
        }
        #endif
        island.onTransition = { [weak self] name in
            self?.pointer?.islandChanged()
            #if DEBUG
            self?.frames?.begin(name)
            // Closed, a pill, a peek, a drop target or a waiting capsule must never hold
            // the keyboard. Silence here is the proof; this line would say otherwise.
            if let self, let window = self.window, window.isKeyWindow, !self.island.hasKeyboard {
                print("[key] WRONG: key after \(name): \(KeyState.describe(window: window, island: self.island))")
                fflush(stdout)
            }
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

    func applicationWillTerminate(_ notification: Notification) {
        hotKey.unregister()
    }

    /// The global shortcut was pressed. The rule for what it does lives in the model;
    /// this only adds what the window must do after a claim.
    private func shortcutPressed() {
        let result = island.shortcutPressed()
        // A claim by click puts the caret at the end once the press is handled; with no
        // press here, do it now, so typing adds to the kept words and Return retries.
        if result == .claimed { caretToEnd() }
        #if DEBUG
        print("[hotkey] \(result.rawValue)")
        fflush(stdout)
        #endif
    }

    /// Where answers come from: the backend, or in debug builds with `MD_ASK_FIXTURE=1`,
    /// a canned stream that costs nothing (see `AskFixture`).
    private var answerSource: AnswerSource {
        #if DEBUG
        if ProcessInfo.processInfo.environment["MD_ASK_FIXTURE"] == "1" { return AskFixture() }
        #endif
        return api
    }

    /// Keys that belong to the capsule, not to the text box: Tab switches mode, ⌘N
    /// starts a new question. A local monitor sees them before the text field does
    /// (the field would take Tab to move focus). No permission is needed for our own
    /// window's keys.
    private func listenForPanelKeys() {
        keys = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, self.island.presentation.isOpen else { return event }
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

    /// Carries out `IslandModel.hasKeyboard`: take the keyboard, or make sure this window
    /// does not hold it.
    private func applyKeyboard(open: Bool, keyboard: Bool) {
        guard let window else { return }
        window.isCapsuleOpen = open
        window.takesKeyboard = keyboard
        if keyboard {
            window.makeKey()
        } else if window.isKeyWindow {
            window.resignKey()
        }
    }

    /// After a press claimed a waiting capsule: the caret at the end of the kept words, so
    /// typing adds to them and Return retries.
    private func caretToEnd() {
        // On the next turn: the text field places the caret where the click landed after
        // the press is handled, and this must come after that.
        DispatchQueue.main.async { [weak self] in
            guard let editor = self?.window?.firstResponder as? NSTextView, editor.isFieldEditor else { return }
            editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        }
    }

    /// The user moved to another app while the capsule had the keyboard: it closes, as a
    /// click elsewhere closes it.
    ///
    /// The window's own signal comes first: a key window is told when it stops being key,
    /// whatever took the keyboard (Cmd-Tab, a Dock click, a click in another app's window,
    /// another app opening a window). An app that becomes active is watched as well, in
    /// case a switch ever leaves this non-activating panel key. Both end in the same close,
    /// which does nothing the second time.
    private func listenForKeyboardLoss(_ window: NotchWindow) {
        let center = NotificationCenter.default
        center.addObserver(forName: NSWindow.didBecomeKeyNotification, object: window, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.logKey("became key") }
        }
        center.addObserver(forName: NSWindow.didResignKeyNotification, object: window, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.logKey("resigned key")
                self.island.keyboardTaken()
            }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] note in
            let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            let other = app?.processIdentifier != ProcessInfo.processInfo.processIdentifier
            MainActor.assumeIsolated {
                guard let self, other, self.island.hasKeyboard else { return }
                self.logKey("another app became active")
                self.island.keyboardTaken()
            }
        }
    }

    /// Debug builds print one `[key]` line for every change of who holds the keyboard.
    private func logKey(_ event: String) {
        #if DEBUG
        guard let window else { return }
        print("[key] \(event): \(KeyState.describe(window: window, island: island))")
        fflush(stdout)
        #endif
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
        case "open": island.openByUser()
        case "keystate": logKey("state")
        case "resignkey": window?.resignKey()
        case "hotkey":
            // The same path as a real press, from just after the system's delivery.
            print("[hotkey] debug press")
            hotKey.handle(.pressed)
            hotKey.handle(.released)
        case "close": island.close()
        case "saved", "reading", "ready", "failed":
            if let activity = DebugRemote.pill(command) { island.show(activity) }
        case "film": film?.film()
        case "snap": film?.snap()
        case "script": script?.run()
        case let tuning where tuning.hasPrefix("tuning-"):
            IslandMotion.switchTuning(to: String(tuning.dropFirst("tuning-".count)))
        default: break
        }
    }
    #endif
}
