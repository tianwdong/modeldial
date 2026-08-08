import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard !condition() else { return }
    failureCount += 1
    fputs("FAIL: \(message)\n", stderr)
}

private func decode<T: Decodable>(_ type: T.Type, payload: [String: Any]) throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        type,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func preview(
    mode: String,
    selectionMode: String = "custom",
    valid: Bool = true,
    reason: Any = NSNull(),
    requestedCandidateIds: [String] = ["candidate-a", "candidate-b"],
    appendedCandidateIds: [String] = []
) -> [String: Any] {
    [
        "schema_version": 1,
        "valid": valid,
        "reason": reason,
        "message": NSNull(),
        "requested_selection_mode": selectionMode,
        "requested_custom_round_mode": mode,
        "execution_selection_mode": valid ? "custom" as Any : NSNull(),
        "execution_custom_round_mode": valid ? mode as Any : NSNull(),
        "profile": [
            "id": "quick",
            "label": "快速对比",
            "question_count": 1,
        ],
        "requested_candidate_ids": requestedCandidateIds,
        "effective_candidate_ids": valid ? requestedCandidateIds : [],
        "execution_candidate_ids": valid ? requestedCandidateIds : [],
        "regular_candidate_ids": [],
        "appended_candidate_ids": appendedCandidateIds,
        "skipped_candidate_ids": [],
        "comparison_group": [
            "id": NSNull(),
            "mode": valid ? "custom" as Any : NSNull(),
            "parent_run_id": NSNull(),
            "append_target_group_id": NSNull(),
        ],
        "total_evaluations": valid ? 2 : 0,
        "completed_evaluations": 0,
    ]
}

@main
private struct ScanPlanPreviewTests {
    static func main() throws {
        let payload: [String: Any] = [
            "schema_version": 1,
            "new_round": preview(mode: "new_round"),
            "append": preview(
                mode: "append",
                appendedCandidateIds: ["candidate-b"]
            ),
        ]
        let options = try decode(BridgeCustomScanPlanOptions.self, payload: payload)
        expect(options.newRound.valid, "new-round preview should decode")
        expect(
            options.append.appendedCandidateIds == ["candidate-b"],
            "append preview should preserve backend candidate projection"
        )

        let newRoundPresentation = ScanPlanPreviewPresenter.option(
            for: options.newRound,
            isAppend: false
        )
        expect(newRoundPresentation.isEnabled, "valid preview should enable the option")
        expect(
            newRoundPresentation.subtitle == "新建一轮 · 2 个配置 · 2 次评测",
            "new-round summary should come from the backend projection"
        )

        let invalid = try decode(
            BridgeScanPlanPreview.self,
            payload: preview(
                mode: "append",
                valid: false,
                reason: "append_profile_mismatch"
            )
        )
        let invalidPresentation = ScanPlanPreviewPresenter.option(
            for: invalid,
            isAppend: true
        )
        expect(!invalidPresentation.isEnabled, "invalid preview should disable the option")
        expect(
            invalidPresentation.subtitle == "当前扫描档位与上一轮不一致",
            "stable backend reason should map in a pure presenter"
        )

        let appendIntent = BridgeScanIntent(
            candidateIDs: ["candidate-b", "candidate-a"],
            selectionMode: .custom,
            customRoundMode: .append,
            evaluationProfileID: "client-profile"
        )
        let validatedAppendIntent = appendIntent.applying(options.append)
        expect(
            validatedAppendIntent?.candidateIDs == ["candidate-a", "candidate-b"],
            "validated intent should consume backend candidate ordering"
        )
        expect(
            validatedAppendIntent?.evaluationProfileID == "quick",
            "validated intent should consume the authoritative backend profile"
        )
        expect(
            BridgeScanIntent(
                candidateIDs: ["candidate-a"],
                selectionMode: .custom,
                customRoundMode: .newRound
            ).applying(options.append) == nil,
            "a preview for another custom round mode must not launch"
        )

        let incrementalPreview = try decode(
            BridgeScanPlanPreview.self,
            payload: preview(
                mode: "new_round",
                selectionMode: "incremental_full",
                requestedCandidateIds: []
            )
        )
        let incrementalIntent = BridgeScanIntent(selectionMode: .incrementalFull)
        expect(
            incrementalIntent.applying(incrementalPreview)?.candidateIDs == nil,
            "a nil candidate intent must stay nil after preview validation"
        )

        let regularQuickPreview = try decode(
            BridgeScanPlanPreview.self,
            payload: preview(
                mode: "new_round",
                selectionMode: "regular"
            )
        )
        let regularQuickIntent = BridgeScanIntent(
            selectionMode: .regular,
            evaluationProfileID: "quick"
        )
        expect(
            regularQuickIntent.applying(regularQuickPreview)?.candidateIDs
                == ["candidate-a", "candidate-b"],
            "a backend-selected regular quick pair must replace the empty client intent"
        )

        let upgradeIntent = BridgeScanIntent(upgradeFromRunID: "run-source")
        let validatedUpgradeIntent = upgradeIntent.applying(regularQuickPreview)
        expect(
            validatedUpgradeIntent?.candidateIDs == nil,
            "an inherited profile upgrade must preserve backend source-round semantics"
        )
        expect(
            validatedUpgradeIntent?.evaluationProfileID == "quick",
            "an inherited profile upgrade must consume the backend target profile"
        )

        var unsupported = payload
        unsupported["schema_version"] = 2
        do {
            _ = try decode(BridgeCustomScanPlanOptions.self, payload: unsupported)
            expect(false, "unsupported options schema must fail")
        } catch {
            expect(true, "unsupported options schema must fail")
        }

        var incomplete = preview(mode: "new_round")
        incomplete.removeValue(forKey: "comparison_group")
        do {
            _ = try decode(BridgeScanPlanPreview.self, payload: incomplete)
            expect(false, "missing required preview field must fail")
        } catch {
            expect(true, "missing required preview field must fail")
        }

        if failureCount > 0 {
            exit(1)
        }
        print("Scan plan preview tests passed")
    }
}
