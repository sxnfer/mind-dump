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
    /// Shadow strength. Trails the shape, so a grown body lifts off after it has grown.
    var shadow: Animation
    /// Content coming in: waits until the shape is mostly there.
    var contentIn: Animation
    /// Content going out: leaves at once, fast, so the shape never shows stale text.
    var contentOut: Animation
    /// Peak blur (points) and start scale of content while it crosses over.
    var blurIn: CGFloat
    var blurOut: CGFloat
    var contentScale: CGFloat
    /// Peak blur (points) of a pill's glyph and words while the pill changes in place
    /// ("Saved" to "Reading page"): the words re-form while the width moves, so they never
    /// visibly slide or stretch. 0 is no blur at all (calm, Reduce Motion).
    var morphBlur: CGFloat = 0
    /// The glass capsule's springs on its way back into the notch, in place of `edges` and
    /// `body`; nil uses those. Its overshoot on the way in would happen inside the notch,
    /// where the mask hides it, so a bouncy tuck shows nothing more and keeps the glass
    /// drawing longer: measured, closes in drastic had 9 and 14 late frames in three runs
    /// against calm's 6, all in the hidden tail. A critically damped spring as quick to the
    /// edge of the notch (90% of the way in about 0.21 s, as the bouncy one) looks the same.
    var tuck: Animation? = nil
    /// Reduce Motion: the glass fades where it is instead of travelling.
    var isReduced = false
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

    // MARK: Tunings

    /// Three complete sets of springs, so the feel can be judged by eye and picked.
    ///
    /// - `calm`: the slice 1 values, islandcn's measurements of the iPhone. Most moves are
    ///   critically damped (no overshoot); only a pill appearing bounces (about 8%).
    /// - `springy`: every shape change overshoots a little (about 2 to 6%) and settles,
    ///   on springs about 10% longer; blur on crossing content roughly doubled.
    /// - `drastic`: every shape change pushes clearly past its target (about 7 to 15%) and
    ///   eases back, on springs 20 to 30% longer so the bounce reads as mass, not jitter;
    ///   blur peaks of 14 to 24 points, so words melt rather than cut.
    ///
    /// Only the shape bounces. Content (opacity, scale, blur) always rides a critically
    /// damped spring: a scale that overshot would push glyphs into the body's edge, and an
    /// opacity over 1 means nothing. Shadows too: a shadow that bounced would flicker.
    ///
    /// Overshoot of a spring with damping fraction z is exp(-z * pi / sqrt(1 - z^2)): 0.62
    /// gives 8%, 0.7 gives 5%, 0.76 gives 2.5%. The numbers below were simulated first
    /// (time to the peak, time to settle within 1%) and then looked at in slow-motion films.
    enum Tuning: String, CaseIterable {
        case calm, springy, drastic
    }

    /// San's pick. Change this one line to change the default.
    static let defaultTuning: Tuning = .springy

    /// The tuning in use. `MD_MOTION_TUNING=calm|springy|drastic` at launch; debug builds can
    /// switch it while running (Darwin notification `…debug.tuning-<name>`, or the script verb
    /// `tuning <name>`). It takes effect from the next transition: a move already under way
    /// keeps the springs it started with.
    @MainActor static var tuning: Tuning = {
        let raw = ProcessInfo.processInfo.environment["MD_MOTION_TUNING"]
        return raw.flatMap(Tuning.init(rawValue:)) ?? defaultTuning
    }()

    #if DEBUG
    /// Switch tunings while the app runs (debug builds), and say so in the log.
    @MainActor static func switchTuning(to name: String) {
        guard let next = Tuning(rawValue: name) else {
            print("[motion] unknown tuning \(name): calm, springy or drastic")
            fflush(stdout)
            return
        }
        tuning = next
        print("[motion] tuning \(next.rawValue)")
        fflush(stdout)
    }
    #endif

    // MARK: Transitions

    /// Pointer arrives: a small, quick swell that says "this is alive". Slightly bouncy.
    @MainActor static var peek: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.36, 0.68), body: spring(0.36, 0.68), corner: spring(0.32, 0.8),
                shadow: spring(0.36, 1),
                contentIn: spring(0.3, 1, delay: 0.05), contentOut: spring(0.12, 1),
                blurIn: 6, blurOut: 4, contentScale: 0.85
            )
        case .springy:
            IslandMotion(
                edges: spring(0.4, 0.64), body: spring(0.4, 0.66), corner: spring(0.36, 0.74),
                shadow: spring(0.4, 1),
                contentIn: spring(0.32, 1, delay: 0.06), contentOut: spring(0.14, 1),
                blurIn: 12, blurOut: 8, contentScale: 0.82
            )
        case .drastic:
            // The hover is the move made most often, so it bounces but stays quick.
            IslandMotion(
                edges: spring(0.48, 0.55), body: spring(0.48, 0.57), corner: spring(0.44, 0.62),
                shadow: spring(0.45, 1),
                contentIn: spring(0.38, 1, delay: 0.07), contentOut: spring(0.16, 1),
                blurIn: 16, blurOut: 10, contentScale: 0.78
            )
        }
    }

    /// Pointer leaves without a click: settle back. Calm has no bounce; the others undershoot
    /// a little, which happens inside the hardware notch, where black on black cannot be seen.
    @MainActor static var unpeek: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.42, 1), body: spring(0.42, 0.97), corner: spring(0.4, 1),
                shadow: spring(0.3, 1),
                contentIn: spring(0.2, 1), contentOut: spring(0.14, 1),
                blurIn: 4, blurOut: 6, contentScale: 0.85
            )
        case .springy:
            IslandMotion(
                edges: spring(0.46, 0.74), body: spring(0.46, 0.76), corner: spring(0.44, 0.8),
                shadow: spring(0.32, 1),
                contentIn: spring(0.22, 1), contentOut: spring(0.16, 1),
                blurIn: 8, blurOut: 12, contentScale: 0.82
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.52, 0.62), body: spring(0.52, 0.64), corner: spring(0.5, 0.68),
                shadow: spring(0.36, 1),
                contentIn: spring(0.24, 1), contentOut: spring(0.2, 1),
                blurIn: 10, blurOut: 16, contentScale: 0.78
            )
        }
    }

    /// A drag carrying text arrives: the capsule comes out as the target. A touch slower
    /// than a peek, with a small overshoot that says "yes, here". Leaving uses `dismiss`
    /// (or `settle` back to a running pill); a release goes in through `collapse`.
    @MainActor static var target: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.4, 0.74), body: spring(0.4, 0.74), corner: spring(0.36, 0.82, delay: 0.015),
                shadow: spring(0.45, 1),
                contentIn: spring(0.32, 1, delay: 0.06), contentOut: spring(0.12, 1),
                blurIn: 8, blurOut: 5, contentScale: 0.88
            )
        case .springy:
            IslandMotion(
                edges: spring(0.44, 0.68), body: spring(0.44, 0.68), corner: spring(0.4, 0.76, delay: 0.015),
                shadow: spring(0.48, 1),
                contentIn: spring(0.36, 1, delay: 0.07), contentOut: spring(0.14, 1),
                blurIn: 16, blurOut: 10, contentScale: 0.86
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.54, 0.56), body: spring(0.54, 0.58), corner: spring(0.5, 0.64, delay: 0.02),
                shadow: spring(0.55, 1),
                contentIn: spring(0.44, 1, delay: 0.09), contentOut: spring(0.16, 1),
                blurIn: 22, blurOut: 14, contentScale: 0.82
            )
        }
    }

    /// Click: the capsule comes out of the notch. This is islandcn's "pressExpand": the fast open that
    /// follows a press, and the one a person triggers most.
    @MainActor static var open: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.387, 0.792), body: spring(0.388, 0.791),
                corner: spring(0.32, 0.831, delay: 0.019),
                shadow: spring(0.507, 0.839),
                contentIn: spring(0.36, 1, delay: 0.056), contentOut: spring(0.14, 1),
                blurIn: 12, blurOut: 10.5, contentScale: 0.9
            )
        case .springy:
            IslandMotion(
                edges: spring(0.42, 0.72), body: spring(0.42, 0.74),
                corner: spring(0.36, 0.78, delay: 0.02),
                shadow: spring(0.52, 0.9),
                contentIn: spring(0.38, 1, delay: 0.06), contentOut: spring(0.16, 1),
                blurIn: 18, blurOut: 16, contentScale: 0.88
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.5, 0.6), body: spring(0.5, 0.62),
                corner: spring(0.44, 0.68, delay: 0.025),
                shadow: spring(0.6, 1),
                contentIn: spring(0.46, 1, delay: 0.08), contentOut: spring(0.18, 1),
                blurIn: 22, blurOut: 18, contentScale: 0.84
            )
        }
    }

    /// The capsule to a small status pill ("Saved"). First leg only: the capsule goes back
    /// into the notch while the body waits there, then the model retargets with `settle`
    /// and the body opens out to the pill: swallowed, then answered.
    ///
    /// The retarget stays at `collapseRetarget` (0.311 s) in every tuning: the bouncier
    /// springs reach 90% of the way sooner (about 0.22 s against 0.26 s), so the capsule is
    /// further in by then, and its overshoot goes up into the notch, out of sight.
    @MainActor static var collapse: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.479, 0.894), body: spring(0.453, 0.928),
                corner: spring(0.449, 0.94, delay: 0.01),
                shadow: spring(0.3, 1),
                contentIn: spring(0.5, 1, delay: 0.36), contentOut: spring(0.22, 1, delay: 0.017),
                blurIn: 8.3, blurOut: 7.75, contentScale: 0.9
            )
        case .springy:
            IslandMotion(
                edges: spring(0.5, 0.76), body: spring(0.48, 0.78),
                corner: spring(0.47, 0.82, delay: 0.01),
                shadow: spring(0.3, 1),
                contentIn: spring(0.5, 1, delay: 0.38), contentOut: spring(0.24, 1, delay: 0.017),
                blurIn: 16, blurOut: 14, contentScale: 0.88, tuck: spring(0.36, 1)
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.56, 0.62), body: spring(0.54, 0.64),
                corner: spring(0.52, 0.7, delay: 0.015),
                shadow: spring(0.3, 1),
                contentIn: spring(0.54, 1, delay: 0.42), contentOut: spring(0.24, 1, delay: 0.02),
                blurIn: 22, blurOut: 18, contentScale: 0.84, tuck: spring(0.36, 1)
            )
        }
    }

    /// Second leg of a collapse, and any in-place change of a pill's size. Calm has no
    /// bounce (the overshoot already happened in the first leg); the others let the body
    /// spring out to the pill, which is the part of a save the user actually sees.
    @MainActor static var settle: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.447, 1), body: spring(0.447, 1), corner: spring(0.4, 1),
                shadow: spring(0.3, 1),
                contentIn: spring(0.3, 1, delay: 0.08), contentOut: spring(0.16, 1),
                blurIn: 8, blurOut: 6, contentScale: 0.9
            )
        case .springy:
            IslandMotion(
                edges: spring(0.48, 0.72), body: spring(0.48, 0.74), corner: spring(0.44, 0.8),
                shadow: spring(0.3, 1),
                contentIn: spring(0.34, 1, delay: 0.09), contentOut: spring(0.18, 1),
                blurIn: 14, blurOut: 12, contentScale: 0.88, morphBlur: 5
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.56, 0.58), body: spring(0.56, 0.6), corner: spring(0.52, 0.66),
                shadow: spring(0.3, 1),
                contentIn: spring(0.42, 1, delay: 0.1), contentOut: spring(0.2, 1),
                blurIn: 20, blurOut: 16, contentScale: 0.84, morphBlur: 9
            )
        }
    }

    /// A pill appears from the bare notch. In calm the one clearly bouncy move (about 8%
    /// over); in the others the bounciest one.
    @MainActor static var appear: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.483, 0.62), body: spring(0.483, 0.62), corner: spring(0.483, 0.62),
                shadow: spring(0.36, 1),
                contentIn: spring(0.45, 1, delay: 0.07), contentOut: spring(0.12, 1),
                blurIn: 12, blurOut: 5.5, contentScale: 0.8
            )
        case .springy:
            IslandMotion(
                edges: spring(0.52, 0.58), body: spring(0.52, 0.6), corner: spring(0.52, 0.64),
                shadow: spring(0.4, 1),
                contentIn: spring(0.46, 1, delay: 0.08), contentOut: spring(0.14, 1),
                blurIn: 20, blurOut: 10, contentScale: 0.78
            )
        case .drastic:
            // About 15% over: a 120-point pill pushes 18 points past and comes back. The
            // words come in late enough (peak at about 0.35 s) to sharpen as it settles.
            IslandMotion(
                edges: spring(0.6, 0.52), body: spring(0.6, 0.54), corner: spring(0.6, 0.58),
                shadow: spring(0.42, 1),
                contentIn: spring(0.5, 1, delay: 0.1), contentOut: spring(0.16, 1),
                blurIn: 24, blurOut: 14, contentScale: 0.72
            )
        }
    }

    /// Back to the bare notch. Calm is critically damped. The others overshoot inwards:
    /// the body dips narrower than the notch for a moment, inside the black hardware cutout
    /// where it cannot be seen, so what shows is a quicker, more decided snap home.
    @MainActor static var dismiss: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.432, 1), body: spring(0.442, 0.97), corner: spring(0.44, 1),
                shadow: spring(0.3, 1),
                contentIn: spring(0.2, 1), contentOut: spring(0.2, 1),
                blurIn: 6, blurOut: 12, contentScale: 0.8
            )
        case .springy:
            IslandMotion(
                edges: spring(0.46, 0.76), body: spring(0.47, 0.78), corner: spring(0.46, 0.82),
                shadow: spring(0.3, 1),
                contentIn: spring(0.22, 1), contentOut: spring(0.22, 1),
                blurIn: 10, blurOut: 20, contentScale: 0.78, tuck: spring(0.34, 1)
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.54, 0.62), body: spring(0.55, 0.64), corner: spring(0.54, 0.7),
                shadow: spring(0.3, 1),
                contentIn: spring(0.24, 1), contentOut: spring(0.26, 1),
                blurIn: 14, blurOut: 24, contentScale: 0.72, tuck: spring(0.34, 1)
            )
        }
    }

    /// The capsule's content changed height in place (an error line, the answer card).
    /// Kept the least bouncy in every tuning: a tall card that overshot would run past the
    /// window, and it changes while the user is reading.
    @MainActor static var resize: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.5, 0.82), body: spring(0.5, 0.82), corner: spring(0.5, 0.82),
                shadow: spring(0.36, 1),
                contentIn: spring(0.3, 1, delay: 0.08), contentOut: spring(0.16, 1),
                blurIn: 8, blurOut: 6, contentScale: 0.94
            )
        case .springy:
            IslandMotion(
                edges: spring(0.52, 0.76), body: spring(0.52, 0.76), corner: spring(0.52, 0.76),
                shadow: spring(0.36, 1),
                contentIn: spring(0.32, 1, delay: 0.08), contentOut: spring(0.16, 1),
                blurIn: 12, blurOut: 10, contentScale: 0.94
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.58, 0.68), body: spring(0.58, 0.68), corner: spring(0.58, 0.68),
                shadow: spring(0.36, 1),
                contentIn: spring(0.34, 1, delay: 0.09), contentOut: spring(0.18, 1),
                blurIn: 16, blurOut: 12, contentScale: 0.92
            )
        }
    }

    /// Reduce Motion is on: the shape still changes, because the change carries meaning,
    /// but it eases for 0.2 s with no overshoot, and content only cross-fades.
    @MainActor static var reduced: IslandMotion {
        let ease = Animation.easeOut(duration: 0.2 * timeScale)
        return IslandMotion(
            edges: ease, body: ease, corner: ease, shadow: ease,
            contentIn: Animation.easeIn(duration: 0.1 * timeScale).delay(0.1 * timeScale),
            contentOut: Animation.easeOut(duration: 0.1 * timeScale),
            blurIn: 0, blurOut: 0, contentScale: 1, isReduced: true
        )
    }

    /// The glass capsule turns on at once: it starts tucked inside the notch, behind the
    /// black body, so there is nothing to fade; the springs bring it out.
    @MainActor var glassIn: Animation {
        isReduced ? .easeOut(duration: 0.2 * Self.timeScale) : .easeOut(duration: 0.05 * Self.timeScale)
    }

    /// It turns off only once it is back inside the notch, so it never fades in view; then
    /// the glass stops drawing.
    @MainActor var glassOut: Animation {
        isReduced
            ? .easeOut(duration: 0.2 * Self.timeScale)
            : .easeIn(duration: 0.08 * Self.timeScale).delay(0.26 * Self.timeScale)
    }

    /// The most blur content inside the glass capsule gets, in any tuning.
    ///
    /// Why a limit: blurring a layer inside the glass costs far more than blurring the
    /// pill's few words on black. Measured on a 120 Hz display, three runs per tuning in
    /// turns, before this limit: with blur peaks of 18 to 24 points in the glass, a drop
    /// release went from 1 late frame in 3 runs (calm) to 6, the second leg after it from 0
    /// to 4, and closes from 6 to 9 or 11. 12 is the most calm itself uses in the glass (its
    /// dismiss), so calm is unchanged and the others cost no more than calm there. The black
    /// body's content keeps the full blur of each tuning.
    static let glassBlurLimit: CGFloat = 12

    /// These springs for content inside the glass: the same, with the blur held to
    /// `glassBlurLimit`.
    var inGlass: IslandMotion {
        var motion = self
        motion.blurIn = min(blurIn, Self.glassBlurLimit)
        motion.blurOut = min(blurOut, Self.glassBlurLimit)
        return motion
    }

    // MARK: Timings that sequence the springs

    /// When a collapse stops aiming at the notch and aims at the pill.
    static let collapseRetarget = 0.311
    /// While an answer streams, the card grows in steps of this many points (about three
    /// lines of the answer), not line by line (see `IslandModel.growsInSteps`).
    static let answerGrowthStep: CGFloat = 60
    /// How long a "Saved" or "Ready" pill stays before it folds away.
    static let confirmationHold = 1.25
    /// How long the pointer may be away before a peek settles back (stops flapping at the edge).
    static let hoverExitGrace = 0.12
}
