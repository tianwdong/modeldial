import Foundation

enum DebugLog {
    private static let enabled: Bool = {
        let environment = ProcessInfo.processInfo.environment
        return environment["MODELDIAL_DEBUG_LOG"] == "1"
    }()

    private static let maxLogSizeBytes: UInt64 = 1_048_576

    private static let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let lock = NSLock()

    private static var logDirectoryURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("modeldial", isDirectory: true)
            .appendingPathComponent("Logs", isDirectory: true)
    }

    private static var logURL: URL {
        logDirectoryURL.appendingPathComponent("debug.log")
    }

    private static var rotatedLogURL: URL {
        logDirectoryURL.appendingPathComponent("debug.log.1")
    }

    static func reset() {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: logURL)
        try? FileManager.default.removeItem(at: rotatedLogURL)
    }

    static func write(_ message: String) {
        guard enabled else { return }
        lock.lock()
        defer { lock.unlock() }

        prepareLogDirectory()
        rotateIfNeeded()
        let line = "[\(formatter.string(from: Date()))] \(message)\n"
        let data = Data(line.utf8)
        if FileManager.default.fileExists(atPath: logURL.path) {
            if let handle = try? FileHandle(forWritingTo: logURL) {
                defer { try? handle.close() }
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: data)
            }
            return
        }

        try? data.write(to: logURL, options: .atomic)
        try? FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: logURL.path
        )
    }

    private static func prepareLogDirectory() {
        try? FileManager.default.createDirectory(
            at: logDirectoryURL,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
    }

    private static func rotateIfNeeded() {
        guard
            let attributes = try? FileManager.default.attributesOfItem(atPath: logURL.path),
            let size = (attributes[.size] as? NSNumber)?.uint64Value,
            size >= maxLogSizeBytes
        else { return }
        try? FileManager.default.removeItem(at: rotatedLogURL)
        try? FileManager.default.moveItem(at: logURL, to: rotatedLogURL)
    }
}
