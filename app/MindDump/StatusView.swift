import AppKit
import SwiftUI

/// The black notch panel: + on the left ear, the status dot on the right ear,
/// and, when open, the capture area underneath.
struct StatusView: View {
    let monitor: HealthMonitor
    let capture: CaptureModel

    var body: some View {
        VStack(spacing: 0) {
            topBar
            if capture.isExpanded {
                CaptureArea(capture: capture)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(
            UnevenRoundedRectangle(
                bottomLeadingRadius: capture.isExpanded ? 18 : 10,
                bottomTrailingRadius: capture.isExpanded ? 18 : 10
            )
            .fill(.black)  // true black, to match the notch
        )
        .foregroundStyle(.white)
        .onExitCommand { capture.close() }  // Escape
        .contextMenu {
            // No Dock icon or menu bar, so right-click is the way to quit.
            Button("Quit Mind Dump") { NSApp.terminate(nil) }
        }
    }

    private var topBar: some View {
        HStack(spacing: 0) {
            Button(action: capture.plusTapped) {
                Image(systemName: capture.isExpanded ? "xmark" : "plus")
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: NotchPanel.earWidth, height: capture.barHeight)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(capture.isExpanded ? "Close" : "Save a link or note")
            .accessibilityLabel(capture.isExpanded ? "Close" : "Save a link or note")

            Spacer(minLength: capture.notchWidth)

            Circle()
                .fill(dotColor)
                .frame(width: 8, height: 8)
                .frame(width: NotchPanel.earWidth, height: capture.barHeight)
                .animation(.easeInOut(duration: 0.25), value: monitor.status)
                .help(dotLabel)
                .accessibilityLabel(dotLabel)
        }
        .frame(height: capture.barHeight)
    }

    private var dotColor: Color {
        switch monitor.status {
        case .unknown: .gray
        case .up: .green
        case .down: .red
        }
    }

    private var dotLabel: String {
        switch monitor.status {
        case .unknown: "Checking backend…"
        case .up: "Backend is running"
        case .down: "Backend is not answering"
        }
    }
}

/// The open part of the panel. One layout per phase.
private struct CaptureArea: View {
    @Bindable var capture: CaptureModel
    @FocusState private var noteFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch capture.phase {
            case .link(let url):
                Text("Link on your clipboard").font(.caption).foregroundStyle(.secondary)
                Text(url.absoluteString)
                    .font(.system(size: 13, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack {
                    Button("Write a note instead", action: capture.writeNoteInstead)
                        .buttonStyle(.link)
                    Spacer()
                    Button("Save link") { Task { await capture.save() } }
                        .keyboardShortcut(.defaultAction)  // Return
                }

            case .note, .idle:
                TextField("Write a note, then press Return", text: $capture.noteText, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .lineLimit(1...3)
                    .focused($noteFocused)
                    .onSubmit { Task { await capture.save() } }
                    .onAppear { noteFocused = true }
                Text("Return saves · Esc closes").font(.caption).foregroundStyle(.secondary)

            case .saving:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Saving…")
                }

            case .saved:
                Label("Saved", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .font(.system(size: 14, weight: .medium))

            case .alreadySaved:
                Label("Already saved", systemImage: "checkmark.circle")
                    .foregroundStyle(.secondary)
                    .font(.system(size: 14, weight: .medium))

            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.system(size: 13))
                    .lineLimit(2)
                HStack {
                    Spacer()
                    Button("Try again", action: capture.retry)
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
    }
}
