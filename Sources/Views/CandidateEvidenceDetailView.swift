import SwiftUI

struct CandidateEvidenceDetailView: View {
    let entry: BridgeLeaderboardEntry
    let evidenceState: String
    let onDismiss: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            evidenceHeader

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            ScrollView {
                VStack(alignment: .leading, spacing: LayoutRhythm.section) {
                    evidenceSection {
                        Text("身份").font(Typography.sectionTitle)
                        evidenceRow("来源", entry.sourceId ?? "未记录")
                        evidenceRow("连接", entry.connectionId ?? "未记录")
                        evidenceRow("模型簇", entry.familyId ?? "未记录")
                        if let variant = entry.variantId, !variant.isEmpty {
                            evidenceRow("变体", variant)
                        }
                        if let effort = visibleEffort {
                            evidenceRow("档位", effort)
                        }
                    }

                    evidenceSection {
                        Text("当前有效成绩").font(Typography.sectionTitle)
                        evidenceRow("题目总分", scoreText)
                        evidenceRow("总分", entry.overallScoreText ?? scoreText)
                        evidenceRow("有效时间", entry.validCompletedAt ?? "未记录")
                        evidenceRow("题包", entry.questionPackVersion)
                        evidenceRow("Run", entry.validRunId ?? "未记录")
                    }

                    evidenceSection {
                        Text("最新尝试").font(Typography.sectionTitle)
                        evidenceRow("状态", entry.latestAttemptStatus ?? "未记录")
                        evidenceRow("时间", entry.latestAttemptAt ?? "未记录")
                        if let category = entry.latestAttemptErrorCategory {
                            evidenceRow("失败分类", category)
                        }
                        if let summary = entry.latestAttemptErrorSummary {
                            evidenceRow("失败摘要", summary)
                        }
                    }

                    evidenceSection {
                        Text("逐题结果").font(Typography.sectionTitle)
                        if entry.questionResults.isEmpty {
                            Text("暂无逐题结果")
                                .font(Typography.settingsCardBody)
                                .foregroundStyle(IslandVisual.secondaryText)
                        } else {
                            ForEach(entry.questionResults) { result in
                                evidenceRow(result.semanticDisplayName, result.outcome.displayName)
                            }
                        }
                    }

                    if evidenceState == "retained_after_failure" || entry.isUsingPreviousValidResult {
                        evidenceSection {
                            Text("旧成绩说明").font(Typography.sectionTitle)
                            Text("本次尝试失败，当前比较继续使用上一次同题包的有效成绩。最新失败与旧成绩时间分别列出，不会把失败结果覆盖成新成绩。")
                                .font(Typography.settingsCardBody)
                                .foregroundStyle(IslandColor.alertAmber)
                        }
                    }
                }
                .padding(.horizontal, LayoutRhythm.section)
                .padding(.vertical, LayoutRhythm.section)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IslandColor.panel)
        .foregroundStyle(IslandVisual.primaryText)
        .clipShape(RoundedRectangle(cornerRadius: IslandRadius.modal, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: IslandRadius.modal, style: .continuous)
                .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
        }
        .shadow(color: .black.opacity(0.45), radius: 24, y: 12)
    }

    private var evidenceHeader: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.label)
                    .font(Typography.pageTitle)
                Text(entry.candidateId)
                    .font(Typography.caption)
                    .foregroundStyle(IslandVisual.tertiaryText)
            }
            Spacer()
            Text(entry.overallScoreText ?? scoreText)
                .font(Typography.settingsStatValue.monospaced())
                .foregroundStyle(IslandVisual.primaryText)
            Button(action: onDismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(IslandIconButtonStyle())
            .keyboardShortcut(.cancelAction)
            .accessibilityLabel(L10n.tr("关闭"))
        }
        .padding(.horizontal, LayoutRhythm.section)
        .padding(.vertical, LayoutRhythm.standard)
    }

    private var visibleEffort: String? {
        let value = entry.effort
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        guard !value.isEmpty, value != "default" else { return nil }
        return value
    }

    private var scoreText: String {
        entry.overallScoreText ?? entry.scoreText
    }

    private func evidenceSection<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact, content: content)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, LayoutRhythm.standard)
            .overlay(alignment: .bottom) {
                Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
            }
    }

    private func evidenceRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: LayoutRhythm.standard) {
            Text(L10n.tr(label))
                .font(Typography.label)
                .foregroundStyle(IslandVisual.tertiaryText)
                .frame(width: 96, alignment: .leading)
            Text(L10n.tr(value))
                .font(Typography.caption)
                .foregroundStyle(IslandVisual.secondaryText)
                .textSelection(.enabled)
        }
    }
}

struct OfficialCandidateEvidenceDetailView: View {
    let entry: BridgeReferenceSnapshotEntry
    let sourceSnapshot: BridgeReferenceSnapshot
    let questions: [BridgeReferenceLeaderboardQuestion]
    let onDismiss: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            evidenceHeader

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            ScrollView {
                VStack(alignment: .leading, spacing: LayoutRhythm.section) {
                    evidenceSection {
                        Text("身份").font(Typography.sectionTitle)
                        evidenceRow("来源", "官网实测")
                        evidenceRow("提供方", entry.modelConfiguration.providerId)
                        evidenceRow("模型", entry.modelConfiguration.canonicalModelId)
                        evidenceRow("档位", entry.modelConfiguration.reasoningEffort)
                        evidenceRow("服务层", entry.modelConfiguration.serviceTier)
                        evidenceRow("路由", entry.modelConfiguration.routeType)
                        if let routeFingerprint = entry.routeFingerprint {
                            evidenceRow("路由指纹", routeFingerprint)
                        }
                    }

                    evidenceSection {
                        Text("官网有效成绩").font(Typography.sectionTitle)
                        evidenceRow("总分", scoreText)
                        evidenceRow("耗时", durationText)
                        evidenceRow("参考费用", costText)
                        evidenceRow("完成时间", entry.completedAt ?? "未记录")
                        evidenceRow("发布时间", sourceSnapshot.publishedAt)
                    }

                    evidenceSection {
                        Text("证据版本").font(Typography.sectionTitle)
                        evidenceRow("题包", sourceSnapshot.questionPackVersion)
                        evidenceRow("评分器", sourceSnapshot.graderVersion)
                        evidenceRow("批次", sourceSnapshot.batchId)
                    }

                    evidenceSection {
                        Text("逐题结果").font(Typography.sectionTitle)
                        if questionRows.isEmpty {
                            Text("暂无逐题结果")
                                .font(Typography.settingsCardBody)
                                .foregroundStyle(IslandVisual.secondaryText)
                        } else {
                            ForEach(questionRows) { row in
                                evidenceRow(row.label, row.score)
                            }
                        }
                    }
                }
                .padding(.horizontal, LayoutRhythm.section)
                .padding(.vertical, LayoutRhythm.section)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IslandColor.panel)
        .foregroundStyle(IslandVisual.primaryText)
        .clipShape(RoundedRectangle(cornerRadius: IslandRadius.modal, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: IslandRadius.modal, style: .continuous)
                .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
        }
        .shadow(color: .black.opacity(0.45), radius: 24, y: 12)
    }

    private var evidenceHeader: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(
                    ModelIdentityPresentation.displayLabel(
                        model: entry.modelConfiguration.canonicalModelId,
                        effort: entry.modelConfiguration.reasoningEffort
                    )
                )
                .font(Typography.pageTitle)
                Text(entry.modelConfigurationId)
                    .font(Typography.caption)
                    .foregroundStyle(IslandVisual.tertiaryText)
            }
            Spacer()
            Text(scoreText)
                .font(Typography.settingsStatValue.monospaced())
                .foregroundStyle(IslandVisual.primaryText)
            Button(action: onDismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(IslandIconButtonStyle())
            .keyboardShortcut(.cancelAction)
            .accessibilityLabel(L10n.tr("关闭"))
        }
        .padding(.horizontal, LayoutRhythm.section)
        .padding(.vertical, LayoutRhythm.standard)
    }

    private var scoreText: String {
        String(format: "%.1f / %.1f", entry.score, entry.maxScore)
    }

    private var durationText: String {
        L10n.tr("%.1f 秒", entry.elapsedMs / 1_000)
    }

    private var costText: String {
        guard let cost = entry.estimatedApiCostUsd else { return L10n.tr("未记录") }
        let suffix = entry.costCoverage == "partial" ? L10n.tr("（部分）") : ""
        return String(format: "$%.4f", cost) + suffix
    }

    private var questionRows: [OfficialQuestionEvidenceRow] {
        let questionsByID = Dictionary(
            questions.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        let orderedIDs = questions
            .sorted { $0.ordinal < $1.ordinal }
            .map(\.id)
        let trailingIDs = entry.questionScores.keys
            .filter { !orderedIDs.contains($0) }
            .sorted()
        return (orderedIDs + trailingIDs).compactMap { questionID in
            guard let score = entry.questionScores[questionID] else { return nil }
            let label = questionsByID[questionID].map {
                "\($0.shortLabel) · \($0.capabilityLabel)"
            } ?? questionID
            return OfficialQuestionEvidenceRow(
                id: questionID,
                label: label,
                score: String(format: "%.1f", score)
            )
        }
    }

    private func evidenceSection<Content: View>(
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact, content: content)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, LayoutRhythm.standard)
            .overlay(alignment: .bottom) {
                Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
            }
    }

    private func evidenceRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: LayoutRhythm.standard) {
            Text(L10n.tr(label))
                .font(Typography.label)
                .foregroundStyle(IslandVisual.tertiaryText)
                .frame(width: 96, alignment: .leading)
            Text(L10n.tr(value))
                .font(Typography.caption)
                .foregroundStyle(IslandVisual.secondaryText)
                .textSelection(.enabled)
        }
    }
}

private struct OfficialQuestionEvidenceRow: Identifiable {
    let id: String
    let label: String
    let score: String
}
