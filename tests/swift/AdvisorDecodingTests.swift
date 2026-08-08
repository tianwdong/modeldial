import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private struct AdvisorEnvelope: Decodable {
    let advisor: BridgeAdvisorDecision?
    let diagnostics: BridgeDiagnosticSummary?
    let advisorV2Evidence: BridgeAdvisorV2Evidence?
    let recommendationPortfolioV2: BridgeRecommendationPortfolioV2?
    let referenceSnapshotFeed: BridgeReferenceSnapshotFeed?
    let recommendationUse: BridgeRecommendationUseSummary?
}

private let payload = """
{
  "advisor": {
    "schema_version": 1,
    "ruleset_version": "advisor-p0-v1",
    "decision": "trial_switch",
    "short_circuit_reason": "qualified_material_benefit",
    "current_model_configuration_id": "current",
    "candidate_model_configuration_id": "candidate",
    "generated_at": "2026-07-24T10:00:00Z",
    "valid_until": "2026-08-07T10:00:00Z",
    "quality": {
      "current_score": 82,
      "candidate_score": 84,
      "score_delta": 2,
      "guard_passed": true,
      "critical_regressions": [],
      "hard_failures": []
    },
    "benefits": {
      "quota_reduction_percent_range": [18.0, 31.0],
      "additional_similar_tasks_range": [2.0, 5.0],
      "quota_evidence": "official_window_attributed",
      "active_time_reduction_percent": 25.0,
      "active_time_evidence": "real_workload",
      "standard_cost_reduction_percent": 40.0,
      "standard_cost_evidence": "real_workload",
      "pricing_snapshot_id": "pricing-v1-2026-07-24-1"
    },
    "confidence": 0.8,
    "confidence_level": "high",
    "reasons": ["候选通过质量护栏并达到实质收益门槛。"],
    "limitations": ["五题只作为能力护栏。"],
    "next_action": "先用候选完成 5 个真实任务，再复核"
  },
  "diagnostics": {
    "schema_version": 1,
    "generated_at": "2026-07-24T10:01:00Z",
    "overall_status": "attention",
    "app_server": {
      "status": "fresh",
      "last_read_at": "2026-07-24T10:00:00Z",
      "read_duration_ms": 42
    },
    "capabilities": {
      "model_catalog": "not_checked",
      "account": "available",
      "rate_limits": "not_applicable"
    },
    "session_history": {
      "source_count": 2,
      "discovered_file_count": 8,
      "sampled_file_count": 8,
      "parsed_file_count": 7,
      "failed_file_count": 0,
      "unknown_file_count": 1,
      "deduplicated_file_count": 0,
      "budget_limited_file_count": 0,
      "visible_started_at": "2026-07-20T10:00:00Z",
      "continuous_since": "2026-07-24T10:00:00Z",
      "coverage_complete": false,
      "gap_detected": true,
      "upstream_retention_risk": "unknown"
    },
    "behavior": {
      "completed_work_units": 5,
      "observed_work_units": 4,
      "coverage_percent": 80,
      "edit_work_units": 3,
      "retry_observed_edit_work_units": 2,
      "retry_indeterminate_edit_work_units": 1
    },
    "versions": {
      "question_pack_id": "coding-fast",
      "question_pack_version": "coding-fast-v4.10",
      "advisor_ruleset_version": "advisor-p0-v1",
      "pricing_snapshot_id": "pricing-v1-2026-07-24-1"
    },
    "advisor_short_circuit_reason": "current_evaluation_incomplete",
    "quota_status": "not_applicable",
    "quota_rejected_intervals": {}
  },
  "advisor_v2_evidence": {
    "schema_version": 2,
    "source_mode": "official_snapshot",
    "resolved_data_source": "official_snapshot",
    "source_reason": "official_snapshot_selected",
    "source_snapshot_id": "snapshot-1",
    "current_model_configuration_id": "current",
    "current_status": "ready",
    "eligible_candidate_ids": ["candidate"],
    "testable_candidate_ids": ["candidate-2"],
    "candidate_decisions": [
      {
        "model_configuration_id": "candidate",
        "status": "eligible",
        "reasons": []
      }
    ],
    "resolved_result_rows": []
  },
  "recommendation_portfolio_v2": {
    "schema_version": 2,
    "source_mode": "official_snapshot",
    "source_mode_by_configuration_id": {
      "current": "official_snapshot"
    },
    "resolved_data_source": "official_snapshot",
    "source_resolution_reason": "official_snapshot_selected",
    "preference": "smart",
    "representative_configuration_id": "current",
    "representative_reason": "currently_producing",
    "representative_evidence": {
      "schema_version": 2,
      "source_mode": "local_evaluation",
      "resolved_data_source": "local_evaluation",
      "source_reason": "local_exact_match",
      "source_snapshot_id": "local:representative-run",
      "pricing_snapshot_id": "pricing-v1-2026-07-24-1",
      "current_model_configuration_id": "current",
      "current_status": "ready",
      "eligible_candidate_ids": ["candidate"],
      "testable_candidate_ids": [],
      "candidate_decisions": [],
      "resolved_result_rows": [
        {
          "model_configuration_id": "current",
          "display_rank": 2,
          "overall_score": 84
        }
      ]
    },
    "status": "recommend",
    "decisions": [
      {
        "current_model_configuration_id": "current",
        "candidate_model_configuration_id": "candidate",
        "decision": "recommend",
        "reason": "material_time_and_cost_reduction",
        "quality_tradeoff": true,
        "quality_warning_question_ids": ["Q3"],
        "quality": {
          "current_score": 84,
          "candidate_score": 81,
          "score_delta": -3
        },
        "time": {
          "current_seconds": 621,
          "candidate_seconds": 460,
          "reduction_percent": 25.9
        },
        "reference_cost": {
          "current_usd": 0.57,
          "candidate_usd": 0.34,
          "reduction_percent": 40.4
        },
        "primary_benefit": {
          "kind": "reference_cost",
          "reduction_percent": 40.4
        }
      }
    ],
    "testable_candidate_ids": ["candidate-2"],
    "unmapped_active_session_count": 1
  },
  "recommendation_use": {
    "schema_version": 1,
    "epochs": [
      {
        "schema_version": 1,
        "use_epoch_id": "use_epoch_1",
        "recommendation_id": "rec_1",
        "current_model_configuration_id": "current",
        "recommended_model_configuration_id": "candidate",
        "resolved_data_source": "official_snapshot",
        "evaluation_snapshot_id": "snapshot-1",
        "pricing_snapshot_id": "pricing-v1-2026-07-24-1",
        "started_at": "2026-07-24T10:00:00Z",
        "ended_at": null,
        "end_reason": null,
        "observed_candidate_session_count": 2,
        "observed_candidate_work_unit_count": 3,
        "observed_candidate_reference_cost_usd": 0.21,
        "observed_candidate_response_wait_ms": null,
        "estimated_reference_cost_delta_usd": -0.14,
        "estimated_model_wait_delta_ms": null,
        "lifecycle_status": "open",
        "estimate_status": "estimated",
        "estimate_basis": "observed_candidate_usage_x_full_pack_ratio"
      }
    ],
    "representative_epoch": {
      "schema_version": 1,
      "use_epoch_id": "use_epoch_1",
      "recommendation_id": "rec_1",
      "current_model_configuration_id": "current",
      "recommended_model_configuration_id": "candidate",
      "resolved_data_source": "official_snapshot",
      "evaluation_snapshot_id": "snapshot-1",
      "pricing_snapshot_id": "pricing-v1-2026-07-24-1",
      "started_at": "2026-07-24T10:00:00Z",
      "ended_at": null,
      "end_reason": null,
      "observed_candidate_session_count": 2,
      "observed_candidate_work_unit_count": 3,
      "observed_candidate_reference_cost_usd": 0.21,
      "observed_candidate_response_wait_ms": null,
      "estimated_reference_cost_delta_usd": -0.14,
      "estimated_model_wait_delta_ms": null,
      "lifecycle_status": "open",
      "estimate_status": "estimated",
      "estimate_basis": "observed_candidate_usage_x_full_pack_ratio"
    },
    "benefit_summary": {
      "schema_version": 1,
      "status": "estimated",
      "observed_work_unit_count": 3,
      "reference_cost_work_unit_count": 3,
      "model_wait_work_unit_count": 2,
      "reference_cost_delta_usd": -0.14,
      "model_wait_delta_ms": -55000,
      "reference_cost_epoch_count": 1,
      "model_wait_epoch_count": 1,
      "latest_observed_at": "2026-07-24T11:00:00Z",
      "estimate_basis": "observed_candidate_usage_x_frozen_full_pack_ratio"
    },
    "value_summary": {
      "schema_version": 1,
      "mode": "realized",
      "period_start": "2026-07-24T10:00:00Z",
      "period_end": "2026-07-24T11:00:00Z",
      "period_days": null,
      "current_model_configuration_id": null,
      "candidate_model_configuration_id": null,
      "completed_work_unit_count": 3,
      "reference_cost_usd": null,
      "reference_cost_status": "estimated",
      "model_wait_ms": null,
      "model_wait_status": "estimated",
      "model_wait_work_unit_count": 2,
      "reference_cost_delta_usd": -0.14,
      "model_wait_delta_ms": -55000,
      "pricing_snapshot_id": "pricing-v1-2026-07-24-1",
      "coverage_complete": null,
      "basis": "observed_candidate_usage"
    }
  },
  "reference_snapshot_feed": {
    "schema_version": 1,
    "status": "loaded",
    "kind": "development_seed",
    "latest": {
      "schema_version": 1,
      "kind": "development_seed",
      "batch_id": "snapshot-1",
      "published_at": "2026-07-24T13:55:35+08:00",
      "question_pack_version": "coding-fast-v4.10",
      "grader_version": "semantic-v2",
      "entry_count": 1,
      "entries": [
        {
          "model_configuration_id": "candidate",
          "model_configuration": {
            "provider_id": "openai",
            "raw_model_id": "gpt-5.6-sol",
            "canonical_model_id": "gpt-5.6-sol",
            "display_name": "gpt-5.6-sol / high",
            "reasoning_effort": "high",
            "service_tier": "chatgpt_subscription",
            "route_type": "official_login"
          },
          "advisor_eligible": true,
          "score": 81,
          "max_score": 100,
          "elapsed_ms": 460000,
          "estimated_api_cost_usd": 0.34,
          "cost_coverage": "complete",
          "question_scores": {"Q1": 16, "Q2": 17}
        }
      ]
    },
    "snapshots": [],
    "delivery": {
      "source": "http",
      "refresh_status": "refreshed",
      "error_code": null
    }
  }
}
"""

private func verifyAdvisorDecoding() {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase

    do {
        let envelope = try decoder.decode(AdvisorEnvelope.self, from: Data(payload.utf8))
        expect(envelope.advisor?.decision == "trial_switch", "decision decodes")
        expect(envelope.advisor?.presentationTitle == "可以有限试用候选", "title maps")
        expect(envelope.advisor?.quality.guardPassed == true, "quality decodes")
        expect(
            envelope.advisor?.benefits.quotaReductionPercentRange == [18.0, 31.0],
            "benefit range decodes"
        )
        expect(
            envelope.advisor?.primaryReason == "候选通过质量护栏并达到实质收益门槛。",
            "primary reason uses backend evidence"
        )
        expect(envelope.diagnostics?.appServer.readDurationMs == 42, "diagnostic latency decodes")
        expect(envelope.diagnostics?.sessionHistory.unknownFileCount == 1, "unknown files decode")
        expect(
            envelope.diagnostics?.behavior.retryIndeterminateEditWorkUnits == 1,
            "behavior denominator decodes"
        )
        expect(envelope.advisorV2Evidence?.currentStatus == "ready", "V2 evidence decodes")
        expect(
            envelope.recommendationPortfolioV2?.representativeDecision?.candidateModelConfigurationId
                == "candidate",
            "V2 representative decision decodes"
        )
        expect(
            envelope.recommendationPortfolioV2?.representativeEvidence?.sourceSnapshotId
                == "local:representative-run",
            "V2 representative evidence decodes"
        )
        expect(
            envelope.recommendationPortfolioV2?.representativeEvidence?
                .resolvedResultRows.first?.displayRank == 2,
            "V2 display rank decodes"
        )
        expect(
            envelope.recommendationPortfolioV2?.representativeDecision?.referenceCost.reductionPercent
                == 40.4,
            "V2 reference cost decodes"
        )
        expect(
            envelope.referenceSnapshotFeed?.latest?.entries.first?.modelConfiguration.displayName
                == "gpt-5.6-sol / high",
            "reference snapshot identity decodes"
        )
        expect(
            envelope.referenceSnapshotFeed?.delivery?.source == "http",
            "reference snapshot delivery decodes"
        )
        expect(
            envelope.referenceSnapshotFeed?.latest?.entries.first?.questionScores["Q2"] == 17,
            "reference question scores decode"
        )
        expect(
            envelope.recommendationUse?.representativeEpoch?.observedCandidateWorkUnitCount == 3,
            "recommendation use epoch decodes"
        )
        expect(
            envelope.recommendationUse?.representativeEpoch?.estimatedReferenceCostDeltaUsd == -0.14,
            "signed observed reference delta decodes"
        )
        expect(
            envelope.recommendationUse?.benefitSummary?.modelWaitDeltaMs == -55_000,
            "model wait benefit decodes"
        )
        expect(
            envelope.recommendationUse?.benefitSummary?.referenceCostDeltaUsd == -0.14,
            "Token-equivalent dollar benefit decodes"
        )
        expect(
            envelope.recommendationUse?.benefitSummary?.referenceCostWorkUnitCount == 3,
            "Token-equivalent dollar coverage decodes"
        )
        expect(
            envelope.recommendationUse?.benefitSummary?.modelWaitWorkUnitCount == 2,
            "model wait coverage decodes"
        )
        expect(
            envelope.recommendationUse?.valueSummary?.mode == "realized",
            "user-facing value mode decodes"
        )
        expect(
            envelope.recommendationUse?.valueSummary?.referenceCostDeltaUsd == -0.14,
            "user-facing value delta decodes"
        )

        let legacy = try decoder.decode(AdvisorEnvelope.self, from: Data("{}".utf8))
        expect(legacy.advisor == nil, "missing advisor remains backward compatible")
        expect(legacy.diagnostics == nil, "missing diagnostics remains backward compatible")
        expect(legacy.advisorV2Evidence == nil, "missing V2 evidence remains backward compatible")
        expect(legacy.recommendationPortfolioV2 == nil, "missing V2 portfolio remains backward compatible")
        expect(legacy.referenceSnapshotFeed == nil, "missing reference feed remains backward compatible")
        expect(legacy.recommendationUse == nil, "missing recommendation use remains backward compatible")
    } catch {
        failureCount += 1
        fputs("FAIL: decode error \(error)\n", stderr)
    }
}

@main
private enum AdvisorDecodingTestMain {
    static func main() {
        verifyAdvisorDecoding()
        if failureCount > 0 {
            exit(1)
        }
        print("Advisor decoding tests passed")
    }
}
