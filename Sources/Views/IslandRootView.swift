import SwiftUI

struct IslandRootView: View {
    @ObservedObject var store: AppSessionStore
    @ObservedObject var model: IslandModel
    @ObservedObject private var appLanguage = AppLanguageStore.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var colorSchemeContrast
    @State private var isHovered = false
    @State private var isSessionPanelWidthExpanded = false
    @State private var isSessionPanelVisible = false
    @State private var isSessionPanelContentVisible = false
    @State private var isExpandedContentVisible = false
    @State private var isExpandedShellOpening = false
    @State private var expandedEntryDestination: GlanceDestination = .overview
    @State private var hoverTransitionTask: Task<Void, Never>?
    @State private var expandedTransitionTask: Task<Void, Never>?
    @Namespace private var islandTransitionNamespace

    var body: some View {
        ZStack(alignment: .top) {
            if model.state == .compact {
                CompactSessionPanelView(
                    store: store,
                    model: model,
                    transitionNamespace: islandTransitionNamespace,
                    isTransitionSource: model.state == .compact && isSessionPanelVisible
                )
                    .frame(height: revealedSessionPanelHeight, alignment: .top)
                    .clipped()
                    .padding(.top, max(0, model.compactHeight - 2))
                    .opacity(isSessionPanelContentVisible ? 1 : 0)
                    .offset(y: isSessionPanelContentVisible ? 0 : -4)
                    .allowsHitTesting(isSessionPanelContentVisible)
                    .zIndex(0)
            }

            islandSurface
                .zIndex(1)

            if model.state == .compact {
                compactIslandButton
                    .zIndex(2)
            }
        }
        .frame(width: model.expandedSize.width, height: model.expandedSize.height, alignment: .top)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onChange(of: model.isPointerInsideInteractionArea) { isInside in
            DebugLog.write("IslandRootView.pointerInside interaction=\(isInside) state=\(model.state)")
            updateCompactHover(isInside)
        }
        .onChange(of: store.glancePresentation.destination) { destination in
            guard model.state == .compact else { return }
            expandedEntryDestination = destination
        }
        .background(alignment: .top) {
            connectedPanelShadow
        }
        .overlay(alignment: .top) {
            activeEdgeSignal
        }
        .onChange(of: store.activeModelSessions.count) { count in
            guard isSessionPanelVisible || isSessionPanelWidthExpanded else { return }
            model.setCompactSessionPanel(
                visible: true,
                height: CompactSessionPanelView.height(forSessionCount: count)
            )
        }
        .onDisappear {
            hoverTransitionTask?.cancel()
            expandedTransitionTask?.cancel()
        }
        .environment(\.locale, appLanguage.locale)
    }

    private var compactIslandButton: some View {
        Button {
            guard model.state == .compact else { return }
            DebugLog.write("IslandRootView.activateCompact")
            store.prepareExpandedDestination()
            expandedEntryDestination = store.consumeExpandedDestination()
            openExpanded()
        } label: {
            Color.clear
                .frame(width: compactSurfaceWidth, height: model.size.height)
                .contentShape(compactSurfaceShape)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(store.glancePresentation.accessibilityLabel)
        .accessibilityHint(L10n.Island.openRecommendationDetails)
        .islandPointerOnHover()
    }

    private var islandSurface: some View {
        ZStack(alignment: .top) {
            backgroundGlow

            ExpandedSelectionView(
                store: store,
                expandedSize: model.expandedSize,
                notchHeight: model.notch.height,
                entryDestination: expandedEntryDestination,
                transitionNamespace: islandTransitionNamespace,
                onCollapse: collapseExpanded
            )
            .equatable()
            .opacity(isExpandedContentVisible ? 1 : 0)
            .allowsHitTesting(isExpandedContentVisible)
            .accessibilityHidden(!isExpandedContentVisible)
        }
        .frame(width: compactSurfaceWidth, height: model.size.height)
        .overlay {
            if model.state != .expanded || isExpandedShellOpening {
                CompactPillView(
                    presentation: store.glancePresentation,
                    notch: model.notch,
                    sideSlotWidth: model.compactSideSlotWidth,
                    transitionNamespace: islandTransitionNamespace,
                    primaryIdentityTransitionID: compactIdentityTransitionID,
                    isTransitionSource: (model.state == .compact && !isSessionPanelVisible)
                        || isExpandedShellOpening,
                    reduceMotion: reduceMotion
                )
                .padding(.horizontal, compactContentShellCompensation)
                .transition(.opacity)
            }
        }
        .clipShape(compactSurfaceShape)
        .contentShape(compactSurfaceShape)
    }

    private var backgroundGlow: some View {
        GlowLayer(
            activity: store.isGlanceActuallyVisible ? store.glancePresentation.activity : .none,
            tint: glowColor,
            reduceTransparency: reduceTransparency,
            increasedContrast: colorSchemeContrast == .increased,
            hovered: isHovered && model.state == .compact,
            connectedPanel: isSessionPanelVisible && model.state == .compact,
            surfaceShape: compactSurfaceShape
        )
        .contentShape(compactSurfaceShape)
        .onTapGesture {
            guard model.state == .expanded else { return }
            collapseExpanded()
        }
        .allowsHitTesting(model.state == .expanded)
    }

    private func collapseExpanded() {
        guard model.state == .expanded else { return }
        expandedTransitionTask?.cancel()
        expandedTransitionTask = nil
        isExpandedShellOpening = false
        withAnimation(expandedContentCloseAnimation) {
            isExpandedContentVisible = false
        }
        withAnimation(shapeCloseAnimation) {
            model.setState(.compact)
        }
    }

    private func openExpanded() {
        guard model.state == .compact else { return }
        hoverTransitionTask?.cancel()
        hoverTransitionTask = nil
        expandedTransitionTask?.cancel()
        expandedTransitionTask = nil
        isExpandedContentVisible = reduceMotion
        isExpandedShellOpening = !reduceMotion
        withAnimation(shapeOpenAnimation) {
            isSessionPanelContentVisible = false
            isSessionPanelWidthExpanded = false
            isSessionPanelVisible = false
            isHovered = false
            model.setState(.expanded)
        }
        guard !reduceMotion else { return }
        expandedTransitionTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard !Task.isCancelled, model.state == .expanded else { return }
            withAnimation(expandedContentOpenAnimation) {
                isExpandedShellOpening = false
                isExpandedContentVisible = true
            }
            expandedTransitionTask = nil
        }
    }

    private func updateCompactHover(_ isHovering: Bool) {
        let shouldHover = isHovering && model.state == .compact
        isHovered = shouldHover
        hoverTransitionTask?.cancel()
        hoverTransitionTask = nil

        if shouldHover {
            guard !isSessionPanelContentVisible
                || !isSessionPanelVisible
                || !isSessionPanelWidthExpanded
            else { return }
            hoverTransitionTask = Task { @MainActor in
                model.setCompactSessionPanel(
                    visible: true,
                    height: CompactSessionPanelView.height(
                        forSessionCount: store.activeModelSessions.count
                    )
                )
                withAnimation(hoverShapeAnimation) {
                    isSessionPanelWidthExpanded = true
                    isSessionPanelVisible = true
                }
                try? await Task.sleep(nanoseconds: 40_000_000)
                guard !Task.isCancelled, isHovered, model.state == .compact else { return }
                withAnimation(hoverContentAnimation) {
                    isSessionPanelContentVisible = true
                }
                hoverTransitionTask = nil
            }
            return
        }

        guard isSessionPanelContentVisible
            || isSessionPanelVisible
            || isSessionPanelWidthExpanded
        else { return }
        hoverTransitionTask = Task { @MainActor in
            guard !Task.isCancelled, !isHovered, model.state == .compact else { return }
            model.setCompactSessionPanel(visible: false)
            withAnimation(hoverCloseAnimation) {
                isSessionPanelContentVisible = false
                isSessionPanelVisible = false
                isSessionPanelWidthExpanded = false
            }
            try? await Task.sleep(nanoseconds: 140_000_000)
            guard !Task.isCancelled, !isSessionPanelWidthExpanded else { return }
            hoverTransitionTask = nil
        }
    }

    private var shapeOpenAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.12) : .islandOpen
    }

    private var shapeCloseAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.10) : .islandClose
    }

    private var expandedContentOpenAnimation: Animation {
        .easeOut(duration: reduceMotion ? 0.08 : 0.16)
    }

    private var expandedContentCloseAnimation: Animation {
        .easeOut(duration: reduceMotion ? 0.06 : 0.10)
    }

    private var hoverContentAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.08) : .islandHoverContent
    }

    private var hoverShapeAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.10) : .islandHover
    }

    private var hoverCloseAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.08) : .islandHoverClose
    }

    private var compactIdentityTransitionID: String {
        IslandTransitionElement.primaryIdentity.rawValue
    }

    private var connectedPanelShadow: some View {
        let isVisible = model.state == .compact && isSessionPanelVisible
        let showsShadow = isVisible && colorSchemeContrast != .increased
        let lineWidth: CGFloat = colorSchemeContrast == .increased ? 1 : 0.75

        return hoverSurfaceShape
            .fill(IslandVisual.panelBackground(reduceTransparency: reduceTransparency))
            .overlay {
                hoverSurfaceShape
                    .strokeBorder(
                        IslandVisual.border(
                            increasedContrast: colorSchemeContrast == .increased
                        ),
                        lineWidth: lineWidth
                    )
            }
            .frame(width: activeEdgeSize.width, height: activeEdgeSize.height)
            .shadow(
                color: showsShadow ? Color.black.opacity(0.46) : .clear,
                radius: showsShadow ? 22 : 0,
                y: showsShadow ? 10 : 0
            )
            .opacity(isVisible ? 1 : 0)
            .allowsHitTesting(false)
    }

    private var activeEdgeSignal: some View {
        ActivityEdgeLayer(
            activity: store.isGlanceActuallyVisible ? store.glancePresentation.activity : .none,
            tint: glowColor,
            reduceMotion: reduceMotion,
            increasedContrast: colorSchemeContrast == .increased,
            surfaceShape: activeOutlineShape
        )
        .frame(width: activeEdgeSize.width, height: activeEdgeSize.height)
        .allowsHitTesting(false)
    }

    private var compactSurfaceShape: IslandShape {
        if model.state == .expanded {
            return .expanded
        }
        guard isSessionPanelVisible else { return IslandShape() }
        return .hoverCap
    }

    private var activeOutlineShape: IslandShape {
        if model.state == .expanded {
            return .expanded
        }
        return isSessionPanelVisible ? hoverSurfaceShape : IslandShape()
    }

    private var hoverSurfaceShape: IslandShape {
        .hover
    }

    private var compactContentShellCompensation: CGFloat {
        guard isSessionPanelVisible else { return 0 }
        return IslandShape.hoverShoulderRadius
    }

    private var compactSurfaceWidth: CGFloat {
        guard model.state == .compact, isSessionPanelWidthExpanded else { return model.size.width }
        return model.compactSessionPanelWidth
    }

    private var revealedSessionPanelHeight: CGFloat {
        let height = CompactSessionPanelView.height(
            forSessionCount: store.activeModelSessions.count
        )
        return isSessionPanelVisible || reduceMotion
            ? height
            : 0
    }

    private var activeEdgeSize: CGSize {
        guard model.state == .compact, isSessionPanelWidthExpanded else { return model.size }
        return CGSize(
            width: model.compactSessionPanelWidth,
            height: model.size.height
                + (isSessionPanelVisible ? revealedSessionPanelHeight - 2 : 0)
        )
    }

    private var glowColor: Color {
        switch store.glancePresentation.tone {
        case .neutral:
            return .clear
        case .active:
            return IslandColor.interaction
        case .success:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .failure:
            return IslandColor.alertRed
        }
    }
}

private struct GlowLayer: View {
    let activity: GlanceActivity
    let tint: Color
    let reduceTransparency: Bool
    let increasedContrast: Bool
    let hovered: Bool
    let connectedPanel: Bool
    let surfaceShape: IslandShape

    var body: some View {
        surfaceShape
            .fill(
                connectedPanel
                    ? Color.clear
                    : IslandVisual.panelBackground(reduceTransparency: reduceTransparency)
            )
            .overlay { staticBorder }
            .shadow(
                color: increasedContrast || connectedPanel
                    ? .clear
                    : Color.black.opacity(hovered ? 0.46 : 0.38),
                radius: increasedContrast || connectedPanel ? 0 : (hovered ? 22 : 18),
                y: increasedContrast || connectedPanel ? 0 : 8
            )
            .animation(.easeOut(duration: 0.12), value: hovered)
            .animation(.easeOut(duration: 0.12), value: activity)
    }

    @ViewBuilder
    private var staticBorder: some View {
        let lineWidth: CGFloat = increasedContrast || activity != .none ? 1 : 0.5
        if !connectedPanel {
            surfaceShape.strokeBorder(borderColor, lineWidth: lineWidth)
        }
    }

    private var borderColor: Color {
        if increasedContrast {
            return IslandVisual.border(increasedContrast: true)
        }
        if activity != .none {
            return tint.opacity(0.36)
        }
        return Color.white.opacity(hovered ? 0.12 : 0.07)
    }
}

private struct ActivityEdgeLayer: View {
    let activity: GlanceActivity
    let tint: Color
    let reduceMotion: Bool
    let increasedContrast: Bool
    let surfaceShape: IslandShape

    @ViewBuilder
    var body: some View {
        if activity != .none {
            if reduceMotion || increasedContrast {
                surfaceShape
                    .strokeBorder(tint.opacity(0.72), lineWidth: 1.2)
            } else {
                TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { context in
                    longTailEdgeHighlight(at: context.date)
                }
            }
        }
    }

    private func longTailEdgeHighlight(at date: Date) -> some View {
        let cycleDuration = 4.8
        let rotation = date.timeIntervalSinceReferenceDate
            .truncatingRemainder(dividingBy: cycleDuration) / cycleDuration * 360

        return surfaceShape
            .stroke(
                AngularGradient(
                    gradient: Gradient(stops: [
                        .init(color: .clear, location: 0.00),
                        .init(color: .clear, location: 0.55),
                        .init(color: tint, location: 0.78),
                        .init(color: Color.white.opacity(0.95), location: 0.92),
                        .init(color: .clear, location: 1.00),
                    ]),
                    center: .center,
                    angle: .degrees(rotation)
                ),
                lineWidth: 4
            )
            .blur(radius: 3)
            .mask(surfaceShape)
    }
}
