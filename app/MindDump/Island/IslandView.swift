import SwiftUI

/// The island: one true-black body that hangs from the notch, and the content inside it.
///
/// The window never moves or resizes. Only this view's body changes size, and each of its
/// dimensions follows its own spring (see `IslandMotion`). That is the difference from
/// animating a window frame: here SwiftUI drives every frame at the display's refresh rate
/// and can retarget a spring mid-flight without a jump.
struct IslandView: View {
    let island: IslandModel
    let panel: PanelModel
    let monitor: HealthMonitor

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// True for a moment after launch, while the content is built once out of sight.
    @State private var rehearsing = true
    /// Ask mode with a finished answer, for the rehearsal only.
    @State private var rehearsalPanel: PanelModel?

    var body: some View {
        let metrics = island.metrics
        let motion = island.motion

        IslandBody(corner: metrics.corner)
            .fill(.black)  // true black: on the hardware notch it disappears
            .animation(motion.corner, value: metrics.corner)
            .overlay(alignment: .top) { content }
            .clipShape(IslandBody(corner: metrics.corner))
            .animation(motion.corner, value: metrics.corner)
            .overlay { keyline(metrics: metrics, motion: motion) }
            .background { shoulders(metrics: metrics, motion: motion) }
            // Width and height each get their own spring. `animation(_:body:)` scopes a
            // spring to exactly the modifier inside the braces.
            .animation(motion.edges) { $0.frame(width: metrics.width) }
            .animation(motion.body) { $0.frame(height: metrics.height) }
            .scaleEffect(island.isPressed ? 1.035 : 1, anchor: .top)
            .animation(IslandMotion.spring(0.36, 0.62), value: island.isPressed)
            .shadow(color: .black.opacity(0.36 * metrics.shadow), radius: 15, y: 10)
            .shadow(color: .black.opacity(0.10 * metrics.shadow), radius: 40, y: 23)
            .animation(motion.shadow, value: metrics.shadow)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(alignment: .top) { rehearsal }
            .onAppear { if rehearsing { rehearsalPanel = PanelModel.rehearsal(island: island) } }
            .foregroundStyle(.white)
            .environment(\.colorScheme, .dark)  // controls drawn for a black surface
            .onExitCommand { island.close() }  // Escape
            .onChange(of: reduceMotion, initial: true) { _, on in island.reduceMotion = on || Self.forceReduceMotion }
            .contextMenu {
                // No Dock icon or menu bar, so right-click is the way to quit.
                Button("Quit Mind Dump") { NSApp.terminate(nil) }
            }
    }

    /// Debug builds: `MD_REDUCE_MOTION=1` acts as if Reduce Motion were on, to check that
    /// path without changing a system setting.
    private static var forceReduceMotion: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["MD_REDUCE_MOTION"] == "1"
        #else
        false
        #endif
    }

    // MARK: Content

    /// One layer per presentation. Layers overlap while they cross over (the old one blurs
    /// out as the new one comes in), so the body never shows an empty black frame.
    @ViewBuilder private var content: some View {
        let transition = IslandContentTransition(motion: island.motion)
        ZStack(alignment: .top) {
            switch island.presentation {
            case .idle:
                Color.clear.frame(height: 1).transition(transition)
            case .peek:
                PeekContent(island: island, monitor: monitor).transition(transition)
            case .compact(let activity):
                CompactContent(island: island, activity: activity)
                    .id(activity.id)
                    .transition(transition)
            case .panel:
                EmptyView()
            }
            // The panel is never removed, only hidden. Rebuilding it on every open cost
            // up to 70 ms with a long answer in it (every line laid out again), which
            // delayed the open. Hidden, it costs nothing; shown, it is already built.
            PanelView(island: island, panel: panel, monitor: monitor)
                .modifier(PanelPresence(shown: island.presentation.isPanel, motion: island.motion))
        }
        // Content is laid out at its final size from the first frame; the growing body
        // reveals it. Text never reflows while the shape is moving.
        .frame(width: contentWidth, alignment: .top)
        .fixedSize(horizontal: true, vertical: true)
        // A click anywhere on a closed island opens it. As a real Button it is also
        // reachable by keyboard and VoiceOver.
        .overlay {
            if !island.presentation.isPanel {
                Button(action: island.open) { Color.clear.contentShape(Rectangle()) }
                    .buttonStyle(IslandPressStyle(island: island))
                    .accessibilityLabel("Open Mind Dump")
                    .help("Save a note or link")
            }
        }
    }

    private var contentWidth: CGFloat {
        switch island.presentation {
        case .panel: IslandLayout.panelWidth
        case .idle: island.layout.notch.width
        case .peek: island.layout.peek.width
        case .compact: island.layout.notch.width + 2 * IslandLayout.maxWing
        }
    }

    /// The first time SwiftUI builds a view it creates the text field, loads fonts and
    /// symbols and lays everything out: about 27 ms here, which is three dropped frames
    /// at 120 Hz in the middle of the first open. The panel itself is now built at launch
    /// and kept (see `content`). What is rehearsed here, built once out of sight and
    /// thrown away, is everything the panel does not show yet: a pill, and Ask mode with
    /// a finished answer in it (styled lines with references, the spinner line, the
    /// notices, an open source row). So the first switch to Ask and the first answer
    /// build nothing new while they animate.
    @ViewBuilder private var rehearsal: some View {
        if rehearsing {
            VStack {
                if let rehearsalPanel {
                    PanelView(island: island, panel: rehearsalPanel, monitor: monitor, isRehearsal: true)
                }
                StatusLine(status: .searching("rehearsal"))
                Notice(glyph: "circle.dashed", text: "Not found in your items.", strong: true)
                Notice(glyph: "ellipsis.circle", text: "May be incomplete.", strong: false)
                CompactContent(
                    island: island,
                    activity: IslandActivity(id: "rehearsal", glyph: .spinner, text: "Reading page"),
                    reportsSize: false
                )
                ActivityGlyph(glyph: .check, tint: .white)
                ActivityGlyph(glyph: .warning, tint: .white)
            }
            .opacity(0)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .environment(\.isRehearsal, true)
            .task {
                try? await Task.sleep(for: .seconds(1))
                rehearsing = false
                rehearsalPanel = nil
            }
        }
    }

    // MARK: Chrome

    /// A faint line along the sides and bottom, so the black body reads against a dark
    /// window. It fades to nothing at the top, where the body meets the screen edge.
    private func keyline(metrics: IslandMetrics, motion: IslandMotion) -> some View {
        IslandBody(corner: metrics.corner)
            .strokeBorder(.white.opacity(0.13 * metrics.keyline), lineWidth: 1)
            .animation(motion.corner, value: metrics.corner)
            .animation(motion.shadow, value: metrics.keyline)
            .mask {
                LinearGradient(
                    stops: [.init(color: .clear, location: 0), .init(color: .black, location: 1)],
                    startPoint: .top, endPoint: .init(x: 0.5, y: 0.45)
                )
            }
            .allowsHitTesting(false)
    }

    /// The two concave curves that flare the body into the top edge of the screen.
    private func shoulders(metrics: IslandMetrics, motion: IslandMotion) -> some View {
        let radius = metrics.shoulder
        return HStack(spacing: 0) {
            IslandShoulder(leading: true).fill(.black)
                .frame(width: radius, height: radius)
                .offset(x: -radius + 0.5)  // half a point of overlap: no hairline seam
            Spacer(minLength: 0)
            IslandShoulder(leading: false).fill(.black)
                .frame(width: radius, height: radius)
                .offset(x: radius - 0.5)
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .animation(motion.corner, value: radius)
        .allowsHitTesting(false)
    }
}

/// The body swells slightly while the pointer is held down, then springs back.
private struct IslandPressStyle: ButtonStyle {
    let island: IslandModel

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .onChange(of: configuration.isPressed) { _, pressed in island.isPressed = pressed }
    }
}

/// The panel's version of `IslandContentTransition`, for a view that stays in place:
/// the same blur, scale and fade, driven by "shown" instead of by insertion and removal.
/// Hidden, it takes no clicks and is invisible to VoiceOver.
private struct PanelPresence: ViewModifier {
    let shown: Bool
    let motion: IslandMotion

    func body(content: Content) -> some View {
        content
            .blur(radius: shown ? 0 : motion.blurOut)
            .scaleEffect(shown ? 1 : motion.contentScale, anchor: .top)
            .opacity(shown ? 1 : 0)
            .animation(shown ? motion.contentIn : motion.contentOut, value: shown)
            .allowsHitTesting(shown)
            .accessibilityHidden(!shown)
    }
}

/// How content enters and leaves the island: it blurs, shrinks a little and fades.
/// Leaving is fast and starts at once; arriving waits for the shape (`IslandMotion`).
struct IslandContentTransition: Transition {
    let motion: IslandMotion

    func body(content: Content, phase: TransitionPhase) -> some View {
        let blur: CGFloat =
            switch phase {
            case .willAppear: motion.blurIn
            case .identity: 0
            case .didDisappear: motion.blurOut
            }
        content
            .blur(radius: blur)
            .scaleEffect(phase.isIdentity ? 1 : motion.contentScale, anchor: .top)
            .opacity(phase.isIdentity ? 1 : 0)
            .animation(phase.isIdentity ? motion.contentIn : motion.contentOut, value: phase)
    }
}

// MARK: - Peek

/// Pointer over the notch: a + in the left wing and the backend's status dot in the right.
private struct PeekContent: View {
    let island: IslandModel
    let monitor: HealthMonitor

    var body: some View {
        HStack(spacing: 0) {
            Image(systemName: "plus")
                .font(.system(size: 12, weight: .semibold))
                .frame(width: IslandLayout.peekWing)
            Spacer(minLength: island.layout.notch.width)
            HealthDot(monitor: monitor)
                .frame(width: IslandLayout.peekWing)
        }
        .frame(height: island.layout.notch.height)
    }
}

/// Green when the backend answers, red when it does not, grey until the first answer.
struct HealthDot: View {
    let monitor: HealthMonitor

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 7, height: 7)
            .animation(.easeInOut(duration: 0.25), value: monitor.status)
            .help(label)
            .accessibilityLabel(label)
    }

    private var color: Color {
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

// MARK: - Compact pill

/// A status pill: glyph left of the notch, a few words right of it.
private struct CompactContent: View {
    let island: IslandModel
    let activity: IslandActivity
    var reportsSize = true

    @State private var leadingWidth: CGFloat = 0
    @State private var trailingWidth: CGFloat = 0

    var body: some View {
        HStack(spacing: 0) {
            ActivityGlyph(glyph: activity.glyph, tint: activity.tint)
                .padding(.leading, 13)
                .padding(.trailing, 8)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { leadingWidth = $0 }
                .frame(maxWidth: .infinity, alignment: .leading)

            Color.clear.frame(width: island.layout.notch.width)

            Text(activity.text)
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundStyle(activity.tint)
                .lineLimit(1)
                .contentTransition(.interpolate)
                .padding(.leading, 8)
                .padding(.trailing, 13)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { trailingWidth = $0 }
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        // Laid out at the pill's final width from the start; the body widens over it.
        .frame(width: pillWidth)
        .frame(height: island.layout.notch.height)
        .frame(maxWidth: .infinity)
        .onChange(of: leadingWidth + trailingWidth, initial: true) {
            guard reportsSize else { return }
            island.compactWingsMeasured(leading: leadingWidth, trailing: trailingWidth)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(activity.text)
    }

    /// The pill's final width: the notch plus two equal wings.
    private var pillWidth: CGFloat {
        island.layout.compact(leading: leadingWidth, trailing: trailingWidth).width
    }
}

private struct ActivityGlyph: View {
    let glyph: IslandActivity.Glyph
    let tint: Color

    var body: some View {
        Group {
            switch glyph {
            case .spinner:
                SpinnerRing().frame(width: 13, height: 13)
            case .check:
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            case .warning:
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            case .symbol(let name):
                Image(systemName: name).foregroundStyle(tint)
            }
        }
        .font(.system(size: 13, weight: .semibold))
        .frame(height: 16)
        .transition(.scale(scale: 0.6).combined(with: .opacity))
        .animation(IslandMotion.spring(0.32, 0.7), value: glyph)
    }
}

/// A thin ring that turns. Drawn here, not a system ProgressView, so its weight and
/// speed match the rest of the island.
struct SpinnerRing: View {
    @State private var turning = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Circle()
            .trim(from: 0.08, to: 0.78)
            .stroke(.white.opacity(0.9), style: StrokeStyle(lineWidth: 1.8, lineCap: .round))
            .rotationEffect(.degrees(turning ? 360 : 0))
            .animation(
                reduceMotion ? nil : .linear(duration: 0.9).repeatForever(autoreverses: false),
                value: turning
            )
            .onAppear { turning = true }
            .accessibilityLabel("Working")
    }
}
