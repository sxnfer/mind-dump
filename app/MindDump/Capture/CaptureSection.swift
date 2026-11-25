import SwiftUI

/// Capture mode, under the shared box (see `PanelView`): what Return will do, and why a
/// save failed. The box itself belongs to the panel, so it survives a mode switch.
struct CaptureSection: View {
    let capture: CaptureModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            hint
            if let message = capture.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.orange)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Says what Return will do, before you press it.
    private var hint: some View {
        HStack(spacing: 6) {
            switch capture.kind {
            case .empty:
                Text("Return saves · Esc closes")
            case .note:
                Image(systemName: "note.text")
                Text("Return saves this note")
            case .link(let url):
                Image(systemName: "link")
                Text(capture.text.isEmpty ? "Return saves the link on your clipboard:" : "Return saves this link:")
                Text(url.host() ?? url.absoluteString)
                    .foregroundStyle(.white.opacity(0.85))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .font(.system(size: 12))
        .foregroundStyle(.white.opacity(0.5))
        .contentTransition(.opacity)
        .animation(.easeOut(duration: 0.15), value: capture.kind)
    }
}
