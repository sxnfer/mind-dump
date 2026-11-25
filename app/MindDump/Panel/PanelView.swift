import AppKit
import SwiftUI

/// The open island: the mode control and the backend's status beside the notch, one box
/// below, and under it whatever the mode shows (Capture's hint, or Ask's answer).
///
/// The box is shared by both modes on purpose. It is the same text field before and
/// after a switch, so it keeps the keyboard focus and never moves; only its prompt and
/// what sits below it change.
struct PanelView: View {
    let island: IslandModel
    let panel: PanelModel
    let monitor: HealthMonitor
    /// Built once, invisibly, at launch (see `IslandView.rehearsal`). A rehearsal copy
    /// never reads the clipboard, never takes the keyboard, and never sizes the island.
    var isRehearsal = false

    @FocusState private var boxFocused: Bool
    /// Each mode's content height. The stack below the box takes the height of the
    /// current mode only, so on a switch the body heads for its new size at once instead
    /// of waiting for the old content to finish fading out.
    @State private var modeHeights: [PanelMode: CGFloat] = [:]
    @State private var boxHeight: CGFloat = 20

    private var capture: CaptureModel { panel.capture }
    private var ask: AskModel { panel.ask }

    var body: some View {
        VStack(spacing: 0) {
            band
            VStack(alignment: .leading, spacing: 10) {
                box
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { boxHeight = $0 }
                modeContent
            }
            .padding(.horizontal, IslandLayout.panelPadding)
            .padding(.top, 10)
            .padding(.bottom, IslandLayout.panelPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            // The body follows the content's height with a spring (`IslandMotion.resize`).
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                if !isRehearsal || panel.mode == .capture { island.panelContentMeasured(height) }
            }
        }
        .frame(width: IslandLayout.panelWidth)
        // Children move as one block while the body morphs, not each on its own.
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
        // The panel stays built while the island is closed (see `IslandView.content`),
        // so opening and closing are changes of `isPanel`, not appear and disappear.
        .onChange(of: island.presentation.isPanel) { _, open in
            guard !isRehearsal else { return }
            if open {
                capture.panelOpened()
                focusBox()
            } else {
                capture.panelClosed()
            }
        }
    }

    // MARK: Band

    /// Level with the menu bar: the mode control left of the notch, the status dot right.
    private var band: some View {
        HStack(spacing: 0) {
            ModeControl(panel: panel)
                .padding(.leading, 12)
                .frame(maxWidth: .infinity, alignment: .leading)

            Color.clear.frame(width: island.layout.notch.width)

            HealthDot(monitor: monitor)
                .padding(.trailing, IslandLayout.panelPadding)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .frame(height: island.layout.notch.height)
    }

    // MARK: Box

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
            // mode changes. (The system placeholder would swap in one frame, and its grey
            // is invisible on true black.)
            .background(alignment: .topLeading) {
                if text.wrappedValue.isEmpty {
                    Text(prompt)
                        .font(.system(size: 16))
                        .foregroundStyle(.white.opacity(0.34))
                        .lineLimit(1)
                        .id(prompt)
                        .transition(.opacity.animation(.easeInOut(duration: 0.18 * IslandMotion.timeScale)))
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .accessibilityLabel(panel.mode == .capture ? "Note or link" : "Question")
    }

    private func submit() {
        // Return in a hidden panel does nothing, in either mode (see `AskModel.submit`).
        guard island.presentation.isPanel else { return }
        switch panel.mode {
        case .capture: Task { await capture.submit() }
        case .ask: ask.submit()
        }
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

    // MARK: Mode content

    /// The largest the Ask content may be before its answer scrolls inside: the panel's
    /// maximum, less the notch band, the box and the padding around them.
    private var askRoom: CGFloat {
        IslandLayout.maxPanelHeight - island.layout.notch.height - 10 - boxHeight - 10 - IslandLayout.panelPadding
    }

    private var modeContent: some View {
        let transition = IslandContentTransition(motion: island.motion)
        return ZStack(alignment: .topLeading) {
            switch panel.mode {
            case .capture:
                CaptureSection(capture: capture)
                    .fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { modeHeights[.capture] = $0 }
                    .transition(transition)
            case .ask:
                AskSection(ask: ask, island: island, room: askRoom, isRehearsal: isRehearsal)
                    .fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { modeHeights[.ask] = $0 }
                    .transition(transition)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(height: modeHeights[panel.mode], alignment: .top)
    }
}

/// Capture and Ask, side by side, with a soft pill behind the active one that slides
/// across on a spring. One pill moved by `matchedGeometryEffect`, not two that fade:
/// your eye follows a thing that travels.
private struct ModeControl: View {
    let panel: PanelModel
    @Namespace private var space

    var body: some View {
        HStack(spacing: 2) {
            ForEach(PanelMode.allCases) { mode in
                let active = panel.mode == mode
                Button {
                    panel.select(mode)
                } label: {
                    Text(mode.title)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white.opacity(active ? 0.92 : 0.42))
                        .padding(.horizontal, 9)
                        .frame(height: 22)
                        .background {
                            if active {
                                Capsule()
                                    .fill(.white.opacity(0.13))
                                    .matchedGeometryEffect(id: "indicator", in: space)
                            }
                        }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .focusable(false)  // the box keeps the keyboard
                .debugTarget("mode-\(mode.rawValue)")
                .accessibilityLabel("\(mode.title) mode")
                .accessibilityAddTraits(active ? .isSelected : [])
                .help(mode == .capture ? "Save a note or link (Tab)" : "Ask your items (Tab)")
            }
        }
    }
}
