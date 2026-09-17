from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

from scanner import reference_snapshot as reference
from tests.test_reference_snapshot import _first_party_snapshot_fixture, _serve_directory


class IncrementalReferenceSnapshotTests(unittest.TestCase):
    def fixture(self, *, legacy: bool = False):
        source = _first_party_snapshot_fixture()
        questions = reference.load_reference_snapshot_feed()["latest"]["leaderboard_projection"]["questions"]
        source["leaderboard_projection"] = reference.build_reference_snapshot_leaderboard_projection(
            source, question_semantics=questions
        )
        source["batch_sha256"] = reference.reference_snapshot_hash(source)
        current = deepcopy(source)
        current["batch_id"] = "incremental-release"
        current["published_at"] = "2026-09-17T00:00:00Z"
        if not legacy:
            current["grader_version"] = "grading-new"
            current["score_baseline_id"] = "baseline-new"
        retained_id = current["entries"][0]["model_configuration_id"]
        current["provenance"]["incremental_sources"] = [{
            "path": f"public/reference-snapshots/archive/{source['batch_id']}.json",
            "batch_sha256": source["batch_sha256"],
            "candidate_ids": [retained_id],
        }]
        if not legacy:
            current["provenance"]["entry_sources"] = {
                entry["model_configuration_id"]: {
                    key: origin[key]
                    for key in ("batch_id", "question_pack_version", "grader_version", "score_baseline_id", "question_ids")
                }
                for entry in current["entries"]
                for origin in [source if entry["model_configuration_id"] == retained_id else current]
            }
        current["leaderboard_projection"] = reference.build_reference_snapshot_leaderboard_projection(
            current, question_semantics=questions
        )
        old_row = next(row for row in source["leaderboard_projection"]["rows"] if row["model_configuration_id"] == retained_id)
        self.row(current, retained_id)["trend"] = deepcopy(old_row["trend"])
        self.seal(current)
        return source, current, retained_id

    @staticmethod
    def row(snapshot, configuration_id):
        return next(row for row in snapshot["leaderboard_projection"]["rows"] if row["model_configuration_id"] == configuration_id)

    @staticmethod
    def seal(snapshot):
        snapshot["batch_sha256"] = reference.reference_snapshot_hash(snapshot)

    def test_accepts_both_publisher_formats_without_mutating_evidence(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                _, snapshot, retained_id = self.fixture(legacy=legacy)
                original = deepcopy(snapshot)
                self.assertEqual(reference.validate_reference_snapshot(snapshot), original)
                self.assertEqual(snapshot, original)
                self.assertNotEqual(self.row(snapshot, retained_id)["trend"]["points"][-1]["batch_id"], snapshot["batch_id"])

    def test_inherited_trend_can_precede_immediate_source_release(self):
        _, snapshot, retained_id = self.fixture()
        snapshot["provenance"]["entry_sources"][retained_id]["batch_id"] = "intermediate-release"
        self.seal(snapshot)
        reference.validate_reference_snapshot(snapshot)

    def test_rejects_old_trend_without_declared_inheritance(self):
        _, snapshot, _ = self.fixture()
        snapshot["provenance"].pop("incremental_sources")
        self.seal(snapshot)
        with self.assertRaisesRegex(ValueError, "does not end at current row"):
            reference.validate_reference_snapshot(snapshot)

    def test_rejects_wrong_candidate_in_inheritance_manifest(self):
        _, snapshot, _ = self.fixture()
        snapshot["provenance"]["incremental_sources"][0]["candidate_ids"] = ["unrelated"]
        self.seal(snapshot)
        with self.assertRaisesRegex(ValueError, "does not end at current row"):
            reference.validate_reference_snapshot(snapshot)

    def test_rejects_future_inherited_observation(self):
        _, snapshot, retained_id = self.fixture()
        self.row(snapshot, retained_id)["trend"]["points"][-1]["published_at"] = "2027-01-01T00:00:00Z"
        self.seal(snapshot)
        with self.assertRaisesRegex(ValueError, "does not end at current row"):
            reference.validate_reference_snapshot(snapshot)

    def test_rejects_changed_metrics_and_route_even_for_inherited_rows(self):
        for mutation in ("score", "elapsed_ms", "route"):
            with self.subTest(mutation=mutation):
                _, snapshot, retained_id = self.fixture()
                if mutation == "route":
                    snapshot["entries"][0]["route_fingerprint"] = "sha256:" + "e" * 64
                else:
                    self.row(snapshot, retained_id)["trend"]["points"][-1][mutation] += 1
                self.seal(snapshot)
                with self.assertRaisesRegex(ValueError, "trend"):
                    reference.validate_reference_snapshot(snapshot)

    def test_rejects_bad_or_missing_source_identity(self):
        for mutation in ("missing", "baseline", "questions", "current"):
            with self.subTest(mutation=mutation):
                _, snapshot, retained_id = self.fixture()
                source = snapshot["provenance"]["entry_sources"][retained_id]
                if mutation == "missing":
                    del snapshot["provenance"]["entry_sources"][retained_id]
                elif mutation == "baseline":
                    source["score_baseline_id"] = "wrong-baseline"
                elif mutation == "questions":
                    source["question_ids"] = ["unrelated"]
                else:
                    source["batch_id"] = snapshot["batch_id"]
                self.seal(snapshot)
                with self.assertRaises(ValueError):
                    reference.validate_reference_snapshot(snapshot)

    def test_rejects_bad_source_hash_and_unsafe_path(self):
        for key, value in (("batch_sha256", "bad"), ("path", "public/reference-snapshots/archive/../other.json")):
            with self.subTest(key=key):
                _, snapshot, _ = self.fixture()
                snapshot["provenance"]["incremental_sources"][0][key] = value
                self.seal(snapshot)
                with self.assertRaisesRegex(ValueError, "incremental source"):
                    reference.validate_reference_snapshot(snapshot)

    def test_hash_and_canonical_ranking_still_reject_tampering(self):
        for mutation in ("hash", "rank"):
            with self.subTest(mutation=mutation):
                _, snapshot, _ = self.fixture()
                if mutation == "hash":
                    snapshot["batch_sha256"] = "sha256:" + "0" * 64
                else:
                    snapshot["leaderboard_projection"]["rows"][0]["rank"] = 99
                    self.seal(snapshot)
                with self.assertRaises(ValueError):
                    reference.validate_reference_snapshot(snapshot)

    def test_advisor_keeps_actual_entry_identity(self):
        source, snapshot, retained_id = self.fixture()
        official = reference.reference_snapshot_to_advisor_source(snapshot)
        row = next(row for row in official["rows"] if row["model_configuration_id"] == retained_id)
        self.assertEqual(row["grader_version"], source["grader_version"])
        self.assertEqual(row["score_baseline_id"], source["score_baseline_id"])
        self.assertEqual(official["score_baseline_id"], snapshot["score_baseline_id"])

    def test_advisor_rejects_same_pack_and_grader_with_different_baseline(self):
        from scanner.advisor_v2 import _result_reasons

        _, snapshot, retained_id = self.fixture()
        official = reference.reference_snapshot_to_advisor_source(snapshot)
        row = next(row for row in official["rows"] if row["model_configuration_id"] == retained_id)
        row["question_pack_version"] = official["question_pack_version"]
        row["grader_version"] = official["grader_version"]
        reasons = _result_reasons(
            configuration={}, row=row, source=official,
            now=datetime(2026, 9, 17, tzinfo=timezone.utc),
        )
        self.assertIn("score_baseline_mismatch", reasons)

    def test_pairwise_does_not_claim_cross_baseline_improvements(self):
        _, snapshot, retained_id = self.fixture()
        pairs = reference.build_reference_snapshot_pairwise_comparisons(snapshot)
        cross_identity = [pair for pair in pairs if retained_id in (pair["baseline_candidate_id"], pair["candidate_id"])]
        self.assertTrue(cross_identity)
        for pair in cross_identity:
            self.assertFalse(pair["is_comparable"])
            self.assertEqual(pair["comparison_status"], "scoring_identity_mismatch")
            self.assertIsNone(pair["quality_delta_points"])
        self.assertTrue(any(pair["is_comparable"] for pair in pairs))

    def test_swift_decodes_old_and_new_entry_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "decode"
            subprocess.run([
                "swiftc", "-parse-as-library", "-o", str(executable),
                "Sources/Model/LocalEncryptedSecretStore.swift",
                "Sources/Model/SelectionModels.swift",
                "tests/swift/IncrementalReferenceDecodingTests.swift",
            ], check=True, capture_output=True, text=True)
            for legacy in (False, True):
                _, snapshot, _ = self.fixture(legacy=legacy)
                payload = root / "snapshot.json"
                payload.write_text(json.dumps(snapshot))
                subprocess.run([str(executable), str(payload), "legacy" if legacy else "current"], check=True)

    def test_http_refresh_cache_and_not_modified_keep_all_entries(self):
        _, snapshot, retained_id = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, cache = root / "remote", root / "cache"
            (remote / "archive").mkdir(parents=True)
            summary = {key: snapshot[key] for key in ("batch_id", "published_at", "question_pack_version", "score_baseline_id", "entry_count", "batch_sha256")}
            summary["path"] = "archive/current.json"
            index = {"schema_version": 1, "kind": snapshot["kind"], "generated_at": snapshot["published_at"], "latest_batch_id": snapshot["batch_id"], "latest_path": summary["path"], "snapshots": [summary]}
            (remote / summary["path"]).write_text(json.dumps(snapshot))
            (remote / "index.json").write_text(json.dumps(index))
            with _serve_directory(remote, [], index_request_headers=[]) as url:
                fresh = reference.load_reference_snapshot_feed_for_app(cache_root=cache, base_url=url)
                self.assertEqual(fresh["delivery"]["refresh_status"], "refreshed")
                again = reference.load_reference_snapshot_feed_for_app(cache_root=cache, base_url=url)
                self.assertEqual(again["delivery"]["refresh_status"], "not_modified")
            cached = reference.load_reference_snapshot_feed(cache)
            self.assertEqual(cached["latest"], snapshot)
            self.assertEqual(again["latest"]["entry_count"], len(snapshot["entries"]))
            self.assertEqual(self.row(cached["latest"], retained_id), self.row(snapshot, retained_id))


if __name__ == "__main__":
    unittest.main()
