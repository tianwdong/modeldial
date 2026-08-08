import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(type, from: Data(json.utf8))
}

private func verifyDashboardProjection() throws {
    let dashboard = try decode(
        BridgeDashboard.self,
        #"""
        {
          "run_count": 0,
          "hits_516": 0,
          "hit_rate_516": 0,
          "pass_rate": 0,
          "reasoning_tokens_total": 0,
          "cards": [],
          "leaderboard": [],
          "comparison_contract": {
            "schema_version": 1,
            "question_pack_version": "pack-v2",
            "grader_version": "scoring-mode:grader-v2",
            "evaluation_snapshot_id": "local:run-2",
            "pricing_snapshot_id": "pricing-v3",
            "trend_comparability_key": "trend-v2"
          },
          "pairwise_comparisons": [
            {
              "schema_version": 1,
              "pair_key": "current__to__candidate",
              "baseline_candidate_id": "current",
              "baseline_label": "Current",
              "candidate_id": "candidate",
              "candidate_label": "Candidate",
              "comparison_status": "comparable",
              "is_comparable": true,
              "baseline_quality_score": 90,
              "candidate_quality_score": 85,
              "quality_delta_points": -5,
              "baseline_elapsed_seconds": 20,
              "candidate_elapsed_seconds": 15,
              "time_delta_percent": 25,
              "baseline_cost_usd": 0.04,
              "candidate_cost_usd": 0.02,
              "cost_delta_percent": 50,
              "baseline_cost_coverage": "complete",
              "candidate_cost_coverage": "complete",
              "baseline_token_totals": {
                "input_tokens": 200,
                "cached_input_tokens": 20,
                "cache_write_input_tokens": 10,
                "output_tokens": 40,
                "reasoning_tokens": 60
              },
              "candidate_token_totals": {
                "input_tokens": 160,
                "cached_input_tokens": 16,
                "cache_write_input_tokens": 8,
                "output_tokens": 32,
                "reasoning_tokens": 48
              },
              "warning_question_ids": ["q2"]
            }
          ],
          "run_metadata": {
            "run_id": "run-2",
            "question_pack_id": "coding-fast",
            "question_pack_version": "pack-v2",
            "candidate_count": 0,
            "question_count": 0,
            "status": "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": [],
            "regular_candidate_ids": [],
            "comparison_group_mode": "regular",
            "appended_candidate_ids": [],
            "skipped_candidate_ids": [],
            "evaluation_profile_id": "full",
            "evaluation_profile_label": "完整评测",
            "evaluation_result_level": "full",
            "evaluation_score_max": 100,
            "question_ids": []
          }
        }
        """#
    )

    expect(dashboard.comparisonContract?.schemaVersion == 1, "contract schema should decode")
    expect(
        dashboard.comparisonContract?.trendComparabilityKey == "trend-v2",
        "trend comparability key should decode"
    )
    expect(dashboard.pairwiseComparisons.count == 1, "pairwise DTO should decode")
    expect(dashboard.runMetadata.runId == "run-2", "strict run metadata should decode")
    expect(
        dashboard.runMetadata.comparisonGroupMode == "regular",
        "comparison group mode should remain explicit"
    )
    let pair = dashboard.pairwiseComparisons[0]
    expect(pair.id == "current__to__candidate", "pair key should be its stable identity")
    expect(pair.qualityDeltaPoints == -5, "quality delta should decode")
    expect(pair.timeDeltaPercent == 25, "time delta should decode")
    expect(pair.costDeltaPercent == 50, "cost delta should decode")
    expect(pair.baselineTokenTotals.inputTokens == 200, "baseline tokens should decode")
    expect(pair.candidateTokenTotals.reasoningTokens == 48, "candidate tokens should decode")
    expect(pair.warningQuestionIds == ["q2"], "warning question IDs should decode")
}

private func verifyCanonicalRankAndLegacyDefaults() throws {
    let ranked = try decode(
        BridgeLeaderboardEntry.self,
        #"""
        {
          "candidate_id": "candidate",
          "label": "Candidate",
          "model": "model",
          "effort": "high",
          "correct_count": 2,
          "total_count": 2,
          "question_count": 2,
          "semantic_score": 36,
          "semantic_total": 40,
          "score_text": "36/40",
          "pass_rate": 100,
          "truncation_hits": 0,
          "canonical_rank": 1,
          "canonical_rank_label": "并列第 1 名",
          "canonical_rank_status": "tied",
          "canonical_rank_semantics": "competition",
          "canonical_rank_score_basis": "overall_score",
          "is_canonical_rank_tied": true,
          "canonical_rank_tie_count": 2,
          "canonical_labels": ["并列第 1 名", "推荐"]
        }
        """#
    )
    expect(ranked.canonicalRank == 1, "canonical rank should decode")
    expect(ranked.canonicalRankLabel == "并列第 1 名", "rank label should decode")
    expect(ranked.canonicalRankStatus == "tied", "rank status should decode")
    expect(ranked.isCanonicalRankTied, "tie flag should decode")
    expect(ranked.canonicalRankTieCount == 2, "tie count should decode")
    expect(ranked.canonicalLabels == ["并列第 1 名", "推荐"], "canonical labels should decode")

    let legacy = try decode(
        BridgeLeaderboardEntry.self,
        #"""
        {
          "label": "Legacy",
          "model": "legacy-model",
          "effort": "high",
          "correct_count": 0,
          "total_count": 0,
          "question_count": 0,
          "semantic_score": 0,
          "semantic_total": 0,
          "score_text": "0/0",
          "pass_rate": 0,
          "truncation_hits": 0
        }
        """#
    )
    expect(legacy.canonicalRank == nil, "legacy rank should remain absent")
    expect(legacy.canonicalRankLabel == "暂不排名", "legacy rank label should be explicit")
    expect(legacy.canonicalRankStatus == "unranked", "legacy rank status should be unranked")
    expect(legacy.canonicalRankSemantics == "competition", "legacy semantics should be stable")
    expect(!legacy.isCanonicalRankTied, "legacy row should not claim a tie")
    expect(legacy.canonicalRankTieCount == 0, "legacy tie count should be zero")
    expect(legacy.canonicalLabels.isEmpty, "legacy labels should stay empty")
}

private func verifyRecommendationQualityGuard() throws {
    let decision = try decode(
        BridgeRecommendationDecisionV2.self,
        #"""
        {
          "current_model_configuration_id": "current",
          "candidate_model_configuration_id": "candidate",
          "comparison_candidate_model_configuration_id": "candidate",
          "comparison_candidate_reasons": [],
          "decision": "recommend",
          "reason": "material_time_gain",
          "quality_tradeoff": false,
          "quality_warning_question_ids": [],
          "quality_guard": {
            "schema_version": 1,
            "status": "passed",
            "rule": "maximum_loss",
            "preference": "smart",
            "decision": "recommend",
            "threshold_points": 5,
            "score_delta_points": -3,
            "passed": true
          },
          "quality": {"current_score": 84, "candidate_score": 81, "score_delta": -3},
          "time": {"current_seconds": 600, "candidate_seconds": 400, "reduction_percent": 33.3},
          "reference_cost": {"current_usd": 1, "candidate_usd": 0.6, "reduction_percent": 40},
          "primary_benefit": {"kind": "time", "reduction_percent": 33.3}
        }
        """#
    )
    expect(decision.qualityGuard?.schemaVersion == 1, "quality guard schema should decode")
    expect(decision.qualityGuard?.status == "passed", "quality guard status should decode")
    expect(decision.qualityGuard?.thresholdPoints == 5, "quality guard threshold should decode")
    expect(decision.qualityGuard?.passed == true, "quality guard outcome should decode")
}

private func verifyPublisherLeaderboardProjection() throws {
    let snapshot = try decode(
        BridgeReferenceSnapshot.self,
        #"""
        {
          "schema_version": 1,
          "kind": "first_party_snapshot",
          "batch_id": "batch-2",
          "published_at": "2026-07-29T10:00:00Z",
          "question_pack_version": "pack-v2",
          "grader_version": "grader-v2",
          "entry_count": 0,
          "entries": [],
          "provenance": {
            "kind": "first_party_snapshot",
            "public_official_snapshot": true
          },
          "pairwise_comparisons": [{
            "schema_version": 1,
            "pair_key": "current__to__candidate",
            "baseline_candidate_id": "current",
            "baseline_label": "Current",
            "candidate_id": "candidate",
            "candidate_label": "Candidate",
            "comparison_status": "comparable",
            "is_comparable": true,
            "baseline_quality_score": 72,
            "candidate_quality_score": 83,
            "quality_delta_points": 11,
            "baseline_elapsed_seconds": 52.083,
            "candidate_elapsed_seconds": 13.05,
            "time_delta_percent": 74.961,
            "baseline_cost_usd": 0.151,
            "candidate_cost_usd": 0.875,
            "cost_delta_percent": -479.47,
            "baseline_cost_coverage": "complete",
            "candidate_cost_coverage": "complete",
            "baseline_token_totals": {},
            "candidate_token_totals": {},
            "warning_question_ids": []
          }],
          "leaderboard_projection": {
            "schema_version": 1,
            "source": "publisher",
            "ranking_rule": "score_desc_hard_failures_elapsed_cost_id_v1",
            "trend_rule": "same_compatibility_key_recent_6_v1",
            "questions": [{
              "id": "q1",
              "short_label": "Q1",
              "title": "Question 1",
              "capability_id": "reasoning",
              "capability_label": "Reasoning",
              "detail_label": "Detail",
              "ordinal": 1
            }],
            "rows": [{
              "model_configuration_id": "candidate",
              "rank": 1,
              "target_labels": [{"id": "highest_score", "label": "最高分"}],
              "decision_tags": [{"kind": "recommended"}],
              "question_scores": [{"question_id": "q1", "score": 18}],
              "trend": {
                "compatibility_key": "sha256:abc",
                "sample_count": 2,
                "comparable": true,
                "stable_ranking_eligible": false,
                "points": [
                  {"batch_id": "batch-1", "published_at": "2026-07-28T10:00:00Z", "score": 17, "elapsed_ms": 1200},
                  {"batch_id": "batch-2", "published_at": "2026-07-29T10:00:00Z", "score": 18, "elapsed_ms": 1000}
                ]
              }
            }]
          }
        }
        """#
    )
    let projection = snapshot.leaderboardProjection
    expect(projection?.schemaVersion == 1, "publisher projection schema should decode")
    expect(projection?.questions.first?.shortLabel == "Q1", "question semantics should decode")
    expect(projection?.rows.first?.rank == 1, "publisher rank should decode")
    expect(projection?.rows.first?.targetLabels.first?.label == "最高分", "publisher labels should decode")
    expect(projection?.rows.first?.decisionTags.first?.kind == "recommended", "publisher decision tags should decode")
    expect(projection?.rows.first?.trend.compatibilityKey == "sha256:abc", "compatibility key should decode")
    expect(projection?.rows.first?.trend.points.count == 2, "publisher trend points should decode")
    expect(snapshot.pairwiseComparisons.count == 1, "reference pairwise projection should decode")
    expect(snapshot.pairwiseComparisons.first?.candidateId == "candidate", "reference pairwise candidate should decode")

    let dataset = ComparisonSelectionPresenter.dataset(
        usesLocalDataset: false,
        usesOfficialSnapshot: true,
        localStatistics: nil,
        localLeaderboard: [],
        localPairwiseComparisons: [],
        officialSnapshot: snapshot
    )
    expect(dataset.pairwiseComparisons.count == 1, "official pairwise projection should reach comparison dataset")

    let developmentSeed = try decode(
        BridgeReferenceSnapshot.self,
        #"""
        {
          "schema_version": 1,
          "kind": "development_seed",
          "batch_id": "seed-1",
          "published_at": "2000-01-01T00:00:00Z",
          "question_pack_version": "pack-v2",
          "grader_version": "grader-v2",
          "entry_count": 0,
          "entries": [],
          "provenance": {
            "public_official_snapshot": true
          },
          "pairwise_comparisons": []
        }
        """#
    )
    expect(!developmentSeed.isPublicOfficialSnapshot, "development seed must fail the official trust check")
    let missingProvenance = try decode(
        BridgeReferenceSnapshot.self,
        #"""
        {
          "schema_version": 1,
          "kind": "first_party_snapshot",
          "batch_id": "missing-provenance",
          "published_at": "2026-07-29T10:00:00Z",
          "question_pack_version": "pack-v2",
          "grader_version": "grader-v2",
          "entry_count": 0,
          "entries": [],
          "provenance": {
            "public_official_snapshot": true
          },
          "pairwise_comparisons": []
        }
        """#
    )
    expect(!missingProvenance.isPublicOfficialSnapshot, "missing provenance kind must fail the official trust check")
    let mismatchedProvenance = try decode(
        BridgeReferenceSnapshot.self,
        #"""
        {
          "schema_version": 1,
          "kind": "first_party_snapshot",
          "batch_id": "mismatched-provenance",
          "published_at": "2026-07-29T10:00:00Z",
          "question_pack_version": "pack-v2",
          "grader_version": "grader-v2",
          "entry_count": 0,
          "entries": [],
          "provenance": {
            "kind": "development_seed",
            "public_official_snapshot": true
          },
          "pairwise_comparisons": []
        }
        """#
    )
    expect(!mismatchedProvenance.isPublicOfficialSnapshot, "mismatched provenance kind must fail the official trust check")
    let untrustedDataset = ComparisonSelectionPresenter.dataset(
        usesLocalDataset: false,
        usesOfficialSnapshot: true,
        localStatistics: nil,
        localLeaderboard: [],
        localPairwiseComparisons: [],
        officialSnapshot: developmentSeed
    )
    expect(untrustedDataset.referenceSnapshot == nil, "untrusted snapshots must not enter comparison")
    expect(untrustedDataset.pairwiseComparisons.isEmpty, "untrusted pairwise data must be suppressed")
}

@main
private struct ComparisonProjectionDecodingTests {
    static func main() {
        do {
            try verifyDashboardProjection()
            try verifyCanonicalRankAndLegacyDefaults()
            try verifyRecommendationQualityGuard()
            try verifyPublisherLeaderboardProjection()
        } catch {
            failureCount += 1
            fputs("FAIL: unexpected decoding error: \(error)\n", stderr)
        }

        if failureCount > 0 {
            exit(1)
        }

        print("Comparison projection decoding tests passed")
    }
}
