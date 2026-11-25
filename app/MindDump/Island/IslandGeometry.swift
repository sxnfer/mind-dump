import SwiftUI

/// The size of the island's black body in one presentation, in points.
///
/// The body hangs from the top edge of the screen and is centred on the notch. `shoulder`
/// is the small concave curve where the body meets the screen's top edge; it sits outside
/// `width`, like the flare at the top of the hardware notch.
struct IslandMetrics: Equatable {
    var width: CGFloat
    var height: CGFloat
    /// Bottom corner radius (continuous curvature).
    var corner: CGFloat
    /// Radius of the concave top curves. 0 at rest: the hardware notch has its own.
    var shoulder: CGFloat
    /// 0...1. The drop shadow under the body; only a panel casts one.
    var shadow: Double
    /// 0...1. A faint outline that separates black from a dark window behind it.
    var keyline: Double
}

/// Turns "which presentation" into sizes, from the one thing measured on the Mac: the notch.
struct IslandLayout: Equatable {
    /// The notch on this display (220 x 38 on a 16-inch MacBook Pro). On a display with
    /// no notch this is a pill of menu bar height.
    var notch: CGSize
    var hasNotch: Bool

    static let fallback = IslandLayout(notch: CGSize(width: 180, height: 32), hasNotch: false)

    /// Panel width. Wide enough for a sentence, narrow enough to stay "at the notch".
    static let panelWidth: CGFloat = 468
    /// Space inside the panel, from its edge to its content.
    static let panelPadding: CGFloat = 18
    /// The widest a pill's wing may grow on either side of the notch.
    static let maxWing: CGFloat = 132
    /// The largest body the window must hold, plus room for the shadow.
    static let maxPanelHeight: CGFloat = 420
    static let shadowRoom: CGFloat = 96

    /// The bare notch: invisible, because black sits on the black hardware cutout. It is
    /// drawn slightly smaller than the cutout, so a sub-pixel mismatch can never show as
    /// a black sliver against a bright menu bar.
    var idle: IslandMetrics {
        IslandMetrics(
            width: notch.width - (hasNotch ? 4 : 0), height: notch.height - (hasNotch ? 2 : 0),
            corner: hasNotch ? 9 : notch.height / 2, shoulder: 0, shadow: 0, keyline: 0
        )
    }

    /// Pointer over the notch: a little wider and deeper, enough to notice.
    var peek: IslandMetrics {
        IslandMetrics(
            width: notch.width + 2 * Self.peekWing, height: notch.height + 6,
            corner: 14, shoulder: 6, shadow: 0.25, keyline: 0.7
        )
    }

    static let peekWing: CGFloat = 30

    /// A status pill: the notch plus a wing on each side sized to its content. The two
    /// wings are kept equal so the pill stays centred on the notch.
    func compact(leading: CGFloat, trailing: CGFloat) -> IslandMetrics {
        let wing = min(max(leading, trailing), Self.maxWing)
        return IslandMetrics(
            width: notch.width + 2 * wing, height: notch.height,
            corner: hasNotch ? 12 : notch.height / 2, shoulder: 5, shadow: 0, keyline: 0.7
        )
    }

    /// The open panel. `content` is the height of what sits below the notch band.
    func panel(content: CGFloat) -> IslandMetrics {
        let height = min(notch.height + content, Self.maxPanelHeight)
        return IslandMetrics(
            width: Self.panelWidth, height: height,
            corner: 30, shoulder: 10, shadow: 1, keyline: 1
        )
    }

    /// Width of the band beside the notch inside a body of this width. Content placed
    /// there sits level with the menu bar, left and right of the cutout.
    func wing(in width: CGFloat) -> CGFloat {
        max((width - notch.width) / 2, 0)
    }
}

/// The body: square at the top (it meets the screen edge), continuous curves at the bottom.
struct IslandBody: InsettableShape {
    var corner: CGFloat
    /// Pulls the sides and bottom in, for a stroke drawn inside the edge. The top stays.
    var inset: CGFloat = 0

    var animatableData: CGFloat {
        get { corner }
        set { corner = newValue }
    }

    func inset(by amount: CGFloat) -> IslandBody {
        var copy = self
        copy.inset += amount
        return copy
    }

    func path(in rect: CGRect) -> Path {
        let rect = CGRect(
            x: rect.minX + inset, y: rect.minY,
            width: max(rect.width - 2 * inset, 0), height: max(rect.height - inset, 0)
        )
        let radius = max(min(corner - inset, rect.height, rect.width / 2), 0)
        return UnevenRoundedRectangle(
            cornerRadii: .init(bottomLeading: radius, bottomTrailing: radius),
            style: .continuous
        ).path(in: rect)
    }
}

/// One concave top curve: the fillet between the screen's top edge and the body's side.
/// Drawn in a square of side `radius`; `leading` is the one on the body's left.
struct IslandShoulder: Shape {
    var leading: Bool

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard rect.width > 0.01 else { return path }
        if leading {
            // From the outer top point, curve down to the body's side, then square off.
            path.move(to: CGPoint(x: rect.minX, y: rect.minY))
            path.addArc(
                center: CGPoint(x: rect.minX, y: rect.maxY), radius: rect.width,
                startAngle: .degrees(-90), endAngle: .degrees(0), clockwise: false
            )
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        } else {
            path.move(to: CGPoint(x: rect.maxX, y: rect.minY))
            path.addArc(
                center: CGPoint(x: rect.maxX, y: rect.maxY), radius: rect.width,
                startAngle: .degrees(-90), endAngle: .degrees(180), clockwise: true
            )
            path.addLine(to: CGPoint(x: rect.minX, y: rect.minY))
        }
        path.closeSubpath()
        return path
    }
}
