import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let api = APIClient.local
    private lazy var monitor = HealthMonitor(url: api.healthURL)
    private let island = IslandModel()
    private lazy var capture = CaptureModel(api: api, island: island)
    private var window: NotchWindow?
    private var pointer: PointerTracker?
    #if DEBUG
    private var frames: FrameMonitor?
    private var film: FilmRecorder?
    private var script: DebugScript?
    #endif

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NotchWindow(
            rootView: IslandView(island: island, capture: capture, monitor: monitor)
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

        // An open panel takes the keyboard; anything else gives it back to your app.
        island.onPanelChange = { [weak window] open in
            guard let window else { return }
            window.takesKeyboard = open
            if open { window.makeKey() } else { window.resignKey() }
        }

        #if DEBUG
        if let view = window.contentView { frames = FrameMonitor(view: view) }
        let film = FilmRecorder(window: window)
        self.film = film
        script = DebugScript(window: window, island: island, pointer: pointer, film: film)
        DebugRemote.listen { [weak self] command in self?.debug(command) }
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
