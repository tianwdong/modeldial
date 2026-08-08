import Foundation

struct UpdateConfiguration: Equatable {
    static let feedURLKey = "SUFeedURL"
    static let publicEDKeyKey = "SUPublicEDKey"

    let feedURL: URL?
    let publicEDKey: String?

    init(bundle: Bundle = .main) {
        self.init(infoDictionary: bundle.infoDictionary ?? [:])
    }

    init(infoDictionary: [String: Any]) {
        let feedValue = Self.nonEmptyString(infoDictionary[Self.feedURLKey])
        feedURL = feedValue.flatMap(URL.init(string:))
        publicEDKey = Self.nonEmptyString(infoDictionary[Self.publicEDKeyKey])
    }

    var isConfigured: Bool {
        guard
            let feedURL,
            feedURL.scheme?.lowercased() == "https",
            feedURL.host?.isEmpty == false,
            feedURL.user == nil,
            feedURL.password == nil,
            let publicEDKey,
            let decodedKey = Data(base64Encoded: publicEDKey)
        else {
            return false
        }
        return decodedKey.count == 32
    }

    private static func nonEmptyString(_ value: Any?) -> String? {
        guard let value = value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
