import Foundation

enum ActiveSessionPresenter {
    struct SessionPresentation: Equatable, Identifiable {
        let id: String
        let title: String
        let context: String
        let identity: String
        let sourceDisplayName: String
    }

    struct OverviewPresentation: Equatable {
        let totalCount: Int
        let visibleSessions: [SessionPresentation]
        let overflowCount: Int
    }

    private static let overviewVisibleCount = 2

    static func present(_ session: BridgeDetectedModelSession) -> SessionPresentation {
        let threadName = session.threadName?.trimmingCharacters(in: .whitespacesAndNewlines)
        let title = threadName.flatMap { $0.isEmpty ? nil : $0 } ?? session.workspaceName

        var contextParts = [session.sourceDisplayName]
        if title != session.workspaceName {
            contextParts.append(session.workspaceName)
        }
        if let model = session.model, !model.isEmpty {
            contextParts.append(
                ModelIdentityPresentation.displayLabel(
                    model: model,
                    effort: session.effort ?? ""
                )
            )
        }

        let identity: String
        if let model = session.model?.trimmingCharacters(in: .whitespacesAndNewlines),
           !model.isEmpty {
            identity = ModelIdentityPresentation.displayLabel(
                model: model,
                effort: session.effort ?? ""
            )
        } else {
            identity = session.sourceDisplayName
        }

        return SessionPresentation(
            id: session.id,
            title: title,
            context: contextParts.joined(separator: " · "),
            identity: identity,
            sourceDisplayName: session.sourceDisplayName
        )
    }

    static func overview(
        _ sessions: [BridgeDetectedModelSession]
    ) -> OverviewPresentation {
        OverviewPresentation(
            totalCount: sessions.count,
            visibleSessions: sessions.prefix(overviewVisibleCount).map { present($0) },
            overflowCount: max(0, sessions.count - overviewVisibleCount)
        )
    }
}
