import SwiftUI

extension Animation {
    static let islandOpen = Animation.spring(response: 0.30, dampingFraction: 0.86)
    static let islandClose = Animation.spring(response: 0.24, dampingFraction: 0.90)
    static let islandHover = Animation.timingCurve(0.16, 1, 0.3, 1, duration: 0.15)
    static let islandHoverContent = Animation.easeOut(duration: 0.12)
    static let islandHoverClose = Animation.easeOut(duration: 0.12)
    static let strongEaseOut = Animation.timingCurve(0.23, 1, 0.32, 1, duration: 0.28)
    static let controlSelection = Animation.timingCurve(0.16, 1, 0.3, 1, duration: 0.22)
    static let interactionFeedback = Animation.easeOut(duration: 0.09)
}

enum IslandTransitionElement: String {
    case primaryIdentity
    case candidateIdentity
    case secondaryStatus
    case qualityMetric
    case timeMetric
    case costMetric
}

extension View {
    @ViewBuilder
    func islandMatchedGeometry(
        id: String,
        in namespace: Namespace.ID,
        isSource: Bool,
        reduceMotion: Bool
    ) -> some View {
        if reduceMotion {
            self
        } else {
            matchedGeometryEffect(
                id: id,
                in: namespace,
                properties: .position,
                isSource: isSource
            )
        }
    }
}
