import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func verifyDraftOperationsIgnoreStaleCompletions() {
    var state = EndpointOperationState()
    let staleGeneration = state.beginDraftOperation()
    state.beginConnectionTest(connectionID: "connection-a", modelID: "model-a")

    state.invalidateDraftOperations()

    expect(!state.finishDraftOperation(staleGeneration), "invalidated completion should be ignored")
    expect(!state.isRunning, "invalidating a draft operation should stop its loading state")
    expect(
        !state.isTesting(connectionID: "connection-a", modelID: "model-a"),
        "invalidating a draft operation should clear its testing identity"
    )

    let currentGeneration = state.beginDraftOperation()
    expect(state.finishDraftOperation(currentGeneration), "current completion should be accepted")
}

private func verifyFeedbackIsScopedToConnectionAndModel() {
    var state = EndpointOperationState()
    let feedback = EndpointTestFeedback(
        connectionID: "connection-a",
        modelID: "model-a",
        ok: true,
        message: "ok"
    )

    state.beginConnectionTest(connectionID: "connection-a", modelID: "model-a")
    state.finishConnectionTest(feedback)

    expect(state.feedback(connectionID: "connection-a", modelID: "model-a")?.ok == true, "matching feedback should be returned")
    expect(state.feedback(connectionID: "connection-a", modelID: "model-b") == nil, "feedback should not leak to another model")
    expect(!state.isRunning, "finishing a connection test should stop its loading state")
}

private func verifyDiscoveryAndFeedbackResetBoundaries() {
    var state = EndpointOperationState()
    state.replaceDiscovery(
        models: ["model-a"],
        newModels: ["model-a"],
        configuredModels: [],
        reasoningProfilesByModel: ["model-a": ["high"]],
        defaultReasoningProfileByModel: ["model-a": "high"],
        message: "found"
    )
    state.recordTestFeedback(EndpointTestFeedback(
        connectionID: "connection-a",
        modelID: "model-a",
        ok: true,
        message: "ok"
    ))

    state.resetModelDiscovery()

    expect(state.discoveredModelIDs.isEmpty, "model discovery reset should clear discovered models")
    expect(state.message == nil, "model discovery reset should clear its operation message")
    expect(state.testFeedback != nil, "model discovery reset should preserve scoped test feedback")

    state.resetDraftFeedback()
    expect(state.testFeedback == nil, "draft feedback reset should clear test feedback")
}

@main
private enum EndpointOperationStateTestMain {
    static func main() {
        verifyDraftOperationsIgnoreStaleCompletions()
        verifyFeedbackIsScopedToConnectionAndModel()
        verifyDiscoveryAndFeedbackResetBoundaries()
        if failureCount > 0 {
            exit(1)
        }
        print("EndpointOperationState tests passed")
    }
}
