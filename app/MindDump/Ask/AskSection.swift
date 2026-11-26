import QuartzCore
import SwiftUI

/// Ask's answer card, under the capsule's line: a hairline, the question in small text,
/// then what the agent is doing, the answer as it flows, and finally the sources. Before
/// anything is asked (and with no error) it takes no height, and the capsule is one line.
///
/// The answer sits in a scroll view whose height is set here, not by SwiftUI: it is the
/// answer's own height until the card reaches its maximum, then it stays put and the
/// answer scrolls inside, following its newest line.
struct AskSection: View {
    let ask: AskModel
    let island: IslandModel
    /// The most height this section may take, less its bottom padding (see
    /// `CapsuleView.askRoom`).
    let room: CGFloat
    var isRehearsal = false

    /// Space under the card's last line.
    static let bottomPadding: CGFloat = 16
    /// Space at the card's sides, from the glass's edge to the text.
    static let sideMargin: CGFloat = 22
    /// Space between the answer's parts (its text, the status line, notices, sources).
    private static let partGap: CGFloat = 14

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

    /// A question was asked, or asking failed: the capsule is a card.
    private var isCard: Bool { ask.question != nil || ask.errorMessage != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle()
                .fill(.white.opacity(0.16))
                .frame(height: 1)
                .padding(.horizontal, 20)
                .opacity(isCard ? 1 : 0)
            VStack(alignment: .leading, spacing: 0) {
                header
                if let message = ask.errorMessage {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(CapsuleStyle.warning)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, ask.question == nil ? 0 : 10)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
                answerScroll
                    .padding(.top, ask.question == nil ? 0 : 10)
                    .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named(Self.sectionSpace)).minY } action: {
                        scrollTop = $0
                    }
            }
            .padding(.horizontal, Self.sideMargin)
            .padding(.top, isCard ? 13 : 0)
            .padding(.bottom, isCard ? Self.bottomPadding : 0)
        }
        // A follow-up keeps the last answer's height while it waits (`AskModel.fitCard`).
        .frame(minHeight: ask.heldCardHeight, alignment: .top)
        // Nothing asked: no height at all. (The scroll view inside stays built, at zero
        // height: building a new one for every question costs frames.)
        .frame(height: isCard ? nil : 0, alignment: .top)
        .clipped()
        .frame(maxWidth: .infinity, alignment: .leading)
        .coordinateSpace(.named(Self.sectionSpace))
    }

    // MARK: Header

    /// The question, small and dimmed: it is context now. Beside it, "New" (⌘N).
    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            if let question = ask.question {
                Text(question)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.white.opacity(CapsuleStyle.secondary))
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .accessibilityLabel("You asked: \(question)")
                    .id(ask.turnID)
                    .transition(IslandContentTransition(motion: island.motion))
            } else {
                Spacer(minLength: 0)
            }
            if isCard {
                Button(action: ask.newQuestion) {
                    HStack(spacing: 4) {
                        Text("New")
                        Text("⌘N").foregroundStyle(.white.opacity(0.4))
                    }
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.7))
                    .padding(.horizontal, 8)
                    .frame(height: 20)
                    .background(Capsule().fill(.white.opacity(CapsuleStyle.controlFill)))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .focusable(false)
                .debugTarget("new-question")
                .accessibilityLabel("New question")
                .help("Forget this conversation and ask something new (⌘N)")
                .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: One turn

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
            // AppKit scroll view inside, and a mask does not reach it.
            .overlay { EdgeFade(scroll: scroll, contentHeight: contentHeight, viewport: viewport, active: overflows) }
            .onChange(of: contentHeight) { _, _ in
                guard scroll.following, overflows, ask.phase != .done else { return }
                // Keep the newest line in sight. Critically damped, unlike the body's
                // spring: a rebound would move the text back up, which looks like the
                // reader scrolling and would switch following off.
                autoScroll(proxy, to: Self.tailID, anchor: .bottom, IslandMotion.spring(0.45, 1), holding: 0.7)
            }
            .onChange(of: ask.phase) { _, phase in
                // The sources (and any notice) come in under the answer's last line. If the
                // reader was following the newest line, keep following, so the rows are
                // seen arriving; if they had scrolled up, leave them where they are. On the
                // next turn of the main queue, once the rows are laid out.
                guard phase == .done, scroll.following else { return }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12 * IslandMotion.timeScale) {
                    autoScroll(proxy, to: Self.tailID, anchor: .bottom, IslandMotion.spring(0.6, 1), holding: 1.0)
                }
            }
            .onChange(of: ask.turnID) { _, _ in
                // A new turn starts at its top, following its newest line.
                scroll.following = true
                proxy.scrollTo(Self.topID, anchor: .top)
            }
            .onChange(of: ask.expanded) { old, new in
                // A row just opened: scroll only as far as needed to show its passage.
                // On the next turn of the main queue, once the row has its new height.
                guard let opened = new.subtracting(old).first else { return }
                DispatchQueue.main.async {
                    autoScroll(proxy, to: SourcesList.rowEndID(opened), anchor: nil, IslandMotion.spring(0.5, 1), holding: 0.8)
                }
            }
        }
    }

    /// The parts are spaced by hand, not by the stack, so the gaps under a status line
    /// alone can differ from the gaps between parts.
    private var answerContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Always there, empty until the first words: inserting a whole new text view
            // at the moment the words start cost two dropped frames. Now the first words
            // add one line to a view that already exists. No blur: characters ink in.
            AnswerText(reveal: ask.reveal)
                .opacity(ask.partialFailed ? 0.4 : 1)
                .id(ask.reveal.generation)
                .transition(.opacity)
            if let status = ask.status {
                // Leaves quickly: the first words take its place. With no words yet it
                // sits where the first line of the answer will sit.
                StatusLine(status: status)
                    .padding(.top, ask.reveal.hasText ? Self.partGap : 0)
                    .transition(.opacity.animation(.easeOut(duration: 0.1 * IslandMotion.timeScale)))
            }
            if ask.phase == .done, let answer = ask.answer, !ask.partialFailed {
                if !answer.grounded {
                    Notice(
                        glyph: "circle.dashed",
                        text: "Not found in your items. Nothing you saved answers this.",
                        strong: true
                    )
                    .padding(.top, Self.partGap)
                    .transition(.opacity)
                }
                if answer.incomplete {
                    Notice(
                        glyph: "ellipsis.circle",
                        text: "The search ran out of steps, so this answer may be incomplete.",
                        strong: false
                    )
                    .padding(.top, Self.partGap)
                    .transition(.opacity)
                }
                if !ask.sources.isEmpty {
                    SourcesList(ask: ask, island: island)
                        .padding(.top, Self.partGap)
                }
            }
            Color.clear.frame(height: 0)
                .padding(.top, statusAlone ? Self.statusAloneGap : Self.partGap)
                .id(Self.tailID)
        }
        .padding(.bottom, 2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Only the status line shows (thinking, searching, reading): the card fits it exactly.
    private var statusAlone: Bool { ask.status != nil && !ask.reveal.hasText }

    /// Under a status line alone, the line's ink ends as far from the card's bottom edge
    /// as the text starts from its sides: this gap, the 2 points under the content and
    /// the card's bottom padding make the side margin. (Measured on screen: 22 points
    /// under and 22 at the sides; the line's ink and the glass's edge each take about a
    /// point, and cancel out.) Between parts the gap stays 14, under an answer as before.
    private static let statusAloneGap: CGFloat = sideMargin - bottomPadding - 2

    // MARK: Scrolling

    /// The view's own scrolling (following the newest line, the sources, an opened row).
    /// While it runs, its motion is not mistaken for the reader's (`autoScrollUntil`).
    private func autoScroll(
        _ proxy: ScrollViewProxy, to id: String, anchor: UnitPoint?, _ animation: Animation, holding seconds: Double
    ) {
        scroll.autoScrollUntil = CACurrentMediaTime() + seconds * IslandMotion.timeScale
        withAnimation(island.reduceMotion ? IslandMotion.reduced.body : animation, completionCriteria: .logicallyComplete) {
            proxy.scrollTo(id, anchor: anchor)
        } completion: {
            // After an animated scroll, SwiftUI kept hit-testing the rows at their old
            // places until a real scroll event came: a click on a source did nothing.
            // Landing once more, without animation, on the same spot brings it up to date.
            proxy.scrollTo(id, anchor: anchor)
        }
    }

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

    /// A zero-height mark at a row's bottom edge: scrolling it into view shows all of
    /// an opened row (a row whose top is already on screen counts as visible).
    static func rowEndID(_ number: Int) -> String { "source-row-end-\(number)" }

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
                    .overlay(alignment: .bottom) {
                        Color.clear.frame(height: 0).id(Self.rowEndID(source.number))
                    }
                    .modifier(Entrance(order: index + 1, animate: animateIn, reduceMotion: island.reduceMotion))
            }
        }
        // Rows have their own padding for the hover panel; pull them back so the
        // numbers line up with the answer's left edge.
        .padding(.horizontal, -8)
        .animation(island.reduceMotion ? IslandMotion.reduced.body : IslandMotion.resize.body, value: ask.expanded)
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
                // One line: the title shortens first; the pages (fixed size) and the
                // arrow (fixed frame) keep theirs.
                Text(source.title)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.86))
                    .lineLimit(1)
                    .truncationMode(.tail)
                if let pages = source.pageLabel {
                    Text(pages)
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
                // Every passage of this item the answer cited, once each, in the order
                // first cited. With several, each quote names its own page and shows at
                // most three lines (ending in "…"), and at most six quotes show: a row
                // with many passages stays a short list to scan, not a wall of text.
                let several = source.citations.count > 1
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(source.shownCitations, id: \.marker) { citation in
                        VStack(alignment: .leading, spacing: 2) {
                            if several, let page = citation.page {
                                Text("p. \(page)")
                                    .font(.system(size: 10.5).monospacedDigit())
                                    .foregroundStyle(.white.opacity(0.36))
                            }
                            Text(citation.quote.trimmingCharacters(in: .whitespacesAndNewlines))
                                .font(.system(size: 12))
                                .foregroundStyle(.white.opacity(0.6))
                                .lineSpacing(3)
                                .lineLimit(several ? 3 : 9)
                                .truncationMode(.tail)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if source.hiddenCount > 0 {
                        Text("and \(source.hiddenCount) more passages")
                            .font(.system(size: 11))
                            .foregroundStyle(.white.opacity(0.36))
                    }
                }
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
        .accessibilityLabel("Source \(source.number): \(source.title)\(source.pageLabel.map { ", \($0)" } ?? "")")
        .accessibilityHint(
            expanded
                ? (source.citations.count > 1 ? "Hides the passages" : "Hides the passage")
                : (source.citations.count > 1 ? "Shows \(source.citations.count) passages" : "Shows the passage")
        )
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

/// Soft edges where the answer scrolls under them: a faint shade, nothing else. Each edge
/// shows only when there is more answer beyond it, and grows with the distance. On the
/// dark glass a light shade of black reads as the text passing under the edge, without
/// painting a black band that the glass would show.
private struct EdgeFade: View {
    let scroll: AnswerScrollState
    let contentHeight: CGFloat
    let viewport: CGFloat
    let active: Bool

    var body: some View {
        let top = active ? min(max(-scroll.offset / 18, 0), 1) : 0
        let bottom = active ? min(max((contentHeight + scroll.offset - viewport) / 18, 0), 1) : 0
        let shade = Color.black.opacity(0.35)
        VStack(spacing: 0) {
            LinearGradient(colors: [shade, .clear], startPoint: .top, endPoint: .bottom)
                .frame(height: 18)
                .opacity(top)
            Spacer(minLength: 0)
            LinearGradient(colors: [.clear, shade], startPoint: .top, endPoint: .bottom)
                .frame(height: 18)
                .opacity(bottom)
        }
        .padding(.horizontal, -22)  // edge to edge across the card
        .allowsHitTesting(false)
    }
}
