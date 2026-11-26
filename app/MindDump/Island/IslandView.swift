import SwiftUI

/// The island: one true-black body that hangs from the notch, and under it the glass
/// capsule that is the box, the answer card and the drop target.
///
/// The window never moves or resizes. Only these two shapes change size inside it, and
/// each dimension follows its own spring (see `IslandMotion`). That is the difference from
/// animating a window frame: here SwiftUI drives every frame at the display's refresh rate
/// and can retarget a spring mid-flight without a jump.
///
/// Why two shapes: the notch is black hardware, so whatever touches it must be black
/// (glass there would have nothing behind it to bend). The glass sits apart, under it.
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
        ZStack(alignment: .top) {
            // Behind the body: closed, the capsule is tucked up inside the notch, where
            // the black body covers it.
            capsuleLayer
            blackBody
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(alignment: .top) { rehearsal }
        .onAppear { if rehearsing { rehearsalPanel = PanelModel.rehearsal(island: island) } }
        .foregroundStyle(.white)
        // White text on both: the black body, and the glass kept dark (`CapsuleSurface`).
        .environment(\.colorScheme, .dark)
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

    // MARK: Black body

    private var blackBody: some View {
        let metrics = island.metrics
        let motion = island.motion
        return IslandBody(corner: metrics.corner)
            .fill(.black)  // true black: on the hardware notch it disappears
            .animation(motion.corner, value: metrics.corner)
            .overlay(alignment: .top) { content }
            .clipShape(IslandBody(corner: metrics.corner))
            .animation(motion.corner, value: metrics.corner)
            .overlay { keyline(metrics: metrics, motion: motion) }
            .background { shoulders(metrics: metrics, motion: motion) }
            // Width and height each get their own spring. `animation(_:body:)` scopes a
            // spring to exactly the modifier inside the braces.
            .animation(motion.edges) { $0.modifier(IslandWidth(width: metrics.width)) }
            .animation(motion.body) { $0.modifier(IslandHeight(height: metrics.height)) }
            .scaleEffect(island.isPressed ? 1.035 : 1, anchor: .top)
            .animation(IslandMotion.spring(0.36, 0.62), value: island.isPressed)
            .shadow(color: .black.opacity(0.36 * metrics.shadow), radius: 15, y: 10)
            .shadow(color: .black.opacity(0.10 * metrics.shadow), radius: 40, y: 23)
            .animation(motion.shadow, value: metrics.shadow)
    }

    /// One layer per presentation. Layers overlap while they cross over (the old one blurs
    /// out as the new one comes in), so the body never shows an empty black frame.
    @ViewBuilder private var content: some View {
        let transition = IslandContentTransition(motion: island.motion)
        ZStack(alignment: .top) {
            switch island.presentation {
            case .idle, .open, .dropTarget:
                // The bare notch, at its full size so a click anywhere on it lands.
                Color.clear.frame(width: island.layout.notch.width, height: island.layout.notch.height)
                    .transition(transition)
            case .peek:
                PeekContent(island: island, monitor: monitor).transition(transition)
            case .compact(let activity):
                CompactContent(island: island, activity: activity)
                    .id(activity.id)
                    .transition(transition)
            }
        }
        // A pill that changes in place blurs once while its width moves (calm: never).
        .modifier(MorphBlur(pulse: island.morphPulse, peak: island.motion.morphBlur))
        // Content is laid out at its final size from the first frame; the growing body
        // reveals it. Text never reflows while the shape is moving.
        .frame(width: contentWidth, alignment: .top)
        .fixedSize(horizontal: true, vertical: true)
        // A click on the notch opens the capsule, and closes it again. As a real Button
        // it is also reachable by keyboard and VoiceOver.
        .overlay {
            Button(action: island.toggle) { Color.clear.contentShape(Rectangle()) }
                .buttonStyle(IslandPressStyle(island: island))
                .accessibilityLabel(island.presentation.isOpen ? "Close Mind Dump" : "Open Mind Dump")
                .help(island.presentation.isOpen ? "Close (⌥⇧Space)" : "Save a note or link (⌥⇧Space)")
        }
    }

    private var contentWidth: CGFloat {
        switch island.presentation {
        case .idle, .open, .dropTarget: island.layout.notch.width
        case .peek: island.layout.peek.width
        case .compact: island.layout.notch.width + 2 * IslandLayout.maxWing
        }
    }

    // MARK: Glass capsule

    /// The capsule: one glass shape whose width, height and place each follow a spring.
    /// What is inside it (the open box and card, or the drop target) is laid out at its
    /// own final size, and the growing shape reveals it, as the black body does.
    ///
    /// The open content is never removed, only hidden. Rebuilding it on every open cost
    /// up to 70 ms with a long answer in it (every line laid out again), which delayed
    /// the open. Hidden, it costs nothing; shown, it is already built.
    private var capsuleLayer: some View {
        let capsule = island.capsule
        // Same springs as the body; blur held to the glass's limit (see `glassBlurLimit`).
        let motion = island.motion.inGlass
        // Going back into the notch: the tuck springs, if this motion has them.
        let tucking = capsule.shown ? nil : motion.tuck
        let drop: DropTarget? = if case .dropTarget(let target) = island.presentation { target } else { nil }
        return GlassEffectContainer {
            Color.clear
                .overlay(alignment: .top) {
                    CapsuleView(island: island, panel: panel)
                        .modifier(CapsulePresence(shown: island.presentation.isOpen, motion: motion))
                        .fixedSize(horizontal: true, vertical: true)
                }
                .overlay(alignment: .top) {
                    ZStack {
                        if let drop {
                            DropTargetContent(drop: drop).transition(IslandContentTransition(motion: motion))
                        }
                    }
                }
                // The drop target's rim: says "here" in the one accent colour.
                .overlay {
                    CapsuleShape()
                        // A refused drag gets a quiet rim: "not here", without a colour.
                        .strokeBorder(drop?.isRefused == true ? Color.white.opacity(0.3) : CapsuleStyle.accent, lineWidth: 2)
                        .opacity(drop == nil ? 0 : 1)
                        .animation(drop == nil ? motion.contentOut : motion.contentIn, value: drop == nil)
                        .allowsHitTesting(false)
                }
                .clipShape(CapsuleShape())
                // On the content itself, not as a sibling: the container draws glass above
                // anything that is not the glass's own content (the rim included).
                .modifier(CapsuleSurface())
                .animation(tucking ?? motion.edges) { $0.modifier(IslandWidth(width: capsule.width)) }
                .animation(tucking ?? motion.body) { $0.modifier(IslandHeight(height: capsule.height)) }
                .offset(y: capsule.top)
                .animation(tucking ?? motion.body, value: capsule.top)
                // Fades in at once (it starts hidden inside the notch anyway) and out only
                // once it is back up there, so the glass stops drawing while closed.
                .opacity(capsule.shown ? 1 : 0)
                .animation(capsule.shown ? motion.glassIn : motion.glassOut, value: capsule.shown)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Nothing of the glass shows above the bottom of the notch: it slides out from
        // behind the menu bar's lower edge, where the notch is, and back in.
        .mask(alignment: .top) {
            VStack(spacing: 0) {
                Color.clear.frame(height: island.layout.notch.height)
                Color.black
            }
        }
    }

    /// The first time SwiftUI builds a view it creates the text field, loads fonts and
    /// symbols and lays everything out: about 27 ms here, which is three dropped frames
    /// at 120 Hz in the middle of the first open. The capsule's content itself is built at
    /// launch and kept (see `capsuleLayer`). What is rehearsed here, built once out of
    /// sight and thrown away, is everything it does not show yet: a pill, Ask mode with a
    /// finished answer in it (styled lines with references, the spinner line, the notices,
    /// an open source row) and the drop target. So the first switch to Ask and the first
    /// answer build nothing new while they animate.
    @ViewBuilder private var rehearsal: some View {
        if rehearsing {
            VStack {
                if let rehearsalPanel {
                    CapsuleView(island: island, panel: rehearsalPanel, isRehearsal: true)
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
                DropTargetContent(drop: DropTarget(kind: .link, detail: "rehearsal"))
                DropTargetContent(drop: DropTarget(kind: .note, detail: "rehearsal", isSaving: true))
                DropTargetContent(drop: DropTarget(kind: .file, detail: "rehearsal.pdf"))
                DropTargetContent(drop: DropTarget(kind: .refused("rehearsal"), detail: "rehearsal.txt"))
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

    /// The two concave curves that flare the body into the top edge of the screen. One
    /// shape that animates its radius itself, so a bouncy spring that swings the radius
    /// below zero draws nothing there instead of a frame with a negative size.
    private func shoulders(metrics: IslandMetrics, motion: IslandMotion) -> some View {
        IslandShoulders(radius: metrics.shoulder)
            .fill(.black)
            .animation(motion.corner, value: metrics.shoulder)
            .allowsHitTesting(false)
    }
}

/// The body's width, fed to layout afresh on every frame of its spring.
///
/// Why not a plain animated `frame(width:)`: SwiftUI lays the view out once, at the final
/// width, and then animates the size and the position of the result separately. The size
/// took this spring; the position (the body is centred) took the slower spring of the
/// surrounding `withAnimation`. So the right edge sprang out and overshot while the left
/// edge crawled, up to 10 points behind. An `Animatable` modifier is handed each in-between
/// width itself, so layout runs per frame and the body stays centred on the notch.
private struct IslandWidth: ViewModifier, Animatable {
    var width: CGFloat

    nonisolated var animatableData: CGFloat {
        get { width }
        set { width = newValue }
    }

    func body(content: Content) -> some View {
        content.frame(width: width)
    }
}

/// The same for the height, so everything inside the body (keyline, clip, content) sees
/// one consistent size on every frame.
private struct IslandHeight: ViewModifier, Animatable {
    var height: CGFloat

    nonisolated var animatableData: CGFloat {
        get { height }
        set { height = newValue }
    }

    func body(content: Content) -> some View {
        content.frame(height: height)
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

/// The capsule content's version of `IslandContentTransition`, for a view that stays in
/// place: the same blur, scale and fade, driven by "shown" instead of by insertion and
/// removal. Hidden, it takes no clicks and is invisible to VoiceOver. (Taking the blur
/// out was measured: no clear gain in open or close, so it stays.)
private struct CapsulePresence: ViewModifier {
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

/// A pill's glyph and words go soft once and come back sharp, while the pill's width
/// springs to fit new words ("Saved" to "Reading page"). Without it the words are sharp
/// while they morph and slide out with the growing edge, which reads as a resize. The blur peaks
/// early (the edge moves fastest at the start) and is back to exactly 0 well before the
/// spring settles; at rest nothing is blurred.
private struct MorphBlur: ViewModifier {
    let pulse: Int
    let peak: CGFloat

    func body(content: Content) -> some View {
        let scale = IslandMotion.timeScale
        let peak = peak
        content.keyframeAnimator(initialValue: CGFloat(0), trigger: pulse) { view, blur in
            view.blur(radius: blur)
        } keyframes: { _ in
            KeyframeTrack {
                CubicKeyframe(peak, duration: 0.07 * scale)
                CubicKeyframe(peak * 0.6, duration: 0.1 * scale)
                CubicKeyframe(0, duration: 0.25 * scale)
            }
        }
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
        // Its own width, not the stack's: while it fades out under a wider presentation
        // (the drop target) the + and dot must not slide apart.
        .frame(width: island.layout.peek.width, height: island.layout.notch.height)
    }
}

// MARK: - Drop target

/// A drag is over the notch: the capsule says what a release will do, next to a filled
/// accent circle (or, for a drag it turns down, why not, next to a grey one). A file's name
/// goes under the words. Laid out at the target's own width, never the stack's.
private struct DropTargetContent: View {
    let drop: DropTarget

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: drop.isRefused ? "nosign" : drop.kind == .file ? "doc" : "plus")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(Circle().fill(drop.isRefused ? Color.white.opacity(CapsuleStyle.controlFill) : CapsuleStyle.accent))
            VStack(alignment: .leading, spacing: 1) {
                Text(headline)
                    .font(.system(size: drop.isRefused ? 14 : 15, weight: .semibold))
                    .lineLimit(1)
                    .contentTransition(.opacity)
                if showsName {
                    Text(drop.detail)
                        .font(.system(size: 12))
                        .foregroundStyle(.white.opacity(CapsuleStyle.secondary))
                        .lineLimit(1)
                        // A long name keeps its start and its extension: "Quarterly re…2026.pdf".
                        .truncationMode(.middle)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.leading, 8)
        .padding(.trailing, 20)
        .frame(width: IslandLayout.dropWidth, height: IslandLayout.capsuleHeight)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(drop.detail.isEmpty ? headline : "\(headline): \(drop.detail)")
    }

    /// Files only: a note's or link's words stay in the accessibility label, as before.
    private var showsName: Bool {
        !drop.detail.isEmpty && (drop.kind == .file || drop.isRefused)
    }

    private var headline: String {
        if drop.isSaving { return "Saving…" }
        switch drop.kind {
        case .link: return "Release to save link"
        case .note: return "Release to save note"
        case .file: return "Release to save file"
        case .refused(let reason): return reason
        }
    }
}

extension DropTarget {
    var isRefused: Bool {
        if case .refused = kind { true } else { false }
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
