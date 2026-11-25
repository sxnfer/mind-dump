import QuartzCore
import SwiftUI

/// Ask mode, under the shared box: a hint, then the latest question, its answer as it
/// flows, what the agent is doing, and finally the sources.
///
/// The answer sits in a scroll view whose height is set here, not by SwiftUI: it is the
/// answer's own height until the panel reaches its maximum, then it stays put and the
/// answer scrolls inside, following its newest line.
struct AskSection: View {
    let ask: AskModel
    let island: IslandModel
    /// The most height this section may take (see `PanelView.askRoom`).
    let room: CGFloat
    var isRehearsal = false

    /// The answer's natural height, and where the scroll view starts in this section.
    @State private var contentHeight: CGFloat = 0
    @State private var scrollTop: CGFloat = 0
    /// How far the answer is scrolled. Kept in its own object and read only by the edge
    /// fade, so scrolling (ours, every frame while following) redraws the fade alone.
    @State private var scroll = AnswerScrollState()

    private var viewport: CGFloat {
        min(contentHeight, max(room - scrollTop, 80))
    }

    private var overflows: Bool { contentHeight > viewport + 0.5 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            hint
            if let message = ask.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.orange)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
            turn
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .coordinateSpace(.named(Self.sectionSpace))
    }

    // MARK: Hint

    private var hintText: String {
        switch ask.phase {
        case .waiting, .answering: "Answering · Esc stops"
        case .idle: "Return asks · Esc closes"
        case .done: ask.conversationID == nil ? "Return asks · Esc closes" : "Return asks a follow-up"
        }
    }

    private var hint: some View {
        HStack(spacing: 6) {
            Text(hintText)
                .contentTransition(.opacity)
                .animation(.easeOut(duration: 0.15), value: hintText)
            Spacer(minLength: 8)
            if ask.question != nil {
                Button(action: ask.newQuestion) {
                    HStack(spacing: 5) {
                        Image(systemName: "plus.bubble")
                        Text("New question")
                        Text("⌘N").foregroundStyle(.white.opacity(0.3))
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .focusable(false)
                .debugTarget("new-question")
                .accessibilityLabel("New question")
                .help("Forget this conversation and ask something new (⌘N)")
                .transition(.opacity)
            }
        }
        .font(.system(size: 12))
        .foregroundStyle(.white.opacity(0.5))
    }

    // MARK: One turn

    /// The question and its answer. The scroll view stays alive between turns (at zero
    /// height when there is none): building a new one for every question costs frames.
    /// What is inside it cross-fades from one turn to the next instead.
    private var turn: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let question = ask.question {
                // The question, moved up out of the box and dimmed: it is context now.
                Text(question)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.45))
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .accessibilityLabel("You asked: \(question)")
                    .id(ask.turnID)
                    .transition(IslandContentTransition(motion: island.motion))
            }
            answerScroll
                .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named(Self.sectionSpace)).minY } action: {
                    scrollTop = $0
                }
        }
        .padding(.top, ask.question == nil ? 0 : 16)
    }

    private var answerScroll: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                // A ZStack, so the old turn fading out and the new one coming in overlap
                // instead of stacking one under the other.
                ZStack(alignment: .top) {
                    Color.clear.frame(height: 0).id(Self.topID)
                    answerContent
                        .id(ask.turnID)
                        .transition(IslandContentTransition(motion: island.motion))
                        .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(Self.scrollSpace)) } action: {
                            [turn = ask.turnID] frame in
                            // Only the current turn sizes the scroll view, not the one leaving.
                            guard turn == ask.turnID else { return }
                            scrolled(to: frame)
                        }
                }
            }
            .coordinateSpace(.named(Self.scrollSpace))
            .scrollIndicators(overflows ? .automatic : .never)
            .scrollDisabled(!overflows)
            .frame(height: ask.question == nil ? 0 : viewport, alignment: .top)
            // Drawn over the edges, not as a mask: a SwiftUI scroll view on the Mac is an
            // AppKit scroll view inside, and a mask does not reach it. On true black, a
            // black-to-clear gradient on top looks exactly like a faded edge.
            .overlay { EdgeFade(scroll: scroll, contentHeight: contentHeight, viewport: viewport, active: overflows) }
            .onChange(of: contentHeight) { _, _ in
                guard scroll.following, overflows, ask.phase != .done else { return }
                // Keep the newest line in sight. Critically damped, unlike the body's
                // spring: a rebound would move the text back up, which looks like the
                // reader scrolling and would switch following off.
                scroll.autoScrollUntil = CACurrentMediaTime() + 0.7 * IslandMotion.timeScale
                withAnimation(island.reduceMotion ? IslandMotion.reduced.body : IslandMotion.spring(0.45, 1)) {
                    proxy.scrollTo(Self.tailID, anchor: .bottom)
                }
            }
            .onChange(of: ask.turnID) { _, _ in
                // A new turn starts at its top, following its newest line.
                scroll.following = true
                proxy.scrollTo(Self.topID, anchor: .top)
            }
            .onChange(of: ask.expanded) { old, new in
                // A row just opened: scroll only as far as needed to show its passage.
                guard let opened = new.subtracting(old).first else { return }
                withAnimation(island.reduceMotion ? IslandMotion.reduced.body : IslandMotion.resize.body) {
                    proxy.scrollTo(SourcesList.rowID(opened))
                }
            }
        }
    }

    private var answerContent: some View {
        VStack(alignment: .leading, spacing: 14) {
            // Always there, empty until the first words: inserting a whole new text view
            // at the moment the words start cost two dropped frames. Now the first words
            // add one line to a view that already exists. No blur: characters ink in.
            AnswerText(reveal: ask.reveal)
                .opacity(ask.partialFailed ? 0.4 : 1)
                .id(ask.reveal.generation)
                .transition(.opacity)
            if let status = ask.status {
                // Leaves quickly: the first words take its place.
                StatusLine(status: status)
                    .padding(.top, ask.reveal.hasText ? 0 : -14)  // no gap under an empty answer
                    .transition(.opacity.animation(.easeOut(duration: 0.1 * IslandMotion.timeScale)))
            }
            if ask.phase == .done, let answer = ask.answer, !ask.partialFailed {
                if !answer.grounded {
                    Notice(
                        glyph: "circle.dashed",
                        text: "Not found in your items. Nothing you saved answers this.",
                        strong: true
                    )
                    .transition(.opacity)
                }
                if answer.incomplete {
                    Notice(
                        glyph: "ellipsis.circle",
                        text: "The search ran out of steps, so this answer may be incomplete.",
                        strong: false
                    )
                    .transition(.opacity)
                }
                if !ask.sources.isEmpty {
                    SourcesList(ask: ask, island: island)
                }
            }
            Color.clear.frame(height: 0).id(Self.tailID)
        }
        .padding(.bottom, 2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Scrolling

    private func scrolled(to frame: CGRect) {
        let grew = abs(frame.height - contentHeight) > 0.5
        if grew { contentHeight = frame.height }
        let moved = frame.minY - scroll.offset
        scroll.offset = frame.minY
        // While our own scroll runs, its motion says nothing about the reader.
        guard !grew, abs(moved) > 0.5, CACurrentMediaTime() > scroll.autoScrollUntil else { return }
        // Only the reader moves the content down (minY grows, towards the top of the
        // answer): stop following then. Back at the bottom, follow again.
        let fromBottom = frame.maxY - viewport
        if moved > 0 && fromBottom > 6 { scroll.following = false }
        if fromBottom <= 6 { scroll.following = true }
    }

    nonisolated private static let sectionSpace = "askSection"
    nonisolated private static let scrollSpace = "answerScroll"
    private static let tailID = "answerTail"
    private static let topID = "answerTop"
}

// MARK: - Answer text

/// The answer, one Text per line. Finished lines never change, so SwiftUI skips them;
/// each frame only re-lays out the line still flowing in.
private struct AnswerText: View {
    let reveal: AnswerReveal

    var body: some View {
        VStack(alignment: .leading, spacing: AnswerLine.lineGap) {
            ForEach(reveal.frozen) { line in
                AnswerLine(line: line, selectable: true).equatable()
            }
            LiveLines(reveal: reveal)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // One element for VoiceOver: the whole answer, read as one.
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Text(reveal.plainText))
    }
}

/// The only part that changes every frame while text flows.
private struct LiveLines: View {
    let reveal: AnswerReveal

    var body: some View {
        ForEach(reveal.live) { line in
            AnswerLine(line: line, selectable: false)
        }
    }
}

private struct AnswerLine: View, Equatable {
    let line: AnswerReveal.Line
    /// Finished lines can be selected and copied. Lines still flowing cannot: a
    /// selectable text rebuilds its selection data every time it changes, which was the
    /// largest single cost of each frame while words flowed. Once the answer is complete
    /// every line is finished, so all of it is selectable.
    let selectable: Bool

    /// Lines of one paragraph sit this far apart, matching the wrap's line spacing, so a
    /// hard newline and a soft wrap look the same.
    static let lineGap: CGFloat = 4.5

    var body: some View {
        if line.isPending {
            EmptyView()
        } else if line.isGap {
            Color.clear.frame(height: 5)
        } else {
            let text = Text(line.text)
                .font(.system(size: 13.5))
                .lineSpacing(Self.lineGap)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if selectable { text.textSelection(.enabled) } else { text }
        }
    }
}

// MARK: - Status

/// "Thinking", "Searching “grinder”", "Reading an item", with the island's spinner.
struct StatusLine: View {
    let status: APIClient.AskStatus

    private var label: String {
        switch status {
        case .thinking: "Thinking"
        case .searching(let query):
            query.map { "Searching “\($0)”" } ?? "Searching"
        case .reading: "Reading an item"
        }
    }

    var body: some View {
        HStack(spacing: 8) {
            SpinnerRing().frame(width: 11, height: 11)
            // A soft cross-fade between the old words and the new. (A glyph morph,
            // `.interpolate`, redraws the text every frame and cost dropped frames.)
            ZStack(alignment: .leading) {
                Text(label)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .id(label)
                    .transition(.opacity)
            }
            .animation(.easeInOut(duration: 0.22 * IslandMotion.timeScale), value: label)
        }
        .font(.system(size: 12.5, weight: .medium))
        .foregroundStyle(.white.opacity(0.6))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(label)
    }
}

/// A calm line under the answer: not from your items, or cut short.
struct Notice: View {
    let glyph: String
    let text: String
    /// The "not from your items" notice must be read: brighter, on a faint panel.
    let strong: Bool

    var body: some View {
        Label {
            Text(text).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: glyph)
        }
        .font(.system(size: 12.5, weight: strong ? .medium : .regular))
        .foregroundStyle(.white.opacity(strong ? 0.82 : 0.5))
        .padding(.horizontal, strong ? 10 : 0)
        .padding(.vertical, strong ? 8 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            if strong { RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.white.opacity(0.07)) }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Sources

private struct SourcesList: View {
    let ask: AskModel
    let island: IslandModel

    static func rowID(_ number: Int) -> String { "source-row-\(number)" }

    var body: some View {
        let animateIn = !ask.sourcesHaveEntered
        VStack(alignment: .leading, spacing: 1) {
            Text("Sources")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.white.opacity(0.36))
                .padding(.leading, 8)
                .padding(.bottom, 4)
                .modifier(Entrance(order: 0, animate: animateIn, reduceMotion: island.reduceMotion))
            ForEach(Array(ask.sources.enumerated()), id: \.element.id) { index, source in
                SourceRow(ask: ask, island: island, source: source, expanded: ask.expanded.contains(source.number))
                    .id(Self.rowID(source.number))
                    .modifier(Entrance(order: index + 1, animate: animateIn, reduceMotion: island.reduceMotion))
            }
        }
        // Rows have their own padding for the hover panel; pull them back so the
        // numbers line up with the answer's left edge.
        .padding(.horizontal, -8)
        .onAppear { ask.sourcesHaveEntered = true }
    }
}

/// Sources arrive one after another, about 40 ms apart, each on the content spring:
/// a little blur and scale, then sharp. With Reduce Motion they simply fade, together.
private struct Entrance: ViewModifier {
    let order: Int
    let animate: Bool
    let reduceMotion: Bool
    @State private var shown = false

    func body(content: Content) -> some View {
        let visible = shown || !animate
        content
            .opacity(visible ? 1 : 0)
            .blur(radius: visible || reduceMotion ? 0 : 6)
            .scaleEffect(visible || reduceMotion ? 1 : 0.96, anchor: .topLeading)
            .offset(y: visible || reduceMotion ? 0 : 4)
            .onAppear {
                guard animate, !shown else { return }
                let animation =
                    reduceMotion
                    ? IslandMotion.reduced.contentIn
                    : IslandMotion.spring(0.34, 1, delay: 0.04 * Double(order))
                withAnimation(animation) { shown = true }
            }
    }
}

private struct SourceRow: View {
    let ask: AskModel
    let island: IslandModel
    let source: AskModel.Source
    let expanded: Bool
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("\(source.number)")
                    .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                    .foregroundStyle(AnswerReveal.accent)
                    .frame(width: 12, alignment: .leading)
                Image(systemName: source.glyph)
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.5))
                    .frame(width: 14)
                Text(source.title)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.86))
                    .lineLimit(1)
                    .truncationMode(.tail)
                if let page = source.citation.page {
                    Text("p. \(page)")
                        .font(.system(size: 11.5).monospacedDigit())
                        .foregroundStyle(.white.opacity(0.4))
                        .fixedSize()
                }
                Spacer(minLength: 6)
                if let link = source.link {
                    Button { ask.open(source) } label: {
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 10.5, weight: .semibold))
                            .frame(width: 20, height: 18)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .focusable(false)
                    .foregroundStyle(.white.opacity(hovering ? 0.75 : 0.45))
                    .debugTarget("open-\(source.number)")
                    .accessibilityLabel("Open \(link.host() ?? "link")")
                    .help(link.absoluteString)
                }
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.white.opacity(hovering || expanded ? 0.5 : 0.22))
                    .rotationEffect(.degrees(expanded ? 180 : 0))
            }
            .frame(minHeight: 18)
            .debugTarget("source-\(source.number)")
            if expanded {
                Text(source.citation.quote.trimmingCharacters(in: .whitespacesAndNewlines))
                    .font(.system(size: 12))
                    .foregroundStyle(.white.opacity(0.6))
                    .lineSpacing(3)
                    .lineLimit(9)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 42)
                    .padding(.trailing, 6)
                    .padding(.bottom, 2)
                    .textSelection(.enabled)
                    .transition(.opacity.combined(with: .offset(y: -3)))
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background {
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(.white.opacity(expanded ? 0.06 : hovering ? 0.045 : 0))
        }
        .contentShape(Rectangle())
        .onTapGesture { ask.toggle(source) }
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Source \(source.number): \(source.title)\(source.citation.page.map { ", page \($0)" } ?? "")")
        .accessibilityHint(expanded ? "Hides the passage" : "Shows the passage")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { ask.toggle(source) }
    }
}

/// Where the answer is scrolled to, and whether it follows the newest line.
@MainActor
@Observable
private final class AnswerScrollState {
    /// 0 at the top, negative as the answer scrolls up.
    var offset: CGFloat = 0
    /// True while the view keeps the newest line in sight. Scrolling up turns it off;
    /// scrolling back to the bottom turns it on again.
    @ObservationIgnored var following = true
    /// Until when the view's own follow scroll is still moving.
    @ObservationIgnored var autoScrollUntil: CFTimeInterval = 0
}

/// Soft edges where the answer scrolls under them: black to clear, nothing else. Each
/// edge shows only when there is more answer beyond it, and grows with the distance.
private struct EdgeFade: View {
    let scroll: AnswerScrollState
    let contentHeight: CGFloat
    let viewport: CGFloat
    let active: Bool

    var body: some View {
        let top = active ? min(max(-scroll.offset / 18, 0), 1) : 0
        let bottom = active ? min(max((contentHeight + scroll.offset - viewport) / 18, 0), 1) : 0
        VStack(spacing: 0) {
            LinearGradient(colors: [.black, .clear], startPoint: .top, endPoint: .bottom)
                .frame(height: 22)
                .opacity(top)
            Spacer(minLength: 0)
            LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                .frame(height: 22)
                .opacity(bottom)
        }
        .allowsHitTesting(false)
    }
}

