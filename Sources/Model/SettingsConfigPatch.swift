import Foundation

enum SettingsConfigPatch {
    enum RecommendationPreference: String {
        case smart
        case quality
        case speed
        case cost
    }

    enum RecommendationSourceMode: String {
        case auto
        case officialSnapshot = "official_snapshot"
        case localEvaluation = "local_evaluation"
    }

    enum SchedulerMode: String {
        case interval
        case daily
        case weekly
    }

    case modelCandidatesEnabled(
        connectionID: String,
        candidateIDs: [String],
        enabled: Bool
    )
    case connectionEnabled(connectionID: String, enabled: Bool)
    case deleteConnection(connectionID: String)
    case removeModelCandidates(connectionID: String, candidateIDs: [String])
    case connectionSecretReferences([String: String])
    case addDiscoveredLocalCandidate(
        connectionID: String,
        modelID: String,
        displayName: String,
        scanProfile: String
    )
    case currentDefault(candidateID: String?)
    case automaticCurrentModel
    case recommendationPreference(RecommendationPreference)
    case sourceMode(RecommendationSourceMode, configurationID: String)
    case projectTaskProfile(name: String, taskMode: String)
    case scanBudget(
        enabled: Bool,
        maxDurationSeconds: Int,
        maxReferenceCostUsd: Double
    )
    case scanExecution(
        maxConcurrentTargets: Int,
        executionTimeoutSeconds: Int,
        timeoutRetryCount: Int
    )
    case scheduler(mode: SchedulerMode, intervalSeconds: Int)
    case schedulerEnabled(Bool)
    case schedulerMode(SchedulerMode)
    case dailySchedule(hour: Int, minute: Int)
    case weeklySchedule(weekday: Int, hour: Int, minute: Int)
    case scheduledEvaluationProfile(String)

    var commandPayload: [String: Any] {
        let command: (operation: String, arguments: [String: Any])
        switch self {
        case .modelCandidatesEnabled(let connectionID, let candidateIDs, let enabled):
            command = (
                "model_candidates_enabled",
                [
                    "connection_id": connectionID,
                    "candidate_ids": candidateIDs,
                    "enabled": enabled,
                ]
            )
        case .connectionEnabled(let connectionID, let enabled):
            command = (
                "connection_enabled",
                ["connection_id": connectionID, "enabled": enabled]
            )
        case .deleteConnection(let connectionID):
            command = (
                "delete_connection",
                ["connection_id": connectionID]
            )
        case .removeModelCandidates(let connectionID, let candidateIDs):
            command = (
                "remove_model_candidates",
                [
                    "connection_id": connectionID,
                    "candidate_ids": candidateIDs,
                ]
            )
        case .connectionSecretReferences(let referencesByConnectionID):
            command = (
                "connection_secret_references",
                ["references_by_connection_id": referencesByConnectionID]
            )
        case .addDiscoveredLocalCandidate(
            let connectionID,
            let modelID,
            let displayName,
            let scanProfile
        ):
            command = (
                "add_discovered_local_candidate",
                [
                    "connection_id": connectionID,
                    "model_id": modelID,
                    "display_name": displayName,
                    "scan_profile": scanProfile,
                ]
            )
        case .currentDefault(let candidateID):
            command = (
                "current_default",
                ["candidate_id": candidateID ?? NSNull()]
            )
        case .automaticCurrentModel:
            command = ("automatic_current_model", [:])
        case .recommendationPreference(let preference):
            command = (
                "recommendation_preference",
                ["preference": preference.rawValue]
            )
        case .sourceMode(let sourceMode, let configurationID):
            command = (
                "source_mode",
                [
                    "source_mode": sourceMode.rawValue,
                    "configuration_id": configurationID,
                ]
            )
        case .projectTaskProfile(let name, let taskMode):
            command = (
                "project_task_profile",
                ["name": name, "task_mode": taskMode]
            )
        case .scanBudget(
            let enabled,
            let maxDurationSeconds,
            let maxReferenceCostUsd
        ):
            command = (
                "scan_budget",
                [
                    "enabled": enabled,
                    "max_duration_seconds": maxDurationSeconds,
                    "max_reference_cost_usd": maxReferenceCostUsd,
                ]
            )
        case .scanExecution(
            let maxConcurrentTargets,
            let executionTimeoutSeconds,
            let timeoutRetryCount
        ):
            command = (
                "scan_execution",
                [
                    "max_concurrent_targets": maxConcurrentTargets,
                    "execution_timeout_seconds": executionTimeoutSeconds,
                    "timeout_retry_count": timeoutRetryCount,
                ]
            )
        case .scheduler(let mode, let intervalSeconds):
            command = (
                "scheduler",
                ["mode": mode.rawValue, "interval_seconds": intervalSeconds]
            )
        case .schedulerEnabled(let enabled):
            command = ("scheduler_enabled", ["enabled": enabled])
        case .schedulerMode(let mode):
            command = ("scheduler_mode", ["mode": mode.rawValue])
        case .dailySchedule(let hour, let minute):
            command = ("daily_schedule", ["hour": hour, "minute": minute])
        case .weeklySchedule(let weekday, let hour, let minute):
            command = (
                "weekly_schedule",
                ["weekday": weekday, "hour": hour, "minute": minute]
            )
        case .scheduledEvaluationProfile(let profileID):
            command = (
                "scheduled_evaluation_profile",
                ["profile_id": profileID]
            )
        }
        return [
            "schema_version": 1,
            "operation": command.operation,
            "arguments": command.arguments,
        ]
    }
}
