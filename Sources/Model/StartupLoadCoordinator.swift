import Foundation

struct StartupLoadResult {
    let snapshot: BridgeSnapshot
    let warningDetail: String?
    let referenceRefreshStatus: String?
}

struct StartupLoadCoordinator {
    private var hasClaimedMaintenance = false

    mutating func claimMaintenanceIfNeeded() -> Bool {
        guard !hasClaimedMaintenance else { return false }
        hasClaimedMaintenance = true
        return true
    }

    static func load(
        recoverRun: () throws -> BridgeRunRecoveryResponse,
        observeState: () throws -> BridgeStateObservationResponse,
        snapshot: () throws -> BridgeSnapshot
    ) throws -> StartupLoadResult {
        var issues: [String] = []
        do {
            let recovery = try recoverRun()
            if recovery.requiresAttention {
                issues.append(recovery.message)
            }
        } catch {
            issues.append(error.localizedDescription)
        }
        do {
            _ = try observeState()
        } catch {
            issues.append(error.localizedDescription)
        }
        return StartupLoadResult(
            snapshot: try snapshot(),
            warningDetail: issues.isEmpty ? nil : issues.joined(separator: "\n"),
            referenceRefreshStatus: nil
        )
    }

    static func refreshReference(
        refreshReference: () throws -> BridgeReferenceRefreshResponse,
        snapshot: () throws -> BridgeSnapshot
    ) throws -> StartupLoadResult {
        var issues: [String] = []
        var refreshStatus: String?
        do {
            let reference = try refreshReference()
            refreshStatus = reference.status
            if reference.requiresAttention {
                issues.append(reference.message)
            }
        } catch {
            refreshStatus = "failed"
            issues.append(error.localizedDescription)
        }
        return StartupLoadResult(
            snapshot: try snapshot(),
            warningDetail: issues.isEmpty ? nil : issues.joined(separator: "\n"),
            referenceRefreshStatus: refreshStatus
        )
    }
}

struct ReferenceSnapshotRefreshPolicy {
    static let refreshInterval: TimeInterval = 6 * 60 * 60
    private static let inFlightRetryInterval: TimeInterval = 15 * 60
    private static let publicationRetryOffsets: [TimeInterval] = [
        2 * 60,
        5 * 60,
        10 * 60,
        20 * 60,
        30 * 60,
    ]
    private static let publicationCatchUpInterval: TimeInterval = 60 * 60
    private static let scheduleVersion = 3
    private static let failureBackoff: [TimeInterval] = [
        5 * 60,
        15 * 60,
        60 * 60,
        6 * 60 * 60,
    ]
    private static let defaultPersistencePrefix =
        "ModelDial.ReferenceSnapshotRefreshPolicy"

    private let persistence: UserDefaults?
    private let nextAttemptKey: String
    private let failureCountKey: String
    private let scheduleVersionKey: String
    private(set) var nextAttemptAt: Date?
    private(set) var consecutiveFailures: Int

    init(
        persistence: UserDefaults? = .standard,
        persistencePrefix: String = Self.defaultPersistencePrefix
    ) {
        self.persistence = persistence
        nextAttemptKey = "\(persistencePrefix).nextAttemptAt"
        failureCountKey = "\(persistencePrefix).consecutiveFailures"
        scheduleVersionKey = "\(persistencePrefix).scheduleVersion"
        let persistedScheduleVersion = persistence?.integer(
            forKey: scheduleVersionKey
        )
        if persistedScheduleVersion == Self.scheduleVersion,
           let timestamp = persistence?.object(
               forKey: nextAttemptKey
           ) as? NSNumber {
            nextAttemptAt = Date(timeIntervalSince1970: timestamp.doubleValue)
        } else {
            nextAttemptAt = nil
        }
        consecutiveFailures = persistedScheduleVersion == Self.scheduleVersion
            ? max(0, persistence?.integer(forKey: failureCountKey) ?? 0)
            : 0
    }

    mutating func claimIfDue(now: Date = Date(), force: Bool = false) -> Bool {
        guard force || isDue(now: now) else { return false }
        nextAttemptAt = now.addingTimeInterval(Self.inFlightRetryInterval)
        persist()
        return true
    }

    func isDue(now: Date = Date()) -> Bool {
        nextAttemptAt.map { now >= $0 } ?? true
    }

    mutating func record(
        status: String,
        latestPublishedAt: Date? = nil,
        now: Date = Date()
    ) {
        if status == "refreshed" || status == "not_modified" {
            consecutiveFailures = 0
            if let latestPublishedAt,
               latestPublishedAt >= Self.currentRefreshSlot(containing: now) {
                nextAttemptAt = Self.nextScheduledRefresh(after: now)
            } else {
                nextAttemptAt = Self.nextPublicationRetry(after: now)
            }
        } else if status == "not_configured" {
            consecutiveFailures = 0
            nextAttemptAt = Self.nextScheduledRefresh(after: now)
        } else {
            consecutiveFailures += 1
            let backoffIndex = min(
                consecutiveFailures - 1,
                Self.failureBackoff.count - 1
            )
            nextAttemptAt = now.addingTimeInterval(
                Self.failureBackoff[backoffIndex]
            )
        }
        persist()
    }

    static func currentRefreshSlot(containing date: Date) -> Date {
        let completedIntervals = floor(
            date.timeIntervalSince1970 / refreshInterval
        )
        return Date(
            timeIntervalSince1970: completedIntervals * refreshInterval
        )
    }

    static func nextPublicationRetry(after date: Date) -> Date {
        let slot = currentRefreshSlot(containing: date)
        for offset in publicationRetryOffsets {
            let candidate = slot.addingTimeInterval(offset)
            if candidate > date {
                return candidate
            }
        }
        return min(
            date.addingTimeInterval(publicationCatchUpInterval),
            nextScheduledRefresh(after: date)
        )
    }

    static func nextScheduledRefresh(after date: Date) -> Date {
        let completedIntervals = floor(
            date.timeIntervalSince1970 / refreshInterval
        )
        return Date(
            timeIntervalSince1970: (completedIntervals + 1) * refreshInterval
        )
    }

    private func persist() {
        guard let persistence else { return }
        if let nextAttemptAt {
            persistence.set(
                nextAttemptAt.timeIntervalSince1970,
                forKey: nextAttemptKey
            )
        } else {
            persistence.removeObject(forKey: nextAttemptKey)
        }
        persistence.set(consecutiveFailures, forKey: failureCountKey)
        persistence.set(Self.scheduleVersion, forKey: scheduleVersionKey)
    }
}
