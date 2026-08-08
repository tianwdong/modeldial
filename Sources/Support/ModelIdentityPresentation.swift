import Foundation

enum ModelIdentityPresentation {
    static func displayLabel(model: String, effort: String) -> String {
        let effort = effortTag(for: effort).map { " \($0)" } ?? ""
        return "\(canonicalName(for: model))\(effort)"
    }

    static func canonicalName(for model: String) -> String {
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        let modelID = String(trimmed.split(separator: "/").last ?? Substring(trimmed))
        let components = modelID
            .replacingOccurrences(of: "_", with: "-")
            .split(separator: "-")
            .map { String($0).lowercased() }

        guard !components.isEmpty else { return trimmed }
        if components.first == "gpt", components.count > 1 {
            let family = "GPT-\(components[1])"
            let variant = components.dropFirst(2).map(canonicalComponent).joined(separator: " ")
            return variant.isEmpty ? family : "\(family) \(variant)"
        }
        return components.map(canonicalComponent).joined(separator: " ")
    }

    static func effortTag(for effort: String) -> String? {
        let normalized = canonicalEffortName(for: effort)
        guard !normalized.isEmpty,
              normalized != "default",
              normalized != "codex_default" else {
            return nil
        }
        return effortLabel(for: normalized)
    }

    static func canonicalEffortName(for effort: String) -> String {
        effort.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    static func effortLabel(for effort: String) -> String {
        switch canonicalEffortName(for: effort) {
        case "xhigh": return "XHigh"
        case "ultra": return "Ultra"
        case "medium": return "Medium"
        case "minimal": return "Minimal"
        case "high": return "High"
        case "low": return "Low"
        case "max": return "Max"
        case "none": return "None"
        case let value:
            return value
                .replacingOccurrences(of: "_", with: "-")
                .split(separator: "-")
                .map { canonicalComponent(String($0)) }
                .joined(separator: " ")
        }
    }

    static func canonicalProviderID(for providerID: String?) -> String? {
        guard let normalized = providerID?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased(),
              !normalized.isEmpty else {
            return nil
        }
        switch normalized {
        case "codex": return "openai"
        case "claude-code": return "anthropic"
        case "grok-build": return "xai"
        case "gemini", "google": return "google"
        case "z-ai", "zhipu": return "zhipu"
        case "openai", "anthropic", "deepseek", "xai", "openrouter",
             "moonshot", "minimax", "vercel-ai-gateway":
            return normalized
        default:
            return normalized
        }
    }

    static func providerBrandID(
        providerID: String?,
        familyID: String? = nil,
        model: String? = nil
    ) -> String? {
        let provider = canonicalProviderID(for: providerID)
        guard provider == nil
                || provider == "custom"
                || provider == "custom_endpoint"
                || provider == "unknown" else {
            return provider
        }
        let modelIdentity = [familyID, model]
            .compactMap { $0?.lowercased() }
            .joined(separator: " ")
        let knownFamilies: [(prefix: String, provider: String)] = [
            ("gpt-", "openai"),
            ("claude-", "anthropic"),
            ("deepseek-", "deepseek"),
            ("gemini-", "google"),
            ("grok-", "xai"),
            ("kimi-", "moonshot"),
            ("glm-", "zhipu"),
            ("minimax-", "minimax"),
        ]
        return knownFamilies.first { modelIdentity.contains($0.prefix) }?.provider
            ?? provider
    }

    static func providerLogoResourceName(for providerID: String?) -> String? {
        switch canonicalProviderID(for: providerID) {
        case "openai": return "openai-lobe"
        case "anthropic": return "anthropic-lobe"
        case "deepseek": return "deepseek-lobe"
        case "google": return "google-lobe"
        case "xai": return "xai-lobe"
        case "openrouter": return "openrouter-lobe"
        case "moonshot": return "moonshot-lobe"
        case "zhipu": return "zhipu-lobe"
        case "minimax": return "minimax-lobe"
        case "vercel-ai-gateway": return "vercel-lobe"
        default: return nil
        }
    }

    static func providerDisplayName(for providerID: String?) -> String {
        switch canonicalProviderID(for: providerID) {
        case "openai": return "OpenAI"
        case "anthropic": return "Anthropic"
        case "deepseek": return "DeepSeek"
        case "google": return "Google"
        case "xai": return "xAI"
        case "openrouter": return "OpenRouter"
        case "moonshot": return "Moonshot AI"
        case "zhipu": return "Zhipu AI"
        case "minimax": return "MiniMax"
        case "vercel-ai-gateway": return "Vercel AI Gateway"
        case let value?:
            return value
                .split(whereSeparator: { $0 == "-" || $0 == "_" })
                .map { $0.capitalized }
                .joined(separator: " ")
        case nil:
            return "AI provider"
        }
    }

    static func providerMonogram(for providerID: String?) -> String {
        let displayName = providerDisplayName(for: providerID)
        let words = displayName.split(separator: " ")
        if words.count > 1 {
            return words.prefix(2).compactMap(\.first).map(String.init).joined().uppercased()
        }
        return String(displayName.prefix(2)).uppercased()
    }

    private static func canonicalComponent(_ component: String) -> String {
        switch component.lowercased() {
        case "gpt": return "GPT"
        case "deepseek": return "DeepSeek"
        case "glm": return "GLM"
        case "kimi": return "Kimi"
        case "claude": return "Claude"
        case "gemini": return "Gemini"
        case "grok": return "Grok"
        case "codex": return "Codex"
        default:
            return Double(component) == nil ? component.capitalized : component
        }
    }
}
