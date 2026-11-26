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
    /// Reduce Motion: a plain fade, with no scale or blur.
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
    /// - `springy` (the default): a pill appearing overshoots a little (about 2.5%) and
    ///   settles; nothing else overshoots: a pill leaving, changing words, both legs of a
    ///   save, and the hover (slice 9: overshoot is for a move that arrives, and the moves
    ///   made most often get the least). Blur on crossing content about doubled.
    /// - `drastic`: every shape change pushes clearly past its target (about 7 to 15%) and
    ///   eases back, on springs 20 to 30% longer so the bounce reads as mass, not jitter;
    ///   blur peaks of 14 to 24 points, so words melt rather than cut.
    ///
    /// The glass capsule is the same in all three: it appears and leaves in place
    /// (`capsuleIn`, `capsuleOut`). Only the black body's springs differ.
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
            // Slice 9: no visible bounce (0.9 overshoots 0.15%, under a pixel), less blur
            // and scale. The hover is made many times a day.
            IslandMotion(
                edges: spring(0.36, 0.9), body: spring(0.36, 0.92), corner: spring(0.34, 0.95),
                shadow: spring(0.4, 1),
                contentIn: spring(0.3, 1, delay: 0.05), contentOut: spring(0.14, 1),
                blurIn: 8, blurOut: 6, contentScale: 0.9
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

    /// Pointer leaves without a click: settle back. Calm and springy have no bounce; drastic
    /// undershoots a little, inside the hardware notch, where black on black cannot be seen.
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
                edges: spring(0.4, 1), body: spring(0.4, 1), corner: spring(0.38, 1),
                shadow: spring(0.3, 1),
                contentIn: spring(0.2, 1), contentOut: spring(0.14, 1),
                blurIn: 6, blurOut: 8, contentScale: 0.9
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

    /// A drag carrying text arrives: the capsule appears in place as the target
    /// (`capsuleIn`; these springs move the body, which rests as the bare notch). Leaving
    /// uses `dismiss` (or `settle` back to a running pill); a release goes through `collapse`.
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

    /// Click or shortcut: the capsule appears in place (`capsuleIn`). These springs move
    /// the body back to the bare notch (from the hover, say), and resize the capsule if it
    /// was already showing as the drop target. From islandcn's "pressExpand".
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

    /// The capsule to a small status pill ("Saved"). First leg only: the capsule starts to
    /// fade out in place (`capsuleOut`) while the body waits at the notch; at
    /// `collapseRetarget` the model retargets with `settle` and the body opens out to the
    /// pill. The pill's words wait for the body (`contentIn`'s delay counts from the first
    /// leg, so it is the retarget plus the settle's own delay).
    @MainActor static var collapse: IslandMotion {
        switch tuning {
        case .calm:
            IslandMotion(
                edges: spring(0.479, 0.894), body: spring(0.453, 0.928),
                corner: spring(0.449, 0.94, delay: 0.01),
                shadow: spring(0.3, 1),
                contentIn: spring(0.5, 1, delay: 0.15), contentOut: spring(0.22, 1, delay: 0.017),
                blurIn: 8.3, blurOut: 7.75, contentScale: 0.9
            )
        case .springy:
            IslandMotion(
                edges: spring(0.48, 1), body: spring(0.48, 1),
                corner: spring(0.46, 1, delay: 0.01),
                shadow: spring(0.3, 1),
                contentIn: spring(0.5, 1, delay: 0.17), contentOut: spring(0.24, 1, delay: 0.017),
                blurIn: 16, blurOut: 14, contentScale: 0.88
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.56, 0.62), body: spring(0.54, 0.64),
                corner: spring(0.52, 0.7, delay: 0.015),
                shadow: spring(0.3, 1),
                contentIn: spring(0.54, 1, delay: 0.21), contentOut: spring(0.24, 1, delay: 0.02),
                blurIn: 22, blurOut: 18, contentScale: 0.84
            )
        }
    }

    /// Second leg of a collapse, and any in-place change of a pill's size. Calm and springy
    /// have no bounce (a pill changing words, or answering a save, is not a gesture with
    /// momentum); drastic lets the body spring out to the pill.
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
                edges: spring(0.46, 1), body: spring(0.46, 1), corner: spring(0.42, 1),
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
    /// over); in springy the only one that overshoots, by about 2.5%; in drastic 15%.
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
                edges: spring(0.5, 0.76), body: spring(0.5, 0.78), corner: spring(0.5, 0.82),
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

    /// Back to the bare notch. Calm and springy are critically damped. Drastic overshoots
    /// inwards: the body dips narrower than the notch for a moment, inside the black
    /// hardware cutout where it cannot be seen, so what shows is a more decided snap home.
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
                edges: spring(0.44, 1), body: spring(0.45, 1), corner: spring(0.44, 1),
                shadow: spring(0.3, 1),
                contentIn: spring(0.22, 1), contentOut: spring(0.22, 1),
                blurIn: 10, blurOut: 20, contentScale: 0.78
            )
        case .drastic:
            IslandMotion(
                edges: spring(0.54, 0.62), body: spring(0.55, 0.64), corner: spring(0.54, 0.7),
                shadow: spring(0.3, 1),
                contentIn: spring(0.24, 1), contentOut: spring(0.26, 1),
                blurIn: 14, blurOut: 24, contentScale: 0.72
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

    // MARK: The glass capsule

    /// The capsule appears in place, just under the notch: it fades in from 96% of its
    /// size, scaled from its top centre (so its top edge stays where it rests), while its
    /// content sharpens from a light blur. No overshoot: a tap or a key press has no
    /// momentum to carry past. The same in every tuning, and from the bare notch or the
    /// hover alike. A critically damped spring of 0.3 s is within 1% of rest at 0.32 s,
    /// and a second press retargets it from wherever it has got to.
    ///
    /// Why in place (slice 9): it used to slide out from behind the notch, hidden above
    /// the notch's lower edge by a straight mask. From the bare notch the glass was far
    /// wider than the notch as it crossed that line, so it looked cut off by nothing.
    @MainActor var capsuleIn: Animation {
        isReduced ? .easeOut(duration: 0.2 * Self.timeScale) : Self.spring(0.3, 1)
    }

    /// It leaves faster than it came: within 1% of gone by 0.21 s.
    @MainActor var capsuleOut: Animation {
        isReduced ? .easeOut(duration: 0.2 * Self.timeScale) : Self.spring(0.2, 1)
    }

    /// Hidden, the capsule is this much of its size (scaled from its top centre).
    var capsuleScale: CGFloat { isReduced ? 1 : 0.96 }

    /// Blur (points) on the capsule's content while hidden; 0 at rest. Light on purpose:
    /// blurring a layer inside the glass costs far more than blurring a pill's words on
    /// black (slice 8 measured 18 to 24 points there: a drop release went from 1 late frame
    /// in three runs to 6, closes from 6 to 9 or 11; 12 cost no more than calm). The glass
    /// itself is not blurred, only faded and scaled.
    var capsuleBlur: CGFloat { isReduced ? 0 : 8 }

    // MARK: Timings that sequence the springs

    /// When a collapse's body stops waiting at the notch and opens out to the pill. By
    /// then the capsule's fade out is about 70% done (`capsuleOut`), so the pill clearly
    /// answers the capsule leaving, without a pause between them. (Slice 8: 0.311 s, the
    /// time the capsule took to slide back into the notch.)
    static let collapseRetarget = 0.1
    /// While an answer streams, the card grows in steps of this many points (about three
    /// lines of the answer), not line by line (see `IslandModel.growsInSteps`).
    static let answerGrowthStep: CGFloat = 60
    /// How long a "Saved" or "Ready" pill stays before it folds away.
    static let confirmationHold = 1.25
    /// How long the pointer may be away before a peek settles back (stops flapping at the edge).
    static let hoverExitGrace = 0.12
}
