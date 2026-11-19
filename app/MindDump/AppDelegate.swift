import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Where the backend answers. One place to change it; settings come later.
    private static let healthURL = URL(string: "http://127.0.0.1:8000/health")!

    private let monitor = HealthMonitor(url: AppDelegate.healthURL)
    private var panel: NotchPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let panel = NotchPanel(rootView: StatusView(monitor: monitor))
        panel.placeAtNotch()
        panel.orderFrontRegardless()  // show it without making the app active
        self.panel = panel

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
        panel?.placeAtNotch()
    }
}
