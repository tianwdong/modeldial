enum RuntimeEventStateUpdate {
    case none
    case runtime(BridgeRuntime)
    case snapshot(BridgeSnapshot)
}

enum RuntimeEventReducer {
    static func stateUpdate(for event: ScanEvent) -> RuntimeEventStateUpdate {
        if let snapshot = event.snapshot {
            return .snapshot(snapshot)
        }
        if let runtime = event.runtimeState?.runtime {
            return .runtime(runtime)
        }
        return .none
    }
}
