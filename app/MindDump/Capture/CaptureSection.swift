import SwiftUI

/// Capture's words at the end of the line (see `CapsuleView`): "Save note" or "Save
/// link" (the address is already in the box). Nothing while the box is empty. They float
/// over the empty end of the box and fade out once the text comes near them, so they
/// never cost the text its room.
struct CaptureWords: View {
    let capture: CaptureModel

    var body: some View {
        Group {
            switch capture.kind {
            case .empty: EmptyView()
            case .note: Text("Save note")
            case .link: Text("Save link")
            }
        }
        .font(.system(size: 12))
        .foregroundStyle(.white.opacity(CapsuleStyle.secondary))
        .fixedSize()
        .frame(height: 20)
        .contentTransition(.opacity)
        .animation(.easeOut(duration: 0.15), value: capture.kind)
        .accessibilityHidden(true)  // said by `CaptureKey`
    }
}

/// Capture's ⏎ key cap, in the slot at the end of the line: Return saves. With
/// `showsGlyph` (the words have given way to the text) a note or link glyph sits beside
/// it, so the line always says which it is. It says the whole thing to VoiceOver.
struct CaptureKey: View {
    let capture: CaptureModel
    var showsGlyph = false

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: glyph)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.white.opacity(CapsuleStyle.secondary))
                .frame(width: 16)
                .opacity(showsGlyph ? 1 : 0)
                .animation(.easeOut(duration: 0.12 * IslandMotion.timeScale), value: showsGlyph)
            KeyCap()
        }
        .opacity(capture.kind == .empty ? 0 : 1)
        .animation(.easeOut(duration: 0.15), value: capture.kind == .empty)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityHidden(capture.kind == .empty)
    }

    /// The same glyphs the sources list uses for a note and a link.
    private var glyph: String {
        if case .link = capture.kind { "link" } else { "note.text" }
    }

    private var accessibilityText: String {
        switch capture.kind {
        case .empty: ""
        case .note: "Return saves this note"
        case .link(let url): "Return saves this link: \(url.host() ?? url.absoluteString)"
        }
    }
}

/// The Return key, drawn as a small flat key.
private struct KeyCap: View {
    var body: some View {
        Text("⏎")
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.white.opacity(0.85))
            .frame(minWidth: 20, minHeight: 20)
            .padding(.horizontal, 1)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(.white.opacity(CapsuleStyle.controlFill)))
            .accessibilityLabel("Return")
    }
}

/// Why a save failed, on a second line of the capsule, under the words (which stay in
/// the box, so Return retries). Nothing at all when there is no error.
struct CaptureError: View {
    let capture: CaptureModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let message = capture.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(CapsuleStyle.warning)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    // Under the box's text: past the mode button and its gap.
                    .padding(.leading, 58)
                    .padding(.trailing, 20)
                    .padding(.top, -6)
                    .padding(.bottom, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension CapsuleStyle {
    /// Errors: a warm orange light enough to read on the dark glass.
    static let warning = Color(red: 1, green: 0.66, blue: 0.32)
}
