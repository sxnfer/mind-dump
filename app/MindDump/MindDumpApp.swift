import SwiftUI

/// Entry point. The app has no normal windows and no Dock icon (LSUIElement):
/// it lives in the notch panel that AppDelegate creates.
@main
struct MindDumpApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // SwiftUI needs at least one scene. An empty Settings scene opens no window.
        Settings { EmptyView() }
    }
}
