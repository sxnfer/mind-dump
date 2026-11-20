import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let api = APIClient.local
    private lazy var monitor = HealthMonitor(url: api.healthURL)
    private lazy var capture = CaptureModel(api: api)
    private var panel: NotchPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let panel = NotchPanel(rootView: StatusView(monitor: monitor, capture: capture))
        panel.placeAtNotch()
        syncGeometry(from: panel)
        panel.orderFrontRegardless()  // show it without making the app active
        self.panel = panel

        // The model says "open" or "closed"; the panel resizes and takes or gives back focus.
        capture.onExpandedChange = { [weak self, weak panel] expanded in
            guard let self, let panel else { return }
            panel.setExpanded(expanded)
            self.syncGeometry(from: panel)
        }

        // Re-place the panel when a display is plugged in, removed or rearranged.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screensChanged),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )

        monitor.start()
    }

    @objc private func screensChanged() {
        guard let panel else { return }
        panel.placeAtNotch()
        syncGeometry(from: panel)
    }

    private func syncGeometry(from panel: NotchPanel) {
        capture.notchWidth = panel.notchWidth
        capture.barHeight = panel.barHeight
    }
}
