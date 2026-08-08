import Foundation

struct EndpointTestFeedback {
    let connectionID: String
    let modelID: String
    let ok: Bool
    let message: String
}

struct EndpointOperationState {
    var discoveredModelIDs: [String] = []
    var newlyDiscoveredModelIDs: [String] = []
    var configuredDiscoveredModelIDs: [String] = []
    var discoveredReasoningProfilesByModel: [String: [String]] = [:]
    var discoveredDefaultReasoningProfileByModel: [String: String] = [:]
    var message: String?
    private(set) var isRunning = false
    private(set) var testingConnectionID: String?
    private(set) var testingModelID: String?
    private(set) var testFeedback: EndpointTestFeedback?

    private var draftOperationGeneration = 0

    mutating func beginOperation(message: String? = nil) {
        isRunning = true
        self.message = message
    }

    mutating func finishOperation() {
        isRunning = false
        testingConnectionID = nil
        testingModelID = nil
    }

    mutating func beginDraftOperation() -> Int {
        draftOperationGeneration += 1
        isRunning = true
        return draftOperationGeneration
    }

    mutating func finishDraftOperation(_ generation: Int) -> Bool {
        guard generation == draftOperationGeneration else { return false }
        finishOperation()
        return true
    }

    mutating func invalidateDraftOperations() {
        draftOperationGeneration += 1
        finishOperation()
    }

    mutating func beginConnectionTest(connectionID: String, modelID: String) {
        isRunning = true
        testingConnectionID = connectionID
        testingModelID = modelID
        message = nil
        testFeedback = nil
    }

    mutating func recordTestFeedback(_ feedback: EndpointTestFeedback) {
        message = feedback.message
        testFeedback = feedback
    }

    mutating func finishConnectionTest(_ feedback: EndpointTestFeedback) {
        recordTestFeedback(feedback)
        finishOperation()
    }

    mutating func clearTestFeedback() {
        testFeedback = nil
    }

    mutating func replaceDiscovery(
        models: [String],
        newModels: [String],
        configuredModels: [String],
        reasoningProfilesByModel: [String: [String]],
        defaultReasoningProfileByModel: [String: String],
        message: String?
    ) {
        discoveredModelIDs = models
        newlyDiscoveredModelIDs = newModels
        configuredDiscoveredModelIDs = configuredModels
        discoveredReasoningProfilesByModel = reasoningProfilesByModel
        discoveredDefaultReasoningProfileByModel = defaultReasoningProfileByModel
        self.message = message
    }

    mutating func clearDiscovery(message: String? = nil) {
        replaceDiscovery(
            models: [],
            newModels: [],
            configuredModels: [],
            reasoningProfilesByModel: [:],
            defaultReasoningProfileByModel: [:],
            message: message
        )
    }

    mutating func resetModelDiscovery() {
        invalidateDraftOperations()
        clearDiscovery()
    }

    mutating func resetDraftFeedback() {
        resetModelDiscovery()
        testFeedback = nil
    }

    func isTesting(connectionID: String, modelID: String) -> Bool {
        isRunning
            && testingConnectionID == connectionID
            && testingModelID == modelID
    }

    func feedback(connectionID: String, modelID: String) -> EndpointTestFeedback? {
        guard testFeedback?.connectionID == connectionID,
              testFeedback?.modelID == modelID else {
            return nil
        }
        return testFeedback
    }
}
