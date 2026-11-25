import Observation
import SwiftUI

/// The open panel's two modes. Capture saves; Ask answers from what was saved.
enum PanelMode: String, CaseIterable, Identifiable {
    case capture
    case ask

    var id: String { rawValue }

    var title: String {
        switch self {
        case .capture: "Capture"
        case .ask: "Ask"
        }
    }
}

/// What the open panel shows: which mode, and the two models behind them. It outlives
/// the panel, so a reopened panel is in the mode you left, with the answer you left.
@MainActor
@Observable
final class PanelModel {
    private(set) var mode: PanelMode = .capture
    let capture: CaptureModel
    let ask: AskModel
    private let island: IslandModel

    init(capture: CaptureModel, ask: AskModel, island: IslandModel) {
        self.capture = capture
        self.ask = ask
        self.island = island
    }

    func select(_ next: PanelMode) {
        // Only an open panel switches mode (see `AskModel.submit`).
        guard island.presentation.isPanel, next != mode else { return }
        island.panelContentWillChange(next == .ask ? "mode-ask" : "mode-capture")
        // One transaction: the indicator slides, the old mode's content blurs out, the
        // new one comes in, and the height follows, all from the same moment.
        let spring = island.reduceMotion ? IslandMotion.reduced.body : IslandMotion.spring(0.36, 0.82)
        withAnimation(spring) { mode = next }
    }

    /// Tab: the other mode.
    func toggleMode() {
        select(mode == .capture ? .ask : .capture)
    }

    /// A drop that could not be saved: open in Capture with its words in the box and the
    /// reason under them. The panel is closed here, so the mode changes without a slide.
    func openCapture(keeping text: String, error: String) {
        guard !island.presentation.isPanel else { return }
        mode = .capture
        capture.keepForRetry(text, error: error)
        island.open()
    }

    /// Built once at launch, out of sight, in Ask mode with a finished answer.
    static func rehearsal(island: IslandModel) -> PanelModel {
        let ask = AskModel(source: APIClient.local, island: island)
        ask.fillForRehearsal()
        let panel = PanelModel(capture: CaptureModel(api: .local, island: island), ask: ask, island: island)
        panel.mode = .ask
        return panel
    }
}
