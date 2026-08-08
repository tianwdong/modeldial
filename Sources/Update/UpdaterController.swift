import Combine
import Foundation
import Sparkle

@MainActor
final class UpdaterController: ObservableObject {
    static let shared = UpdaterController()

    let configuration: UpdateConfiguration
    let currentVersion: String
    let currentBuild: String

    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false
    @Published private(set) var automaticallyDownloadsUpdates = false
    @Published private(set) var allowsAutomaticUpdates = false
    @Published private(set) var updateCheckState: UpdateCheckState = .idle

    private let updateCheckObserver = UpdateCheckObserver()
    private let standardUpdaterController: SPUStandardUpdaterController
    private var observations: [NSKeyValueObservation] = []
    private var hasStarted = false
    private var isManualUpdateProbe = false
    private var manualUpdateProbeOutcome: UpdateCheckState?

    var isConfigured: Bool {
        configuration.isConfigured
    }

    init(bundle: Bundle = .main) {
        configuration = UpdateConfiguration(bundle: bundle)
        currentVersion = Self.bundleString("CFBundleShortVersionString", bundle: bundle)
        currentBuild = Self.bundleString("CFBundleVersion", bundle: bundle)
        standardUpdaterController = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: updateCheckObserver,
            userDriverDelegate: nil
        )

        let updater = standardUpdaterController.updater
        synchronize(with: updater)
        observe(updater)
        updateCheckObserver.onEvent = { [weak self] event in
            self?.handleUpdateCheckEvent(event)
        }
    }

    func startIfConfigured() {
        guard configuration.isConfigured, !hasStarted else { return }
        hasStarted = true
        standardUpdaterController.startUpdater()
        synchronize(with: standardUpdaterController.updater)
    }

    func checkForUpdates() {
        startIfConfigured()
        let updater = standardUpdaterController.updater
        guard configuration.isConfigured, hasStarted else {
            updateCheckState = .notConfigured
            return
        }
        guard updater.canCheckForUpdates, !updater.sessionInProgress else { return }

        isManualUpdateProbe = true
        manualUpdateProbeOutcome = nil
        updateCheckState = .checking
        updater.checkForUpdateInformation()
    }

    func setAutomaticallyChecksForUpdates(_ enabled: Bool) {
        guard configuration.isConfigured else { return }
        standardUpdaterController.updater.automaticallyChecksForUpdates = enabled
        synchronize(with: standardUpdaterController.updater)
    }

    func setAutomaticallyDownloadsUpdates(_ enabled: Bool) {
        let updater = standardUpdaterController.updater
        guard configuration.isConfigured, updater.allowsAutomaticUpdates else { return }
        updater.automaticallyDownloadsUpdates = enabled
        synchronize(with: updater)
    }

    private func observe(_ updater: SPUUpdater) {
        observations = [
            updater.observe(\.canCheckForUpdates, options: [.new]) { [weak self] updater, _ in
                Task { @MainActor in self?.synchronize(with: updater) }
            },
            updater.observe(\.automaticallyChecksForUpdates, options: [.new]) { [weak self] updater, _ in
                Task { @MainActor in self?.synchronize(with: updater) }
            },
            updater.observe(\.automaticallyDownloadsUpdates, options: [.new]) { [weak self] updater, _ in
                Task { @MainActor in self?.synchronize(with: updater) }
            },
            updater.observe(\.allowsAutomaticUpdates, options: [.new]) { [weak self] updater, _ in
                Task { @MainActor in self?.synchronize(with: updater) }
            },
        ]
    }

    private func synchronize(with updater: SPUUpdater) {
        guard configuration.isConfigured else {
            canCheckForUpdates = false
            automaticallyChecksForUpdates = false
            automaticallyDownloadsUpdates = false
            allowsAutomaticUpdates = false
            return
        }
        canCheckForUpdates = hasStarted && updater.canCheckForUpdates
        automaticallyChecksForUpdates = updater.automaticallyChecksForUpdates
        automaticallyDownloadsUpdates = updater.automaticallyDownloadsUpdates
        allowsAutomaticUpdates = updater.allowsAutomaticUpdates
    }

    private func handleUpdateCheckEvent(_ event: UpdateCheckObserver.Event) {
        guard isManualUpdateProbe else { return }
        switch event {
        case .foundValidUpdate:
            manualUpdateProbeOutcome = .updateAvailable
        case .didNotFindUpdate(let error):
            manualUpdateProbeOutcome = state(forNoUpdateError: error)
        case .finished(let error):
            let outcome = manualUpdateProbeOutcome
                ?? error.map { _ in .failed }
                ?? .failed
            isManualUpdateProbe = false
            manualUpdateProbeOutcome = nil
            updateCheckState = outcome
            if outcome == .updateAvailable {
                standardUpdaterController.checkForUpdates(nil)
            }
        }
    }

    private func state(forNoUpdateError error: Error) -> UpdateCheckState {
        let error = error as NSError
        guard error.domain == SUSparkleErrorDomain,
              error.code == Int(SUError.noUpdateError.rawValue),
              let rawReason = error.userInfo[SPUNoUpdateFoundReasonKey] as? NSNumber,
              let reason = SPUNoUpdateFoundReason(
                rawValue: OSStatus(rawReason.int32Value)
              ) else {
            return .failed
        }

        switch reason {
        case .onLatestVersion, .onNewerThanLatestVersion:
            return .upToDate
        case .systemIsTooOld, .systemIsTooNew, .hardwareDoesNotSupportARM64:
            return .unsupportedSystem
        case .unknown:
            return .failed
        @unknown default:
            return .failed
        }
    }

    private static func bundleString(_ key: String, bundle: Bundle) -> String {
        guard let value = bundle.object(forInfoDictionaryKey: key) as? String else {
            return "—"
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "—" : trimmed
    }
}

@MainActor
private final class UpdateCheckObserver: NSObject, SPUUpdaterDelegate {
    enum Event {
        case foundValidUpdate
        case didNotFindUpdate(Error)
        case finished(Error?)
    }

    var onEvent: ((Event) -> Void)?

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        onEvent?(.foundValidUpdate)
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: Error) {
        onEvent?(.didNotFindUpdate(error))
    }

    func updater(
        _ updater: SPUUpdater,
        didFinishUpdateCycleFor updateCheck: SPUUpdateCheck,
        error: Error?
    ) {
        onEvent?(.finished(error))
    }
}
