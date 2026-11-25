import SwiftUI

/// How the island moves. One spring per moving part, not one for the whole box.
///
/// Why separate springs: a box that scales as one piece looks like a rectangle being
/// resized. The Dynamic Island feels alive because its width, its height and its corner
/// each follow their own spring, and the corner trails the height by a few milliseconds.
///
/// Where the numbers come from: islandcn (MIT, github.com/opencoredev/islandcn) measured
/// the iPhone's island frame by frame and published each spring in SwiftUI's own terms,
/// `response` (seconds for one cycle) and `dampingFraction` (1 = no overshoot). We use the
/// numbers, not its code. Hover is ours: a phone has no pointer.
struct IslandMotion: Equatable {
    /// Width. The island is centred, so one spring moves both edges.
    var edges: Animation
    /// Height.
    var body: Animation
    /// Bottom corner radius. Starts a beat after the height so the corner "catches up".
    var corner: Animation
    /// Shadow strength. Trails the shape, so the panel lifts off after it has grown.
    var shadow: Animation
    /// Content coming in: waits until the shape is mostly there.
    var contentIn: Animation
    /// Content going out: leaves at once, fast, so the shape never shows stale text.
    var contentOut: Animation
    /// Peak blur (points) and start scale of content while it crosses over.
    var blurIn: CGFloat
    var blurOut: CGFloat
    var contentScale: CGFloat
}

extension IslandMotion {
    /// Stretches every duration and delay. 1 is real speed. `MD_MOTION_SCALE=8` in the
    /// environment gives slow motion, which is how the choreography is inspected frame
    /// by frame. Springs keep their shape when `response` is scaled.
    @MainActor static var timeScale: Double = {
        let raw = ProcessInfo.processInfo.environment["MD_MOTION_SCALE"].flatMap(Double.init)
        return max(raw ?? 1, 0.1)
    }()

    @MainActor static func spring(_ response: Double, _ damping: Double, delay: Double = 0) -> Animation {
        Animation.spring(response: response * timeScale, dampingFraction: damping)
            .delay(delay * timeScale)
    }

    /// A real-time interval scaled like the animations, for timers that sequence them.
    @MainActor static func seconds(_ value: Double) -> Duration {
        .milliseconds(Int(value * timeScale * 1000))
    }

    // MARK: Transitions

    /// Pointer arrives: a small, quick swell that says "this is alive". Slightly bouncy.
    @MainActor static var peek: IslandMotion {
        IslandMotion(
            edges: spring(0.36, 0.68), body: spring(0.36, 0.68), corner: spring(0.32, 0.8),
            shadow: spring(0.36, 1),
            contentIn: spring(0.3, 1, delay: 0.05), contentOut: spring(0.12, 1),
            blurIn: 6, blurOut: 4, contentScale: 0.85
        )
    }

    /// Pointer leaves without a click: settle back with no bounce.
    @MainActor static var unpeek: IslandMotion {
        IslandMotion(
            edges: spring(0.42, 1), body: spring(0.42, 0.97), corner: spring(0.4, 1),
            shadow: spring(0.3, 1),
            contentIn: spring(0.2, 1), contentOut: spring(0.14, 1),
            blurIn: 4, blurOut: 6, contentScale: 0.85
        )
    }

    /// Click: open into a panel. This is islandcn's "pressExpand": the fast open that
    /// follows a press, and the one a person triggers most.
    @MainActor static var open: IslandMotion {
        IslandMotion(
            edges: spring(0.387, 0.792), body: spring(0.388, 0.791),
            corner: spring(0.32, 0.831, delay: 0.019),
            shadow: spring(0.507, 0.839),
            contentIn: spring(0.36, 1, delay: 0.056), contentOut: spring(0.14, 1),
            blurIn: 12, blurOut: 10.5, contentScale: 0.9
        )
    }

    /// Panel to a small status pill ("Saved"). First leg only: the model aims at the
    /// notch, then retargets with `settle`, which gives the waist-and-rebound of a
    /// collapse instead of a plain shrink.
    @MainActor static var collapse: IslandMotion {
        IslandMotion(
            edges: spring(0.479, 0.894), body: spring(0.453, 0.928),
            corner: spring(0.449, 0.94, delay: 0.01),
            shadow: spring(0.3, 1),
            contentIn: spring(0.5, 1, delay: 0.36), contentOut: spring(0.22, 1, delay: 0.017),
            blurIn: 8.3, blurOut: 7.75, contentScale: 0.9
        )
    }

    /// Second leg of a collapse, and any in-place change of a pill's size. No bounce:
    /// the overshoot already happened in the first leg.
    @MainActor static var settle: IslandMotion {
        IslandMotion(
            edges: spring(0.447, 1), body: spring(0.447, 1), corner: spring(0.4, 1),
            shadow: spring(0.3, 1),
            contentIn: spring(0.3, 1, delay: 0.08), contentOut: spring(0.16, 1),
            blurIn: 8, blurOut: 6, contentScale: 0.9
        )
    }

    /// A pill appears from the bare notch. The one clearly bouncy move (about 8% over).
    @MainActor static var appear: IslandMotion {
        IslandMotion(
            edges: spring(0.483, 0.62), body: spring(0.483, 0.62), corner: spring(0.483, 0.62),
            shadow: spring(0.36, 1),
            contentIn: spring(0.45, 1, delay: 0.07), contentOut: spring(0.12, 1),
            blurIn: 12, blurOut: 5.5, contentScale: 0.8
        )
    }

    /// Back to the bare notch. Critically damped: a disappearing thing should not wobble.
    @MainActor static var dismiss: IslandMotion {
        IslandMotion(
            edges: spring(0.432, 1), body: spring(0.442, 0.97), corner: spring(0.44, 1),
            shadow: spring(0.3, 1),
            contentIn: spring(0.2, 1), contentOut: spring(0.2, 1),
            blurIn: 6, blurOut: 12, contentScale: 0.8
        )
    }

    /// The panel's content changed height in place (a second line of text, an error).
    @MainActor static var resize: IslandMotion {
        IslandMotion(
            edges: spring(0.5, 0.82), body: spring(0.5, 0.82), corner: spring(0.5, 0.82),
            shadow: spring(0.36, 1),
            contentIn: spring(0.3, 1, delay: 0.08), contentOut: spring(0.16, 1),
            blurIn: 8, blurOut: 6, contentScale: 0.94
        )
    }

    /// Reduce Motion is on: the shape still changes, because the change carries meaning,
    /// but it eases for 0.2 s with no overshoot, and content only cross-fades.
    @MainActor static var reduced: IslandMotion {
        let ease = Animation.easeOut(duration: 0.2 * timeScale)
        return IslandMotion(
            edges: ease, body: ease, corner: ease, shadow: ease,
            contentIn: Animation.easeIn(duration: 0.1 * timeScale).delay(0.1 * timeScale),
            contentOut: Animation.easeOut(duration: 0.1 * timeScale),
            blurIn: 0, blurOut: 0, contentScale: 1
        )
    }

    // MARK: Timings that sequence the springs

    /// When a collapse stops aiming at the notch and aims at the pill.
    static let collapseRetarget = 0.311
    /// How long a "Saved" or "Ready" pill stays before it folds away.
    static let confirmationHold = 1.25
    /// How long the pointer may be away before a peek settles back (stops flapping at the edge).
    static let hoverExitGrace = 0.12
}
