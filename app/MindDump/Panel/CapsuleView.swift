import AppKit
import SwiftUI

/// The open capsule: one line with the mode button, the box, and what Return will do;
/// under it, Capture's error line or Ask's answer card.
///
/// The box is shared by both modes on purpose. It is the same text field before and
/// after a switch, so it keeps the keyboard focus and never moves; only its prompt, the
/// button's glyph and what sits beside and below it change.
struct CapsuleView: View {
    let island: IslandModel
    let panel: PanelModel
    /// Built once, invisibly, at launch (see `IslandView.rehearsal`). A rehearsal copy
    /// never takes the keyboard and never sizes the capsule.
    var isRehearsal = false

    @FocusState private var boxFocused: Bool
    /// Each mode's height below the line. The stack takes the height of the current mode
    /// only, so on a switch the capsule heads for its new size at once instead of waiting
    /// for the old content to finish fading out.
    @State private var belowHeights: [PanelMode: CGFloat] = [:]
    @State private var rowHeight: CGFloat = IslandLayout.capsuleHeight
    /// Capture's text on one line, and its words' width: measured, so the words can give
    /// way before the text reaches them.
    @State private var textWidth: CGFloat = 0
    @State private var wordsWidth: CGFloat = 0

    private var capture: CaptureModel { panel.capture }
    private var ask: AskModel { panel.ask }
    /// Ask shows a card under the line (a question, or an error).
    private var hasCard: Bool { ask.question != nil || ask.errorMessage != nil }

    var body: some View {
        VStack(spacing: 0) {
            row
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { rowHeight = $0 }
            below
        }
        .frame(width: IslandLayout.capsuleWidth)
        // The capsule follows the content's height with a spring (`IslandMotion.resize`).
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
            if !isRehearsal { island.capsuleContentMeasured(height) }
        }
        // Children move as one block while the capsule morphs, not each on its own.
        .geometryGroup()
        .animation(IslandMotion.spring(0.3, 1), value: capture.errorMessage)
        .onChange(of: ask.focusRequests) { _, _ in
            guard !isRehearsal else { return }
            // Already typing into the box (AppKit's field editor has the keyboard):
            // leave it alone, or the next keystroke can be lost in the hand-over.
            if (NSApp.keyWindow?.firstResponder as? NSTextView)?.isFieldEditor == true { return }
            // SwiftUI may still believe the box has focus after AppKit gave the keyboard
            // to the selected text, and setting `true` again would then do nothing. So
            // let go first, then take it back on the next turn.
            boxFocused = false
            DispatchQueue.main.async { boxFocused = true }
        }
        .task { await warmFocus() }
        // The content stays built while the capsule is closed (see `IslandView`), so
        // opening and closing are changes of `isOpen`, not appear and disappear.
        .onChange(of: island.presentation.isOpen) { _, open in
            guard !isRehearsal else { return }
            if open {
                capture.panelOpened()
                focusBox()
            } else {
                capture.panelClosed()
            }
        }
    }

    // MARK: The line

    /// Aligned to the top, so with a long note in the box (up to four lines) the buttons
    /// stay level with its first line.
    ///
    /// The box has one width, always: the line less the mode button, the end slot and
    /// two 12-point gaps (398 points). Its text editor kept wrapping at an old width when
    /// the box changed width under it (measured: a 324-point box whose text still wrapped
    /// at 267), which made text wrap early. So nothing that changes (the mode, Capture's
    /// words) changes the box's width.
    private var row: some View {
        HStack(alignment: .top, spacing: 12) {
            ModeButton(panel: panel)
                .padding(.top, 8)
            box
                .padding(.vertical, 17)
                .frame(width: Self.boxWidth, alignment: .leading)
                // Capture's words float over the end of the box, left of the key cap.
                .overlay(alignment: .topTrailing) { captureWords }
            endSlot
        }
        .padding(.horizontal, 8)
        .frame(minHeight: IslandLayout.capsuleHeight)
    }

    /// The capsule less 8 + 8 padding, the 38-point button, the end slot and two 12s.
    private static let boxWidth: CGFloat = IslandLayout.capsuleWidth - 16 - 38 - endSlotWidth - 24

    /// Room for Ask's 38-point send button, or for Capture's key cap with the note or
    /// link glyph beside it.
    private static let endSlotWidth: CGFloat = 44

    private var text: Binding<String> {
        switch panel.mode {
        case .capture: Binding(get: { capture.text }, set: { capture.text = $0 })
        case .ask: Binding(get: { ask.draft }, set: { ask.draft = $0 })
        }
    }

    private var prompt: String {
        switch panel.mode {
        case .capture: "Type a note, or paste a link"
        case .ask: ask.conversationID == nil ? "Ask your items" : "Ask a follow-up"
        }
    }

    private var box: some View {
        TextField("", text: text, axis: .vertical)
            .textFieldStyle(.plain)
            .font(.system(size: 16))
            .lineLimit(1...4)
            .focused($boxFocused)
            .onSubmit(submit)
            .disabled((panel.mode == .capture && capture.isSaving) || isRehearsal)
            // The prompt is drawn here, not by the field, so it can cross-fade when the
            // mode changes. (The system placeholder would swap in one frame.)
            .background(alignment: .topLeading) {
                if text.wrappedValue.isEmpty {
                    Text(prompt)
                        .font(.system(size: 16))
                        .foregroundStyle(.white.opacity(CapsuleStyle.secondary))
                        .lineLimit(1)
                        .id(prompt)
                        .transition(.opacity.animation(.easeInOut(duration: 0.18 * IslandMotion.timeScale)))
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            // Capture's text as one line, measured out of sight (see `wordsGiveWay`).
            .background(alignment: .topLeading) {
                Text(capture.text)
                    .font(.system(size: 16))
                    .fixedSize()
                    .hidden()
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { textWidth = $0 }
                    .accessibilityHidden(true)
            }
            .debugTarget("box")
            .accessibilityLabel(panel.mode == .capture ? "Note or link" : "Question")
    }

    /// The slot at the end of the line: Capture's ⏎ key cap (with the note or link glyph
    /// once the words have given way), or Ask's send button. Both are always built and
    /// only one shows: inserting the other on a mode flip cost a late frame.
    private var endSlot: some View {
        ZStack(alignment: .topTrailing) {
            CaptureKey(capture: capture, showsGlyph: wordsGiveWay)
                .padding(.top, 17)
                .padding(.trailing, 2)
                .frame(maxHeight: .infinity, alignment: .top)
                .modifier(Shown(shown: panel.mode == .capture))
            SendButton(ask: ask, action: submit)
                .padding(.top, 8)
                .frame(maxHeight: .infinity, alignment: .top)
                .modifier(Shown(shown: panel.mode == .ask))
        }
        .frame(width: Self.endSlotWidth, alignment: .trailing)
    }

    /// "Save note" or "Save link": shown while the text leaves room for them, faded out
    /// once it comes near, so the text runs on towards the key cap before it wraps. Then
    /// a small note or link glyph beside the key cap says which it is, so there is always
    /// a sign while there is text.
    private var captureWords: some View {
        let shown = panel.mode == .capture && !wordsGiveWay
        return CaptureWords(capture: capture)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { wordsWidth = $0 }
            .padding(.top, 17)
            .opacity(shown ? 1 : 0)
            .animation(.easeOut(duration: 0.12 * IslandMotion.timeScale), value: shown)
            .allowsHitTesting(false)
    }

    /// The typed line would come within 12 points of the words.
    private var wordsGiveWay: Bool {
        textWidth > Self.boxWidth - wordsWidth - 12
    }

    private func submit() {
        // Return in a hidden capsule does nothing, in either mode (see `AskModel.submit`).
        guard island.presentation.isOpen else { return }
        switch panel.mode {
        case .capture: Task { await capture.submit() }
        case .ask: ask.submit()
        }
    }

    /// AppKit sets up text editing the first time any field takes focus: 12 ms in slice
    /// 1, and 55 to 58 ms here inside the glass (measured: one stall at +240 ms into the
    /// first open). So the box takes the focus once at launch, out of sight, while the
    /// capsule is closed: the window is not key, so no keys reach it, and the box keeps
    /// that focus from then on (see `DropCatcher` for why that is safe for drops). The
    /// first open then has nothing left to set up.
    private func warmFocus() async {
        guard !isRehearsal else { return }
        try? await Task.sleep(for: .seconds(1.5))  // after the launch rehearsal
        guard !Self.hasFocusedOnce, !island.presentation.isOpen else { return }
        Self.hasFocusedOnce = true
        boxFocused = true
    }

    /// Opening focuses the box. If it opens in the first moments after launch, before
    /// `warmFocus`, wait until the spring's fast part is over, where the stall cannot be
    /// seen. Every later open focuses at once.
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

    // MARK: Below the line

    /// The largest Ask's card may be before its answer scrolls inside: the card's maximum,
    /// less the line and the padding at the bottom of the card.
    private var askRoom: CGFloat {
        IslandLayout.maxCardHeight - rowHeight - AskSection.bottomPadding
    }

    /// Both modes' parts stay built, laid over each other, and only the current one
    /// shows: rebuilding Ask's (with its scroll view) on every flip cost frames. The stack
    /// takes the current mode's height only.
    private var below: some View {
        ZStack(alignment: .topLeading) {
            CaptureError(capture: capture)
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { belowHeights[.capture] = $0 }
                .modifier(Shown(shown: panel.mode == .capture))
            AskSection(ask: ask, island: island, room: askRoom, isRehearsal: isRehearsal)
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { belowHeights[.ask] = $0 }
                .modifier(Shown(shown: panel.mode == .ask))
                // With no card it is empty: no fade. Fading its (empty) scroll view inside
                // the glass still cost one to four late frames per flip.
                .animation(hasCard ? IslandMotion.spring(0.36, 0.82) : nil, value: panel.mode)
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(height: belowHeights[panel.mode] ?? 0, alignment: .top)
    }
}

// MARK: - Controls

/// One of two views laid over each other (a mode's control or its part below the line):
/// the hidden one fades and shrinks a little, takes no clicks and is hidden from
/// VoiceOver. No blur: blurring text inside the glass cost frames on every flip.
private struct Shown: ViewModifier {
    let shown: Bool

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .scaleEffect(shown ? 1 : 0.94, anchor: .top)
            .allowsHitTesting(shown)
            .accessibilityHidden(!shown)
    }
}

/// The round button at the start of the line: it shows the mode (a plus for Capture, a
/// speech bubble for Ask) and flips it, as Tab does. A flat fill, not glass: glass on
/// glass muddies both.
private struct ModeButton: View {
    let panel: PanelModel

    var body: some View {
        let capture = panel.mode == .capture
        Button(action: panel.toggleMode) {
            // Two glyphs that cross-fade and turn, not one symbol with a replace effect:
            // inside the glass, the replace effect cost 5 to 13 late frames per flip.
            ZStack {
                Image(systemName: "plus")
                    .opacity(capture ? 1 : 0)
                    .rotationEffect(.degrees(capture ? 0 : -90))
                    .scaleEffect(capture ? 1 : 0.6)
                Image(systemName: "bubble.left")
                    .opacity(capture ? 0 : 1)
                    .rotationEffect(.degrees(capture ? 90 : 0))
                    .scaleEffect(capture ? 0.6 : 1)
            }
                .font(.system(size: 15, weight: .semibold))
                .frame(width: 38, height: 38)
                .background(Circle().fill(.white.opacity(CapsuleStyle.controlFill)))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .focusable(false)  // the box keeps the keyboard
        .debugTarget("mode")
        .accessibilityLabel("\(panel.mode.title) mode")
        .accessibilityHint(capture ? "Switches to Ask (Tab)" : "Switches to Capture (Tab)")
        .help(capture ? "Capture: save a note or link. Switch to Ask (Tab)" : "Ask your items. Switch to Capture (Tab)")
    }
}

/// Ask's send button: the one place the accent colour fills a control.
private struct SendButton: View {
    let ask: AskModel
    let action: () -> Void

    var body: some View {
        let ready = !ask.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !ask.isBusy
        Button(action: action) {
            Image(systemName: "arrow.up")
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(Circle().fill(CapsuleStyle.accent))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .focusable(false)
        .disabled(!ready)
        .opacity(ready ? 1 : 0.45)
        .animation(.easeOut(duration: 0.15), value: ready)
        .debugTarget("send")
        .accessibilityLabel("Ask")
        .help(ask.isBusy ? "Answering · Esc stops" : "Ask (Return)")
    }
}

// MARK: - Look

/// The capsule's few fixed colours.
enum CapsuleStyle {
    /// The one accent: the send button and the drop target's rim. Close to #1F6FEB, which
    /// holds 4.6:1 against white glyphs.
    static let accent = Color(red: 0x1F / 255, green: 0x6F / 255, blue: 0xEB / 255)
    /// Secondary text on the dark glass (the prompt, "Save note", the question).
    static let secondary = 0.72
    /// Flat fills for controls inside the capsule.
    static let controlFill = 0.14
}

/// The capsule's surface: Liquid Glass, kept dark.
///
/// Regular glass adapts to what is behind it, and over a bright window it turns light,
/// which would leave white text unreadable. A dark tint keeps it a dark lens in both
/// system appearances, so the white text matches the black notch. With Reduce
/// Transparency on, it is a solid dark shape instead.
struct CapsuleSurface: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    /// Is the window key? Glass in a window that is not (the drop target, the moment of
    /// closing) draws lighter: measured over the busy backdrop, the background behind the
    /// text went from about 80 to about 135 grey, which is too light for white text.
    @Environment(\.appearsActive) private var appearsActive

    func body(content: Content) -> some View {
        if reduceTransparency || Self.forceSolid {
            content.background {
                CapsuleShape()
                    .fill(Color(white: 0.11))
                    .overlay { CapsuleShape().strokeBorder(.white.opacity(0.12), lineWidth: 1) }
            }
        } else {
            content
                // Under the content, inside the glass: a shade that makes up the difference
                // while the window is not key.
                .background {
                    CapsuleShape()
                        .fill(.black.opacity(appearsActive ? 0 : CapsuleStyle.inactiveShade))
                        .animation(.easeOut(duration: 0.2 * IslandMotion.timeScale), value: appearsActive)
                }
                .glassEffect(.regular.tint(.black.opacity(CapsuleStyle.glassDim)), in: CapsuleShape())
        }
    }

    /// Debug builds: `MD_REDUCE_TRANSPARENCY=1` acts as if Reduce Transparency were on.
    private static var forceSolid: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["MD_REDUCE_TRANSPARENCY"] == "1"
        #else
        false
        #endif
    }
}

extension CapsuleStyle {
    /// How dark the glass's tint is. Enough that white text reads over a white window.
    static let glassDim = 0.6
    /// The extra shade while the window is not key (see `CapsuleSurface`).
    static let inactiveShade = 0.5
}
