import AppKit
import SwiftUI

enum IslandActionKind {
    case primary
    case secondary
    case danger
}

struct IslandActionButtonStyle: ButtonStyle {
    let kind: IslandActionKind

    init(_ kind: IslandActionKind = .secondary) {
        self.kind = kind
    }

    func makeBody(configuration: Configuration) -> Body {
        Body(configuration: configuration, kind: kind)
    }

    struct Body: View {
        let configuration: ButtonStyle.Configuration
        let kind: IslandActionKind

        @Environment(\.isEnabled) private var isEnabled
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @State private var hovered = false
        @State private var cursorIsPushed = false

        var body: some View {
            configuration.label
                .font(Typography.button)
                .foregroundStyle(foregroundColor)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .fill(backgroundColor)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .strokeBorder(borderColor, lineWidth: 0.5)
                )
                .scaleEffect(configuration.isPressed ? 0.98 : 1)
                .opacity(configuration.isPressed ? 0.84 : 1)
                .animation(reduceMotion ? nil : .interactionFeedback, value: hovered)
                .animation(reduceMotion ? nil : .interactionFeedback, value: configuration.isPressed)
                .onHover { hovering in
                    hovered = hovering
                    updateCursor(hovering: hovering)
                }
                .onChange(of: isEnabled) { enabled in
                    if !enabled {
                        updateCursor(hovering: false)
                    }
                }
                .onDisappear {
                    updateCursor(hovering: false)
                }
        }

        private var foregroundColor: Color {
            guard isEnabled else { return IslandVisual.hintText }
            if kind == .primary { return IslandVisual.primaryActionText }
            return hovered ? IslandVisual.primaryText : IslandVisual.secondaryText
        }

        private var backgroundColor: Color {
            guard isEnabled else { return IslandVisual.surfaceSubtle }
            switch kind {
            case .primary:
                return IslandColor.interaction.opacity(hovered ? 1 : 0.9)
            case .secondary:
                return hovered ? IslandVisual.surfaceRaised : IslandVisual.surfaceSubtle
            case .danger:
                return IslandColor.alertRed.opacity(hovered ? 0.22 : 0.12)
            }
        }

        private var borderColor: Color {
            guard isEnabled else { return IslandVisual.hairline }
            switch kind {
            case .primary:
                return IslandVisual.selectedBorder.opacity(hovered ? 1 : 0.82)
            case .secondary:
                return hovered ? Color.white.opacity(0.13) : IslandVisual.hairline
            case .danger:
                return IslandColor.alertRed.opacity(hovered ? 0.5 : 0.3)
            }
        }

        private func updateCursor(hovering: Bool) {
            if hovering && isEnabled && !cursorIsPushed {
                NSCursor.pointingHand.push()
                cursorIsPushed = true
            } else if cursorIsPushed && (!hovering || !isEnabled) {
                NSCursor.pop()
                cursorIsPushed = false
            }
        }
    }
}

struct IslandIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> Body {
        Body(configuration: configuration)
    }

    struct Body: View {
        let configuration: ButtonStyle.Configuration

        @Environment(\.isEnabled) private var isEnabled
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @State private var hovered = false
        @State private var cursorIsPushed = false

        var body: some View {
            configuration.label
                .font(Typography.button)
                .foregroundStyle(foregroundColor)
                .frame(width: 28, height: 28)
                .background {
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .fill(hovered ? IslandVisual.surfaceRaised : Color.clear)
                }
                .overlay {
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .strokeBorder(hovered ? IslandVisual.hairline : Color.clear, lineWidth: 0.5)
                }
                .contentShape(RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous))
                .scaleEffect(configuration.isPressed && !reduceMotion ? 0.96 : 1)
                .opacity(configuration.isPressed ? 0.82 : 1)
                .animation(reduceMotion ? nil : .interactionFeedback, value: hovered)
                .animation(reduceMotion ? nil : .interactionFeedback, value: configuration.isPressed)
                .onHover { hovering in
                    hovered = hovering
                    updateCursor(hovering: hovering)
                }
                .onChange(of: isEnabled) { enabled in
                    if !enabled {
                        updateCursor(hovering: false)
                    }
                }
                .onDisappear {
                    updateCursor(hovering: false)
                }
        }

        private var foregroundColor: Color {
            guard isEnabled else { return IslandVisual.hintText }
            return hovered ? IslandVisual.secondaryText : IslandVisual.tertiaryText
        }

        private func updateCursor(hovering: Bool) {
            if hovering && isEnabled && !cursorIsPushed {
                NSCursor.pointingHand.push()
                cursorIsPushed = true
            } else if cursorIsPushed && (!hovering || !isEnabled) {
                NSCursor.pop()
                cursorIsPushed = false
            }
        }
    }
}

struct IslandPointerHoverModifier: ViewModifier {
    let enabled: Bool
    @State private var cursorIsPushed = false

    func body(content: Content) -> some View {
        content
            .onHover { hovering in
                updateCursor(hovering: hovering)
            }
            .onDisappear {
                updateCursor(hovering: false)
            }
    }

    private func updateCursor(hovering: Bool) {
        if hovering && enabled && !cursorIsPushed {
            NSCursor.pointingHand.push()
            cursorIsPushed = true
        } else if cursorIsPushed && (!hovering || !enabled) {
            NSCursor.pop()
            cursorIsPushed = false
        }
    }
}

extension View {
    func islandPointerOnHover(enabled: Bool = true) -> some View {
        modifier(IslandPointerHoverModifier(enabled: enabled))
    }
}
