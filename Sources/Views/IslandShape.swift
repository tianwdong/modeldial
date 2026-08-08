import SwiftUI

struct IslandShape: InsettableShape {
    static let hoverShoulderRadius: CGFloat = 14
    static let expandedShoulderRadius: CGFloat = 14

    var inset: CGFloat = 0
    var topShoulderRadius: CGFloat = 0
    var bottomRadius: CGFloat = 14

    static var expanded: IslandShape {
        IslandShape(topShoulderRadius: expandedShoulderRadius, bottomRadius: 32)
    }

    static var hover: IslandShape {
        IslandShape(topShoulderRadius: hoverShoulderRadius, bottomRadius: 24)
    }

    static var hoverCap: IslandShape {
        IslandShape(topShoulderRadius: hoverShoulderRadius, bottomRadius: 0)
    }

    var animatableData: AnimatablePair<CGFloat, AnimatablePair<CGFloat, CGFloat>> {
        get { AnimatablePair(inset, AnimatablePair(topShoulderRadius, bottomRadius)) }
        set {
            inset = newValue.first
            topShoulderRadius = newValue.second.first
            bottomRadius = newValue.second.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let frame = rect.insetBy(dx: inset, dy: inset)
        let shoulderRadius = min(
            max(0, topShoulderRadius),
            max(0, min(frame.width / 4, frame.height / 4))
        )
        let bodyMinX = frame.minX + shoulderRadius
        let bodyMaxX = frame.maxX - shoulderRadius
        let lowerRadius = min(
            max(0, bottomRadius),
            max(0, min((bodyMaxX - bodyMinX) / 2, frame.height - shoulderRadius))
        )

        var path = Path()
        path.move(to: CGPoint(x: frame.minX, y: frame.minY))
        path.addLine(to: CGPoint(x: frame.maxX, y: frame.minY))
        path.addQuadCurve(
            to: CGPoint(x: bodyMaxX, y: frame.minY + shoulderRadius),
            control: CGPoint(x: bodyMaxX, y: frame.minY)
        )
        path.addLine(to: CGPoint(x: bodyMaxX, y: frame.maxY - lowerRadius))
        path.addQuadCurve(
            to: CGPoint(x: bodyMaxX - lowerRadius, y: frame.maxY),
            control: CGPoint(x: bodyMaxX, y: frame.maxY)
        )
        path.addLine(to: CGPoint(x: bodyMinX + lowerRadius, y: frame.maxY))
        path.addQuadCurve(
            to: CGPoint(x: bodyMinX, y: frame.maxY - lowerRadius),
            control: CGPoint(x: bodyMinX, y: frame.maxY)
        )
        path.addLine(to: CGPoint(x: bodyMinX, y: frame.minY + shoulderRadius))
        path.addQuadCurve(
            to: CGPoint(x: frame.minX, y: frame.minY),
            control: CGPoint(x: bodyMinX, y: frame.minY)
        )
        path.closeSubpath()
        return path
    }

    func inset(by amount: CGFloat) -> IslandShape {
        var copy = self
        copy.inset += amount
        return copy
    }
}
