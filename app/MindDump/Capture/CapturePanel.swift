import SwiftUI

/// The open island: a title and the backend's status beside the notch, and one box below.
struct CapturePanel: View {
    let island: IslandModel
    @Bindable var capture: CaptureModel
    let monitor: HealthMonitor
    /// Built once, invisibly, at launch (see `IslandView.rehearsal`). A rehearsal copy
    /// never reads the clipboard and never takes the keyboard.
    var isRehearsal = false

    @FocusState private var boxFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            band
            VStack(alignment: .leading, spacing: 10) {
                box
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
            .padding(.horizontal, IslandLayout.panelPadding)
            .padding(.top, 10)
            .padding(.bottom, IslandLayout.panelPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            // The body follows the content's height with a spring (`IslandMotion.resize`).
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                island.panelContentMeasured(height)
            }
        }
        .frame(width: IslandLayout.panelWidth)
        // Children move as one block while the body morphs, not each on its own.
        .geometryGroup()
        .animation(IslandMotion.spring(0.3, 1), value: capture.errorMessage)
        .onAppear {
            guard !isRehearsal else { return }
            capture.panelOpened()
            focusBox()
        }
        .onDisappear { if !isRehearsal { capture.panelClosed() } }
    }

    /// AppKit sets up text editing the first time any field takes focus, which costs
    /// about 12 ms: more than one frame at 120 Hz. On the first open after launch, wait
    /// until the spring's fast part is over, where a late frame cannot be seen. Every
    /// later open focuses at once.
    private func focusBox() {
        guard !Self.hasFocusedOnce else {
            boxFocused = true
            return
        }
        Self.hasFocusedOnce = true
        Task {
            try? await Task.sleep(for: IslandMotion.seconds(0.24))
            boxFocused = true
        }
    }

    private static var hasFocusedOnce = false

    /// Level with the menu bar: the title left of the notch, the status dot right of it.
    private var band: some View {
        HStack(spacing: 0) {
            Label("Capture", systemImage: "tray.and.arrow.down.fill")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
                .labelStyle(.titleAndIcon)
                .padding(.leading, IslandLayout.panelPadding)
                .frame(maxWidth: .infinity, alignment: .leading)

            Color.clear.frame(width: island.layout.notch.width)

            HealthDot(monitor: monitor)
                .padding(.trailing, IslandLayout.panelPadding)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .frame(height: island.layout.notch.height)
    }

    private var box: some View {
        TextField(
            "Note or link", text: $capture.text,
            // The system placeholder colour is dark grey: invisible on true black.
            prompt: Text("Type a note, or paste a link").foregroundStyle(.white.opacity(0.34)),
            axis: .vertical
        )
            .textFieldStyle(.plain)
            .font(.system(size: 16))
            .lineLimit(1...4)
            .focused($boxFocused)
            .onSubmit { Task { await capture.submit() } }
            .disabled(capture.isSaving || isRehearsal)
            .accessibilityLabel("Note or link")
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
