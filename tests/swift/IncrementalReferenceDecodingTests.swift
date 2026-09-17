import Foundation

@main
struct IncrementalReferenceDecodingTests {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let snapshot = try decoder.decode(
            BridgeReferenceSnapshot.self,
            from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        )
        let entry = snapshot.entries[0]
        if CommandLine.arguments[2] == "legacy" {
            precondition(snapshot.entrySource(for: entry.modelConfigurationId) == nil)
        } else {
            let source = snapshot.entrySource(for: entry.modelConfigurationId)!
            precondition(source.batchId != snapshot.batchId)
            precondition(source.graderVersion != snapshot.graderVersion)
            precondition(!source.scoreBaselineId.isEmpty)
        }
        precondition(snapshot.entries.count == snapshot.entryCount)
        precondition(snapshot.isPublicOfficialSnapshot)
    }
}
