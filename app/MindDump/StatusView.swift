import AppKit
import SwiftUI

/// The black notch extension with one status dot in its right "ear".
struct StatusView: View {
    let monitor: HealthMonitor

    var body: some View {
        ZStack(alignment: .trailing) {
            UnevenRoundedRectangle(bottomLeadingRadius: 10, bottomTrailingRadius: 10)
                .fill(.black)  // true black, to match the notch

            Circle()
                .fill(dotColor)
                .frame(width: 8, height: 8)
                .padding(.trailing, 14)
                .animation(.easeInOut(duration: 0.25), value: monitor.status)
                .help(label)
                .accessibilityLabel(label)
        }
        .contextMenu {
            // No Dock icon or menu bar, so right-click is the way to quit.
            Button("Quit Mind Dump") { NSApp.terminate(nil) }
        }
    }

    private var dotColor: Color {
        switch monitor.status {
        case .unknown: .gray
        case .up: .green
        case .down: .red
        }
    }

    private var label: String {
        switch monitor.status {
        case .unknown: "Checking backend…"
        case .up: "Backend is running"
        case .down: "Backend is not answering"
        }
    }
}
