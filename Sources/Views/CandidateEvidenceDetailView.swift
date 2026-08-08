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
