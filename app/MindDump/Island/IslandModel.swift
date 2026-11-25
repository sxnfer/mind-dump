import AppKit
import Observation
import SwiftUI

/// A small status the island shows as a pill: an icon in the left wing, a few words in
/// the right wing. The top activity decides what the island shows when it is not open.
struct IslandActivity: Equatable, Identifiable {
    enum Glyph: Equatable {
        case spinner
        case check
        case warning
        case symbol(String)
    }

    var id: String
    var glyph: Glyph
    var text: String
    var tint: Color = .white
    /// Seconds before the pill folds away by itself. nil stays until replaced.
    var hold: Double? = IslandMotion.confirmationHold
}

/// What a drop on the island would save, shown while a drag carrying text is over it.
struct DropTarget: Equatable {
    var isLink: Bool
    /// What will be saved, short: the link's host and path, or the note's first words.
    var detail: String
    /// Released, and the save is taking longer than a glance.
    var isSaving = false
}

/// What the island is showing. The view draws this; it holds no logic.
///
/// The first three are the black body at the notch. The last two bring out the glass
/// capsule under it, while the body rests as the bare notch.
enum IslandPresentation: Equatable {
    case idle                       // the bare notch
    case peek                       // pointer is over it
    case compact(IslandActivity)    // a status pill
    case dropTarget(DropTarget)     // a drag carrying text is over it: the capsule is the target
    case open                       // the capsule: the box, and the answer card in Ask

    var isOpen: Bool { self == .open }

    var isDropTarget: Bool {
        if case .dropTarget = self { true } else { false }
    }
}

/// The island's state machine and choreography.
///
/// It owns four things that must change together: the presentation (what content shows),
/// the metrics (how big the black body is), the capsule (where and how big the glass is)
/// and the motion (which springs carry the change). Every transition is one method here,
/// so the timing lives in one place.
@MainActor
@Observable
final class IslandModel {
    private(set) var presentation: IslandPresentation = .idle
    private(set) var metrics: IslandMetrics
    private(set) var capsule: CapsuleMetrics
    private(set) var motion: IslandMotion = .settle
    /// True while the pointer is held down on the island: the body swells a little.
    var isPressed = false

    var layout: IslandLayout {
        didSet {
            guard layout != oldValue else { return }
            metrics = target(for: presentation)
            capsule = capsuleTarget(for: presentation)
        }
    }

    /// The capsule's content height, measured by the view once it has laid out (rounded
    /// up to a step while an answer streams).
    private(set) var capsuleContentHeight: CGFloat = IslandLayout.capsuleHeight
    private var measuredContentHeight: CGFloat = IslandLayout.capsuleHeight

    /// True while an answer streams: the card grows in steps of a few lines, not on every
    /// new line, then fits its content exactly once the answer is complete.
    ///
    /// Why: the glass shape resizing on its spring for each new line was the cost of a
    /// streaming answer. Measured on the same long canned answer, in late frames per
    /// second: every line 2.37 and 2.48; in 60-point steps 1.68 and 1.62; the slice 3
    /// black panel at the same time 1.86 and 2.44. (A solid capsule with no glass at all
    /// was 2.55 and 2.99, and taking out the mask 2.19 and 1.92: the glass was not it.)
    /// The text pours exactly as before; only the card's edge runs a little ahead of it.
    var growsInSteps = false {
        didSet {
            // Back to an exact fit. On the next turn of the main queue, so the content
            // that ended the stream (the sources, a notice, an error) is measured first
            // and the card makes one move, not a shrink and then a grow.
            guard oldValue, !growsInSteps else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.growsInSteps else { return }
                self.capsuleContentMeasured(self.measuredContentHeight)
            }
        }
    }
    /// Wing widths the current pill needs, measured by the view.
    private var compactWings = CGSize(width: 44, height: 44)

    /// System "Reduce motion". The view keeps this in sync.
    var reduceMotion = false {
        didSet {
            // With it on, a closed capsule waits where it opens, hidden, so the next open
            // only fades it in: nothing travels out of the notch.
            guard reduceMotion, !capsule.shown else { return }
            var resting = layout.capsule(content: capsuleContentHeight)
            resting.shown = false
            capsule = resting
        }
    }

    /// The window listens: an open capsule takes the keyboard, anything else gives it back.
    var onOpenChange: ((Bool) -> Void)?
    /// Called just before the capsule starts to close, before the collapse is applied, so
    /// work that would land in the collapse's first frame can be stopped first.
    var onWillClose: (() -> Void)?
    /// Called for every transition, so the frame monitor can time it.
    var onTransition: ((String) -> Void)?

    private var isHovering = false
    /// Debug only: a script is standing in for the pointer, so ignore the real one.
    var hoverIsScripted = false
    /// First leg of a collapse: the capsule is going back into the notch, and the body
    /// waits there before it opens out to the pill.
    private var isCollapsingToNotch = false
    private var lastTransition = ContinuousClock.now
    private var lastContentChange = ContinuousClock.now - .seconds(10)
    /// A transition started so recently that its springs are still doing the work.
    private var isMidTransition: Bool {
        ContinuousClock.now - lastTransition < IslandMotion.seconds(0.3)
    }
    private var sequence: Task<Void, Never>?
    private var hoverExit: Task<Void, Never>?
    /// A running status pill ("Reading page") the drop target covered, or a status that
    /// came in while the drag was over the island. It comes back if the drag leaves.
    private var parked: IslandActivity?
    /// The drop was released: the next pill is its result, so it squeezes out of the target.
    private var dropReleased = false

    init(layout: IslandLayout = .fallback) {
        self.layout = layout
        metrics = layout.idle
        capsule = layout.tucked
    }

    // MARK: Intent

    /// The pointer entered or left the island's hot zone.
    func hover(_ inside: Bool, scripted: Bool = false) {
        guard scripted || !hoverIsScripted, inside != isHovering else { return }
        isHovering = inside
        hoverExit?.cancel()
        if inside {
            if presentation == .idle { go(.peek, with: .peek, name: "peek") }
        } else {
            // A short grace: the body shrinks under the pointer, which would otherwise
            // make "inside" flap at the edge.
            hoverExit = Task { [weak self] in
                try? await Task.sleep(for: IslandMotion.seconds(IslandMotion.hoverExitGrace))
                guard let self, !Task.isCancelled, !self.isHovering else { return }
                if self.presentation == .peek { self.go(.idle, with: .unpeek, name: "unpeek") }
            }
        }
    }

    /// A click on the notch.
    func toggle() {
        presentation.isOpen ? close() : open()
    }

    func open() {
        guard !presentation.isOpen else { return }
        parked = nil
        dropReleased = false
        NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now)
        go(.open, with: .open, name: "open")
    }

    /// Close with nothing to report: the capsule goes back into the notch (and the body
    /// peeks, if the pointer is still there).
    func close() {
        guard presentation.isOpen else { return }
        go(isHovering ? .peek : .idle, with: .dismiss, name: "close")
    }

    /// Show a status pill. From the open capsule (or a released drop target) this is a
    /// collapse: the capsule goes back up into the notch, and as it arrives the body opens
    /// out to the pill, which reads as the notch swallowing it and answering.
    func show(_ activity: IslandActivity) {
        if presentation.isDropTarget && !dropReleased {
            // A status for an earlier item while a drag is over the island. Changing shape
            // now would pull the target from under the drag; keep it for after.
            parked = activity
            return
        }
        // A released drop goes into its pill the same way a save from the box does.
        let fromDrop = presentation.isDropTarget
        let fromLarge = presentation.isOpen || fromDrop
        parked = nil
        dropReleased = false
        if case .compact(let current) = presentation, current.id == activity.id {
            // Same pill, new words ("Saved" to "Reading"): change in place.
            go(.compact(activity), with: .settle, name: "pill-update", hold: activity.hold)
            return
        }
        if fromLarge && !reduceMotion {
            sequence?.cancel()
            apply(
                .compact(activity), metrics: layout.idle, capsule: layout.tucked, motion: .collapse,
                name: fromDrop ? "drop" : "collapse"
            )
            isCollapsingToNotch = true
            sequence = Task { [weak self] in
                try? await Task.sleep(for: IslandMotion.seconds(IslandMotion.collapseRetarget))
                guard let self, !Task.isCancelled else { return }
                self.isCollapsingToNotch = false
                self.apply(
                    .compact(activity), metrics: self.target(for: .compact(activity)),
                    capsule: self.capsule, motion: .settle, name: fromDrop ? "drop-settle" : "collapse-settle"
                )
                await self.holdThenDismiss(activity.hold)
            }
        } else {
            go(.compact(activity), with: fromLarge ? .settle : .appear, name: "pill", hold: activity.hold)
        }
    }

    /// Fold a pill away now.
    func dismissActivity(id: String) {
        if presentation.isDropTarget, parked?.id == id { parked = nil }
        guard case .compact(let current) = presentation, current.id == id else { return }
        go(isHovering ? .peek : .idle, with: .dismiss, name: "dismiss")
    }

    // MARK: Drop target

    /// A drag carrying text is over the notch: bring the capsule out as the target and say
    /// what a release saves. Not over the open capsule; its box takes drops in its own way.
    func showDropTarget(_ drop: DropTarget) {
        guard !presentation.isOpen else { return }
        if case .dropTarget(let current) = presentation {
            guard current != drop else { return }
            // Same drag, new words ("Saving"): change in place.
            go(.dropTarget(drop), with: .settle, name: "target-update")
            return
        }
        // A running status ("Reading page") comes back if the drag leaves. A confirmation
        // ("Saved") was about to fold away anyway.
        if case .compact(let activity) = presentation, activity.hold == nil { parked = activity } else { parked = nil }
        dropReleased = false
        go(.dropTarget(drop), with: .target, name: "target")
    }

    /// The drag left, or was cancelled: back to what showed before.
    func hideDropTarget() {
        guard presentation.isDropTarget else { return }
        dropReleased = false
        if let activity = parked {
            parked = nil
            go(.compact(activity), with: .settle, name: "target-exit", hold: activity.hold)
        } else {
            go(isHovering ? .peek : .idle, with: .dismiss, name: "target-exit")
        }
    }

    /// The drag was released over the island. Nothing moves yet: the save's result (a
    /// pill, or the open capsule on failure) is the next transition.
    func dropTargetReleased() {
        guard presentation.isDropTarget else { return }
        dropReleased = true
        parked = nil
    }

    /// The save is slow (a busy backend): say so in the target, which has not moved.
    func dropTargetSaving() {
        guard dropReleased, case .dropTarget(var drop) = presentation else { return }
        drop.isSaving = true
        showDropTarget(drop)
    }

    // MARK: Measurements from the view

    func capsuleContentMeasured(_ measured: CGFloat) {
        measuredContentHeight = measured
        // While an answer streams, the card grows a few lines at a time (see
        // `growsInSteps`); otherwise it fits its content exactly.
        let step = IslandMotion.answerGrowthStep
        let height = growsInSteps && measured > 2 * step ? (measured / step).rounded(.up) * step : measured
        guard abs(height - capsuleContentHeight) > 0.5 else { return }
        capsuleContentHeight = height
        guard presentation.isOpen else { return }
        // Already open and settled: the content grew or shrank in place (an answer card,
        // an error line). While the capsule is still opening, keep its springs and only
        // move the target.
        if !isMidTransition { use(reduceMotion ? .reduced : .resize) }
        capsule = capsuleTarget(for: .open)
        // A resize that a named change (a mode switch) just caused is part of that change.
        let named = ContinuousClock.now - lastContentChange < IslandMotion.seconds(0.3)
        if !isMidTransition && !named { onTransition?("resize") }
    }

    /// The open capsule's content is about to change in place (a mode switch, a new
    /// answer, sources coming in). Content crosses over and the height follows on the
    /// resize springs, not on whatever transition ran last.
    func capsuleContentWillChange(_ name: String) {
        guard presentation.isOpen else { return }
        // While the capsule is still opening, its own springs keep the shape.
        if !isMidTransition { use(reduceMotion ? .reduced : .resize) }
        lastContentChange = .now
        onTransition?(name)
    }

    func compactWingsMeasured(leading: CGFloat, trailing: CGFloat) {
        let wings = CGSize(width: leading, height: trailing)
        guard abs(wings.width - compactWings.width) > 0.5 || abs(wings.height - compactWings.height) > 0.5
        else { return }
        compactWings = wings
        guard case .compact = presentation, !isCollapsingToNotch else { return }
        // The words changed width ("Saved" to "Reading page"): follow, without a bounce.
        if !isMidTransition { use(reduceMotion ? .reduced : .settle) }
        metrics = target(for: presentation)
    }

    // MARK: Choreography

    /// Change springs only when they differ: every view that reads `motion` is redrawn
    /// on a write, even of the same value, and a streaming answer resizes line by line.
    private func use(_ next: IslandMotion) {
        if motion != next { motion = next }
    }

    /// The black body. While the capsule is out, the body rests as the bare notch.
    private func target(for presentation: IslandPresentation) -> IslandMetrics {
        switch presentation {
        case .idle, .open, .dropTarget: layout.idle
        case .peek: layout.peek
        case .compact: layout.compact(leading: compactWings.width, trailing: compactWings.height)
        }
    }

    /// The glass capsule.
    private func capsuleTarget(for presentation: IslandPresentation) -> CapsuleMetrics {
        switch presentation {
        case .open: return layout.capsule(content: capsuleContentHeight)
        case .dropTarget: return layout.dropCapsule
        case .idle, .peek, .compact:
            // Reduce Motion: it fades where it is, with no trip into the notch.
            guard reduceMotion else { return layout.tucked }
            var here = capsule
            here.shown = false
            return here
        }
    }

    private func go(
        _ next: IslandPresentation, with motion: IslandMotion, name: String, hold: Double? = nil
    ) {
        sequence?.cancel()
        sequence = nil
        apply(next, metrics: target(for: next), capsule: capsuleTarget(for: next), motion: motion, name: name)
        if case .compact = next, let hold {
            sequence = Task { [weak self] in await self?.holdThenDismiss(hold) }
        }
    }

    /// The one place state changes. Presentation, sizes and springs move in the same
    /// update, so the view never sees a new size with the old springs.
    private func apply(
        _ next: IslandPresentation, metrics: IslandMetrics, capsule: CapsuleMetrics, motion: IslandMotion,
        name: String
    ) {
        let wasOpen = presentation.isOpen
        if wasOpen && !next.isOpen { onWillClose?() }
        let chosen = reduceMotion ? IslandMotion.reduced : motion
        isCollapsingToNotch = false
        lastTransition = .now
        self.motion = chosen
        // The carrier animation only keeps outgoing content alive long enough to leave;
        // each part overrides it with its own spring.
        withAnimation(IslandMotion.spring(0.6, 1)) {
            presentation = next
            self.metrics = metrics
            self.capsule = capsule
        }
        if next.isOpen != wasOpen { onOpenChange?(next.isOpen) }
        onTransition?(name)
    }

    private func holdThenDismiss(_ hold: Double?) async {
        guard let hold else { return }
        try? await Task.sleep(for: IslandMotion.seconds(hold))
        guard !Task.isCancelled, case .compact = presentation else { return }
        sequence = nil
        let next: IslandPresentation = isHovering ? .peek : .idle
        apply(next, metrics: target(for: next), capsule: capsuleTarget(for: next), motion: .dismiss, name: "dismiss")
    }
}
