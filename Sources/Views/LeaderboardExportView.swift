import AppKit
import SwiftUI
import UniformTypeIdentifiers

enum LeaderboardExportLanguage: String, CaseIterable, Identifiable {
    case simplifiedChinese
    case english

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .simplifiedChinese: return "简体中文"
        case .english: return "English"
        }
    }

    var filenameCode: String {
        switch self {
        case .simplifiedChinese: return "zh"
        case .english: return "en"
        }
    }

    var appLanguage: AppLanguage {
        switch self {
        case .simplifiedChinese: return .zhHans
        case .english: return .en
        }
    }

    static var currentAppDefault: LeaderboardExportLanguage {
        AppLanguageResolver.resolvedResourceName(
            for: AppLanguageResolver.current()
        ) == "zh-Hans" ? .simplifiedChinese : .english
    }
}

private struct LeaderboardExportCopy {
    let language: LeaderboardExportLanguage

    private var bundle: Bundle {
        AppLanguageResolver.localizationBundle(for: language.appLanguage)
    }

    private var locale: Locale {
        AppLanguageResolver.locale(for: language.appLanguage)
    }

    func text(_ source: String) -> String {
        bundle.localizedString(forKey: source, value: source, table: "Localizable")
    }

    func format(_ source: String, _ arguments: CVarArg...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }

    func resultsAsOfText(_ date: Date?) -> String {
        guard let date else { return text("成绩时间未记录") }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy.MM.dd HH:mm"
        return format("成绩截至 %@", formatter.string(from: date))
    }

    func rankLabel(for row: LeaderboardExportRow) -> String {
        guard let rank = row.canonicalRank else { return text("暂不排名") }
        return row.isTiedRank
            ? format("并列第 %d 名", rank)
            : format("第 %d 名", rank)
    }

    func tagLabel(_ kind: String) -> String {
        switch kind {
        case "recommended": return text("推荐")
        case "value": return text("性价比")
        case "speed": return text("速度优选")
        case "lightweight": return text("轻量优选")
        default: return kind
        }
    }

    func tagDescription(_ kind: String) -> String {
        switch kind {
        case "recommended": return text("由当前推荐决策标记。")
        case "value": return text("第一梯队模型，参考费用最低。")
        case "speed": return text("第一梯队模型，总耗时最短。")
        case "lightweight":
            return text("费用显著更低、性能适中，适合 OpenClaw、Hermes 等日常使用。")
        default: return kind
        }
    }
}

struct LeaderboardExportTag: Identifiable {
    let kind: String
    let label: String

    var id: String { kind }

    var priority: Int {
        switch kind {
        case "recommended": return 0
        case "value": return 1
        case "speed": return 2
        case "lightweight": return 3
        default: return 99
        }
    }
}

struct LeaderboardExportRow: Identifiable {
    let id: String
    let providerID: String?
    let modelLabel: String
    let canonicalRank: Int?
    let isTiedRank: Bool
    let isRecommended: Bool
    let score: Int
    let elapsedSeconds: Double?
    let referenceCostUsd: Double?
    let decisionTags: [LeaderboardExportTag]

    var visibleDecisionTags: [LeaderboardExportTag] {
        Array(
            decisionTags
                .sorted { $0.priority < $1.priority }
                .prefix(2)
        )
    }
}

struct LeaderboardExportContent {
    let resultsUpdatedAt: Date?
    let exportedAt: Date
    let language: LeaderboardExportLanguage
    let totalValidResultCount: Int
    let rows: [LeaderboardExportRow]

    var defaultFilename: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        return "modeldial-ranking-\(language.filenameCode)-\(formatter.string(from: exportedAt)).png"
    }

    var hiddenValidResultCount: Int {
        max(0, totalValidResultCount - rows.count)
    }

    func withLanguage(_ newLanguage: LeaderboardExportLanguage) -> LeaderboardExportContent {
        LeaderboardExportContent(
            resultsUpdatedAt: resultsUpdatedAt,
            exportedAt: exportedAt,
            language: newLanguage,
            totalValidResultCount: totalValidResultCount,
            rows: rows
        )
    }
}

struct LeaderboardExportView: View {
    static let canvasSize = CGSize(width: 1080, height: 1920)

    let content: LeaderboardExportContent
    let brandMark: NSImage
    let brandWordmark: NSImage

    private var copy: LeaderboardExportCopy {
        LeaderboardExportCopy(language: content.language)
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            LeaderboardExportPalette.canvas

            RadialGradient(
                colors: [
                    LeaderboardExportPalette.accent.opacity(0.13),
                    Color.clear,
                ],
                center: .topTrailing,
                startRadius: 0,
                endRadius: 620
            )
            .frame(width: 780, height: 780)
            .offset(x: 390, y: -250)
            .blur(radius: 28)

            LinearGradient(
                colors: [
                    Color.white.opacity(0.025),
                    Color.clear,
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            exportBody
                .padding(.horizontal, 64)
                .padding(.top, 64)
                .padding(.bottom, 56)
        }
        .frame(
            width: Self.canvasSize.width,
            height: Self.canvasSize.height,
            alignment: .topLeading
        )
        .clipped()
    }

    private var exportBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            brandHeader

            Rectangle()
                .fill(LeaderboardExportPalette.hairline)
                .frame(height: 1)
                .padding(.top, 28)

            titleBlock
                .padding(.top, 32)

            leaderboardTable
                .padding(.top, 30)

            tagLegend
                .padding(.top, 24)

            Spacer(minLength: 18)

            brandFooter
        }
    }

    private var brandHeader: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(nsImage: brandMark)
                .resizable()
                .interpolation(.high)
                .frame(width: 52, height: 52)

            Image(nsImage: brandWordmark)
                .resizable()
                .interpolation(.high)
                .aspectRatio(contentMode: .fit)
                .frame(width: 164, height: 38)
                .accessibilityLabel("modeldial")

            Spacer()

            Text(copy.resultsAsOfText(content.resultsUpdatedAt))
                .font(.system(size: 22, weight: .medium, design: .monospaced))
                .foregroundStyle(LeaderboardExportPalette.tertiaryText)
        }
        .frame(height: 56)
    }

    private var titleBlock: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(copy.text("模型综合榜单"))
                .font(.system(size: 60, weight: .semibold))
                .tracking(-1.5)
                .foregroundStyle(LeaderboardExportPalette.primaryText)

            Text(
                content.hiddenValidResultCount > 0
                    ? copy.format("%d 个有效结果，展示其中 15 个", content.totalValidResultCount)
                    : copy.format("%d 个有效结果", content.totalValidResultCount)
            )
            .font(.system(size: 24, weight: .medium))
            .foregroundStyle(LeaderboardExportPalette.secondaryText)
        }
    }

    private var leaderboardTable: some View {
        VStack(spacing: 0) {
            tableHeader

            Rectangle()
                .fill(LeaderboardExportPalette.hairline)
                .frame(height: 1)

            ForEach(Array(content.rows.enumerated()), id: \.element.id) { index, row in
                leaderboardRow(row)

                if index < content.rows.count - 1 {
                    Rectangle()
                        .fill(LeaderboardExportPalette.hairline.opacity(0.72))
                        .frame(height: 1)
                        .padding(.horizontal, 20)
                }
            }

            if content.hiddenValidResultCount > 0 {
                Rectangle()
                    .fill(LeaderboardExportPalette.hairline)
                    .frame(height: 1)

                Text(
                    copy.format(
                        "图片展示其中 15 个，另有 %d 个有效结果未展示。",
                        content.hiddenValidResultCount
                    )
                )
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(LeaderboardExportPalette.tertiaryText)
                    .frame(maxWidth: .infinity, minHeight: 50, alignment: .center)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(LeaderboardExportPalette.tableSurface)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(LeaderboardExportPalette.tableBorder, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var tableHeader: some View {
        HStack(spacing: 12) {
            Text(copy.text("排名"))
                .frame(width: 116, alignment: .leading)
            Text(copy.text("模型"))
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 20) {
                Text(copy.text("总分"))
                    .frame(width: 72, alignment: .trailing)
                Text(copy.text("总耗时"))
                    .frame(width: 116, alignment: .trailing)
                Text(copy.text("参考费用"))
                    .frame(width: 116, alignment: .trailing)
            }
            .padding(.leading, 12)
        }
        .font(.system(size: 22, weight: .semibold))
        .foregroundStyle(LeaderboardExportPalette.tertiaryText)
        .frame(height: 62)
        .padding(.horizontal, 20)
    }

    private func leaderboardRow(_ row: LeaderboardExportRow) -> some View {
        HStack(spacing: 12) {
            Text(copy.rankLabel(for: row))
                .font(.system(size: 22, weight: .medium))
                .foregroundStyle(
                    row.isRecommended
                        ? LeaderboardExportPalette.accent
                        : LeaderboardExportPalette.tertiaryText
                )
                .lineLimit(1)
                .minimumScaleFactor(0.68)
                .frame(width: 116, alignment: .leading)

            HStack(alignment: .center, spacing: 10) {
                ProviderLogoMark(providerID: row.providerID)

                Text(row.modelLabel)
                    .font(.system(size: 30, weight: .semibold))
                    .tracking(-0.25)
                    .foregroundStyle(LeaderboardExportPalette.primaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.68)
                    .layoutPriority(1)

                if !row.visibleDecisionTags.isEmpty {
                    Spacer(minLength: 10)
                    LeaderboardExportTagCluster(
                        tags: row.visibleDecisionTags,
                        copy: copy
                    )
                        .fixedSize(horizontal: true, vertical: false)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 20) {
                Text("\(row.score)")
                    .font(.system(size: 40, weight: .semibold, design: .monospaced))
                    .foregroundStyle(
                        row.isRecommended
                            ? LeaderboardExportPalette.accent
                            : LeaderboardExportPalette.primaryText
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                    .frame(width: 72, alignment: .trailing)

                Text(durationText(row.elapsedSeconds))
                    .font(.system(size: 26, weight: .medium, design: .monospaced))
                    .foregroundStyle(valueColor(row.elapsedSeconds))
                    .frame(width: 116, alignment: .trailing)

                Text(referenceCostText(row.referenceCostUsd))
                    .font(.system(size: 26, weight: .medium, design: .monospaced))
                    .foregroundStyle(valueColor(row.referenceCostUsd))
                    .frame(width: 116, alignment: .trailing)
            }
            .padding(.leading, 12)
        }
        .frame(height: rowHeight)
        .padding(.horizontal, 20)
        .background(
            row.isRecommended
                ? LeaderboardExportPalette.recommendedRowFill
                : Color.clear
        )
        .overlay(alignment: .leading) {
            if row.isRecommended {
                RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                    .fill(LeaderboardExportPalette.recommendedRowBar)
                    .frame(width: 3, height: max(34, rowHeight - 28))
            }
        }
    }

    private var rowHeight: CGFloat {
        let count = max(content.rows.count, 1)
        return min(92, max(76, 1140 / CGFloat(count)))
    }

    private var legendTags: [LeaderboardExportTag] {
        var seenKinds = Set<String>()
        return content.rows
            .flatMap { $0.visibleDecisionTags }
            .filter { seenKinds.insert($0.kind).inserted }
            .sorted { $0.priority < $1.priority }
    }

    private var tagLegend: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(legendTags) { tag in
                tagLegendLine(
                    kind: tag.kind,
                    label: copy.tagLabel(tag.kind),
                    description: copy.tagDescription(tag.kind)
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func tagLegendLine(
        kind: String,
        label: String,
        description: String
    ) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Text(label)
                .font(.system(
                    size: content.language == .english ? 19 : 22,
                    weight: .semibold
                ))
                .foregroundStyle(
                    kind == "recommended"
                        ? LeaderboardExportPalette.accent
                        : LeaderboardExportPalette.weakTagText
                )
                .frame(
                    width: content.language == .english ? 152 : 112,
                    height: 38,
                    alignment: .center
                )
                .background(
                    Capsule()
                        .fill(
                            kind == "recommended"
                                ? LeaderboardExportPalette.accent.opacity(0.12)
                                : LeaderboardExportPalette.weakTagFill
                        )
                )
                .overlay {
                    Capsule()
                        .strokeBorder(
                            kind == "recommended"
                                ? LeaderboardExportPalette.accent.opacity(0.34)
                                : LeaderboardExportPalette.weakTagBorder,
                            lineWidth: 1
                        )
                }

            Text(description)
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(LeaderboardExportPalette.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var brandFooter: some View {
        HStack(alignment: .bottom, spacing: 0) {
            Text("modeldial.com")
                .font(.system(size: 25, weight: .semibold, design: .monospaced))
                .foregroundStyle(LeaderboardExportPalette.accent)
                .lineLimit(1)

            Spacer(minLength: 24)

            brandSlogan
                .frame(maxWidth: .infinity, alignment: .bottomTrailing)
        }
        .frame(maxWidth: .infinity, alignment: .bottom)
    }

    private var brandSlogan: some View {
        Text("The best model is the one ready now.")
            .font(.system(size: 27, weight: .medium))
            .tracking(-0.25)
            .foregroundStyle(LeaderboardExportPalette.secondaryText)
            .lineLimit(1)
            .minimumScaleFactor(0.72)
    }

    private func durationText(_ seconds: Double?) -> String {
        guard let rounded = checkedRoundedDurationSeconds(seconds) else {
            return copy.text("未提供")
        }
        if rounded < 60 { return "\(rounded)s" }
        let minutes = rounded / 60
        let remainder = rounded % 60
        return "\(minutes)m \(remainder)s"
    }

    private func referenceCostText(_ cost: Double?) -> String {
        guard let cost else { return copy.text("未提供") }
        if cost > 0, cost < 0.01 { return "<$0.01" }
        return String(format: "$%.2f", cost)
    }

    private func valueColor(_ value: Double?) -> Color {
        value == nil
            ? LeaderboardExportPalette.hintText
            : LeaderboardExportPalette.secondaryText
    }
}

private struct LeaderboardExportTagCluster: View {
    let tags: [LeaderboardExportTag]
    let copy: LeaderboardExportCopy

    private var isCompact: Bool {
        tags.count > 1
    }

    var body: some View {
        HStack(spacing: isCompact ? 5 : 7) {
            ForEach(tags) { tag in
                Text(copy.tagLabel(tag.kind))
                    .font(.system(size: isCompact ? 22 : 24, weight: .semibold))
                    .foregroundStyle(tagForeground(tag.kind))
                    .padding(.horizontal, isCompact ? 8 : 10)
                    .frame(height: 36)
                    .background(
                        Capsule()
                            .fill(tagBackground(tag.kind))
                    )
                    .overlay {
                        Capsule()
                            .strokeBorder(tagBorder(tag.kind), lineWidth: 1)
                    }
            }
        }
    }

    private func tagForeground(_ kind: String) -> Color {
        kind == "recommended"
            ? LeaderboardExportPalette.accent
            : LeaderboardExportPalette.weakTagText
    }

    private func tagBackground(_ kind: String) -> Color {
        kind == "recommended"
            ? LeaderboardExportPalette.accent.opacity(0.12)
            : LeaderboardExportPalette.weakTagFill
    }

    private func tagBorder(_ kind: String) -> Color {
        kind == "recommended"
            ? LeaderboardExportPalette.accent.opacity(0.34)
            : LeaderboardExportPalette.weakTagBorder
    }
}

private enum LeaderboardExportPalette {
    static let canvas = Color(red: 6 / 255, green: 7 / 255, blue: 9 / 255)
    static let tableSurface = Color(red: 12 / 255, green: 15 / 255, blue: 19 / 255)
    static let tableBorder = Color(red: 34 / 255, green: 41 / 255, blue: 49 / 255)
    static let hairline = Color(red: 31 / 255, green: 37 / 255, blue: 44 / 255)
    static let primaryText = Color(red: 238 / 255, green: 242 / 255, blue: 245 / 255)
    static let secondaryText = Color(red: 190 / 255, green: 198 / 255, blue: 206 / 255)
    static let tertiaryText = Color(red: 139 / 255, green: 150 / 255, blue: 161 / 255)
    static let hintText = Color(red: 71 / 255, green: 79 / 255, blue: 88 / 255)
    static let accent = Color(red: 114 / 255, green: 179 / 255, blue: 220 / 255)
    static let weakTagText = Color(red: 165 / 255, green: 179 / 255, blue: 191 / 255)
    static let weakTagFill = Color(red: 31 / 255, green: 42 / 255, blue: 53 / 255)
    static let weakTagBorder = Color(red: 62 / 255, green: 77 / 255, blue: 90 / 255)
    static let recommendedRowFill = accent.opacity(0.045)
    static let recommendedRowBar = accent.opacity(0.86)
}

private struct LeaderboardExportPreviewView: View {
    let content: LeaderboardExportContent
    let brandMark: NSImage
    let brandWordmark: NSImage
    let onClose: () -> Void
    let onSaved: (URL) -> Void
    let onFailure: (String) -> Void

    @State private var selectedLanguage: LeaderboardExportLanguage
    @State private var previewImage: NSImage?
    @State private var pngData: Data?
    @State private var shareURL: URL?
    @State private var statusText: String?
    @State private var renderError: String?

    init(
        content: LeaderboardExportContent,
        brandMark: NSImage,
        brandWordmark: NSImage,
        onClose: @escaping () -> Void,
        onSaved: @escaping (URL) -> Void,
        onFailure: @escaping (String) -> Void
    ) {
        self.content = content
        self.brandMark = brandMark
        self.brandWordmark = brandWordmark
        self.onClose = onClose
        self.onSaved = onSaved
        self.onFailure = onFailure
        _selectedLanguage = State(initialValue: content.language)
    }

    private var localizedContent: LeaderboardExportContent {
        content.withLanguage(selectedLanguage)
    }

    private var copy: LeaderboardExportCopy {
        LeaderboardExportCopy(language: selectedLanguage)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(copy.text("导出榜单图片"))
                        .font(.system(size: 20, weight: .semibold))
                    Text(copy.format(
                        "%d 个有效结果 · PNG · 1080 × 1920",
                        content.totalValidResultCount
                    ))
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Button(copy.text("关闭"), action: onClose)
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 18)

            Divider()

            HStack(alignment: .top, spacing: 24) {
                previewPane
                    .frame(width: 294, height: 522)

                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(copy.text("导出语言"))
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.secondary)

                        Picker(copy.text("导出语言"), selection: $selectedLanguage) {
                            ForEach(LeaderboardExportLanguage.allCases) { language in
                                Text(language.displayName).tag(language)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.segmented)
                    }

                    VStack(alignment: .leading, spacing: 8) {
                        Text(copy.resultsAsOfText(content.resultsUpdatedAt))
                            .font(.system(size: 13, weight: .medium))
                        Text("PNG · 1080 × 1920")
                            .font(.system(size: 12, weight: .medium, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }

                    if let statusText {
                        Label(statusText, systemImage: "checkmark.circle.fill")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.green)
                    } else if let renderError {
                        Label(renderError, systemImage: "exclamationmark.triangle.fill")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Spacer()

                    VStack(spacing: 10) {
                        Button(action: copyImage) {
                            Label(copy.text("复制图片"), systemImage: "doc.on.doc")
                                .frame(maxWidth: .infinity)
                        }
                        .disabled(pngData == nil)

                        Button(action: saveImage) {
                            Label(copy.text("存储…"), systemImage: "square.and.arrow.down")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(pngData == nil)

                        if let shareURL {
                            ShareLink(item: shareURL) {
                                Label(copy.text("分享…"), systemImage: "square.and.arrow.up")
                                    .frame(maxWidth: .infinity)
                            }
                        } else {
                            Button(action: {}) {
                                Label(copy.text("分享…"), systemImage: "square.and.arrow.up")
                                    .frame(maxWidth: .infinity)
                            }
                            .disabled(true)
                        }
                    }
                    .controlSize(.large)
                }
                .frame(width: 270, height: 522, alignment: .topLeading)
            }
            .padding(24)
        }
        .frame(width: 660, height: 640)
        .background(Color(nsColor: .windowBackgroundColor))
        .task(id: selectedLanguage) {
            renderPreview()
        }
    }

    @ViewBuilder
    private var previewPane: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.black.opacity(0.32))

            if let previewImage {
                Image(nsImage: previewImage)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .padding(10)
            } else if renderError == nil {
                ProgressView(copy.text("生成预览中…"))
                    .controlSize(.small)
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.12), lineWidth: 1)
        }
    }

    private func renderPreview() {
        previewImage = nil
        pngData = nil
        shareURL = nil
        statusText = nil
        renderError = nil

        do {
            let data = try LeaderboardImageExporter.renderPNG(
                content: localizedContent,
                brandMark: brandMark,
                brandWordmark: brandWordmark
            )
            guard let image = NSImage(data: data) else {
                throw LeaderboardImageExportError.pngEncodingFailed
            }
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent(localizedContent.defaultFilename)
            try data.write(to: url, options: .atomic)
            previewImage = image
            pngData = data
            shareURL = url
        } catch {
            renderError = error.localizedDescription
        }
    }

    private func copyImage() {
        guard let pngData else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        guard pasteboard.setData(pngData, forType: .png) else {
            statusText = nil
            renderError = copy.text("无法复制图片")
            return
        }
        renderError = nil
        statusText = copy.text("图片已复制")
    }

    private func saveImage() {
        guard let pngData else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.allowsOtherFileTypes = false
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.nameFieldStringValue = localizedContent.defaultFilename
        panel.title = L10n.tr("导出榜单图片")
        panel.message = "PNG · 1080 × 1920"
        panel.prompt = L10n.tr("导出")

        let completion: (NSApplication.ModalResponse) -> Void = { response in
            guard response == .OK, let destinationURL = panel.url else { return }
            do {
                try pngData.write(to: destinationURL, options: .atomic)
                onSaved(destinationURL)
            } catch {
                onFailure(error.localizedDescription)
            }
        }

        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window, completionHandler: completion)
        } else {
            panel.begin(completionHandler: completion)
        }
    }
}

@MainActor
enum LeaderboardImageExporter {
    private static var previewWindowController: NSWindowController?

    static func presentExportFlow(
        content: LeaderboardExportContent,
        omittedCount: Int,
        onSuccess: @escaping (URL) -> Void,
        onFailure: @escaping (String) -> Void
    ) {
        guard let presentingWindow = NSApp.keyWindow
            ?? NSApp.mainWindow
            ?? NSApp.windows.first(where: \.isVisible)
        else {
            onFailure(L10n.tr("无法打开保存窗口，请重新展开榜单后重试。"))
            return
        }

        NSApp.activate(ignoringOtherApps: true)
        guard omittedCount > 0 else {
            presentPreview(
                content: content,
                onSuccess: onSuccess,
                onFailure: onFailure
            )
            return
        }

        let alert = NSAlert()
        let validResultScope: String
        if content.hiddenValidResultCount > 0 {
            validResultScope = L10n.tr(
                "有效结果共 %d 个，图片展示其中 15 个。",
                content.totalValidResultCount
            )
        } else {
            validResultScope = L10n.tr(
                "只导出其余 %d 个有效结果。",
                content.totalValidResultCount
            )
        }
        alert.messageText = L10n.tr("当前榜单有未导出结果")
        alert.informativeText = L10n.tr(
            "当前有 %d 个结果不符合当前榜单范围，将不进入图片（可能因失败、未完成、过期或配置不适用）。%@是否继续？",
            omittedCount,
            validResultScope
        )
        alert.alertStyle = .warning
        alert.addButton(withTitle: L10n.tr("仍要导出"))
        alert.addButton(withTitle: L10n.tr("取消"))
        alert.beginSheetModal(for: presentingWindow) { response in
            guard response == .alertFirstButtonReturn else { return }
            presentPreview(
                content: content,
                onSuccess: onSuccess,
                onFailure: onFailure
            )
        }
    }

    private static func presentPreview(
        content: LeaderboardExportContent,
        onSuccess: @escaping (URL) -> Void,
        onFailure: @escaping (String) -> Void
    ) {
        let brandMark: NSImage
        let brandWordmark: NSImage
        do {
            brandMark = try loadBrandImage(named: "ModeldialShareMark")
            brandWordmark = try loadBrandImage(named: "ModeldialWordmark")
        } catch {
            onFailure(error.localizedDescription)
            return
        }

        let previewWindow = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 660, height: 640),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        previewWindow.title = L10n.tr("导出榜单图片")
        previewWindow.isReleasedWhenClosed = false
        previewWindow.level = NSWindow.Level(
            rawValue: NSWindow.Level.popUpMenu.rawValue + 1
        )
        previewWindow.contentMinSize = NSSize(width: 660, height: 640)
        previewWindow.contentMaxSize = NSSize(width: 660, height: 640)
        previewWindow.center()

        previewWindowController?.close()
        let windowController = NSWindowController(window: previewWindow)
        previewWindowController = windowController

        let closePreview = {
            windowController.close()
            if previewWindowController === windowController {
                previewWindowController = nil
            }
        }

        let preview = LeaderboardExportPreviewView(
            content: content,
            brandMark: brandMark,
            brandWordmark: brandWordmark,
            onClose: closePreview,
            onSaved: { url in
                closePreview()
                onSuccess(url)
            },
            onFailure: { message in
                closePreview()
                onFailure(message)
            }
        )
        previewWindow.contentViewController = NSHostingController(rootView: preview)
        windowController.showWindow(nil)
        previewWindow.makeKeyAndOrderFront(nil)
    }

    static func renderPNG(
        content: LeaderboardExportContent,
        brandMark: NSImage,
        brandWordmark: NSImage
    ) throws -> Data {
        let exportView = LeaderboardExportView(
            content: content,
            brandMark: brandMark,
            brandWordmark: brandWordmark
        )
            .frame(
                width: LeaderboardExportView.canvasSize.width,
                height: LeaderboardExportView.canvasSize.height
            )

        let renderer = ImageRenderer(content: exportView)
        renderer.proposedSize = ProposedViewSize(LeaderboardExportView.canvasSize)
        renderer.scale = 1

        guard let image = renderer.nsImage else {
            throw LeaderboardImageExportError.renderFailed
        }
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(
                  using: .png,
                  properties: [.compressionFactor: 1.0]
              ) else {
            throw LeaderboardImageExportError.pngEncodingFailed
        }
        return png
    }

    private static func loadBrandImage(named name: String) throws -> NSImage {
        guard let url = Bundle.main.url(
            forResource: name,
            withExtension: "svg"
        ), let image = NSImage(contentsOf: url) else {
            throw LeaderboardImageExportError.missingBrandAsset
        }
        return image
    }
}

private enum LeaderboardImageExportError: LocalizedError {
    case missingBrandAsset
    case renderFailed
    case pngEncodingFailed

    var errorDescription: String? {
        switch self {
        case .missingBrandAsset:
            return L10n.tr("未找到 modeldial 品牌标识，请重新构建 App 后再试。")
        case .renderFailed:
            return L10n.tr("榜单图片渲染失败。")
        case .pngEncodingFailed:
            return L10n.tr("榜单图片无法编码为 PNG。")
        }
    }
}
