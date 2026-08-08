from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from devtools.pricing.updater import build_update, execute_update


FETCHED_AT = "2026-07-25T08:00:00Z"
SOURCE_REVISION = "a" * 40
SOURCE_SHA256 = "sha256:" + "b" * 64


def _previous_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": "pricing-v1-previous",
        "generated_at": "2026-07-24T00:00:00Z",
        "upstreams": [{"name": "ModelDial curated fallback"}],
        "models": {
            "exact-model": {
                "input_per_token": 1e-6,
                "cached_input_per_token": 1e-7,
                "output_per_token": 5e-6,
            },
            "alias-model": {
                "input_per_token": 2e-6,
                "output_per_token": 8e-6,
            },
            "missing-model": {
                "input_per_token": 3e-6,
                "output_per_token": 9e-6,
            },
        },
        "aliases": {"latest-model": "exact-model"},
    }


def _upstream_payload() -> dict[str, object]:
    return {
        "exact-model": {
            "litellm_provider": "provider-a",
            "mode": "chat",
            "input_cost_per_token": 2e-6,
            "cache_read_input_token_cost": 2e-7,
            "cache_creation_input_token_cost": 2.5e-6,
            "output_cost_per_token": 10e-6,
            "input_cost_per_token_above_200k_tokens": 4e-6,
            "output_cost_per_token_above_200k_tokens": 15e-6,
        },
        "provider/alias-v2": {
            "litellm_provider": "provider-b",
            "mode": "chat",
            "input_cost_per_token": 4e-6,
            "output_cost_per_token": 12e-6,
        },
    }


def _policy(**overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "schema_version": 1,
        "source_name": "LiteLLM",
        "source_url": f"https://example.test/{SOURCE_REVISION}/prices.json",
        "source_revision": SOURCE_REVISION,
        "source_sha256": SOURCE_SHA256,
        "minimum_upstream_entry_count": 2,
        "minimum_model_count": 3,
        "minimum_fresh_model_count": 2,
        "maximum_cost_per_token": 0.01,
        "required_priceable_prefixes": ["exact-", "alias-", "missing-"],
        "reviewed_matches": {"alias-model": "provider/alias-v2"},
    }
    policy.update(overrides)
    return policy


class PricingUpdaterTest(unittest.TestCase):
    def test_policy_requires_commit_pinned_source_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_revision"):
            build_update(
                _previous_snapshot(),
                _upstream_payload(),
                _policy(source_revision=None),
                fetched_at=FETCHED_AT,
            )
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            build_update(
                _previous_snapshot(),
                _upstream_payload(),
                _policy(source_sha256="sha256:not-a-digest"),
                fetched_at=FETCHED_AT,
            )
        with self.assertRaisesRegex(ValueError, "must contain source_revision"):
            build_update(
                _previous_snapshot(),
                _upstream_payload(),
                _policy(source_url="https://example.test/main/prices.json"),
                fetched_at=FETCHED_AT,
            )

    def test_checked_in_snapshot_matches_pinned_source_identity(self) -> None:
        root = Path(__file__).resolve().parent.parent
        policy = json.loads(
            (root / "devtools" / "pricing" / "policy.json").read_text(
                encoding="utf-8"
            )
        )
        snapshot = json.loads(
            (root / "scanner" / "pricing_snapshot.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            snapshot["upstreams"][0],
            {
                "name": policy["source_name"],
                "url": policy["source_url"],
                "revision": policy["source_revision"],
                "sha256": policy["source_sha256"],
            },
        )

    def test_builds_candidate_from_exact_and_reviewed_matches_only(self) -> None:
        outcome = build_update(
            _previous_snapshot(),
            _upstream_payload(),
            _policy(),
            fetched_at=FETCHED_AT,
            requested_models=("unknown-model",),
        )

        self.assertEqual(outcome.report["status"], "candidate_ready")
        self.assertEqual(outcome.report["fresh_model_count"], 2)
        self.assertEqual(outcome.report["stale_model_count"], 1)
        self.assertEqual(outcome.report["unpriced_requested_models"], ["unknown-model"])
        self.assertIsNotNone(outcome.candidate)
        candidate = outcome.candidate or {}
        models = candidate["models"]

        exact = models["exact-model"]
        self.assertEqual(exact["input_per_token"], 2e-6)
        self.assertEqual(exact["cached_input_per_token"], 2e-7)
        self.assertEqual(exact["cache_write_input_per_token"], 2.5e-6)
        self.assertEqual(
            exact["long_context"],
            {
                "threshold_tokens": 200_000,
                "input_multiplier": 2.0,
                "output_multiplier": 1.5,
            },
        )
        self.assertEqual(
            exact["provenance"],
            {
                "source": "LiteLLM",
                "matched_key": "exact-model",
                "fetched_at": FETCHED_AT,
                "stale": False,
                "confidence": "exact",
                "reason": "exact_model_id",
            },
        )

        reviewed = models["alias-model"]["provenance"]
        self.assertEqual(reviewed["matched_key"], "provider/alias-v2")
        self.assertEqual(reviewed["confidence"], "reviewed")
        self.assertEqual(reviewed["reason"], "reviewed_alias")

        stale = models["missing-model"]
        self.assertEqual(stale["input_per_token"], 3e-6)
        self.assertTrue(stale["provenance"]["stale"])
        self.assertEqual(stale["provenance"]["reason"], "upstream_missing_preserved")
        self.assertNotIn("unknown-model", models)
        self.assertEqual(candidate["aliases"], {"latest-model": "exact-model"})
        self.assertEqual(
            candidate["upstreams"][0],
            {
                "name": "LiteLLM",
                "url": f"https://example.test/{SOURCE_REVISION}/prices.json",
                "revision": SOURCE_REVISION,
                "sha256": SOURCE_SHA256,
            },
        )
        self.assertTrue(candidate["snapshot_id"].startswith("pricing-v1-"))
        self.assertEqual(candidate["content_hash"], candidate["snapshot_id"].removeprefix("pricing-v1-"))

    def test_does_not_fuzzy_match_an_unreviewed_provider_key(self) -> None:
        previous = _previous_snapshot()
        previous["models"] = {
            "target-model": {
                "input_per_token": 1e-6,
                "output_per_token": 2e-6,
            }
        }
        previous["aliases"] = {}
        upstream = {
            "provider/target-model": {
                "litellm_provider": "provider",
                "mode": "chat",
                "input_cost_per_token": 9e-6,
                "output_cost_per_token": 9e-6,
            },
            "provider/brand-new": {
                "litellm_provider": "provider",
                "mode": "chat",
                "input_cost_per_token": 9e-6,
                "output_cost_per_token": 9e-6,
            },
        }
        policy = _policy(
            minimum_model_count=1,
            minimum_fresh_model_count=0,
            required_priceable_prefixes=["target-"],
            reviewed_matches={},
        )

        outcome = build_update(
            previous,
            upstream,
            policy,
            fetched_at=FETCHED_AT,
            requested_models=("brand-new",),
        )

        candidate = outcome.candidate or {}
        target = candidate["models"]["target-model"]
        self.assertEqual(target["input_per_token"], 1e-6)
        self.assertTrue(target["provenance"]["stale"])
        self.assertEqual(outcome.report["unpriced_requested_models"], ["brand-new"])
        self.assertNotIn("brand-new", candidate["models"])

    def test_fetched_time_does_not_create_a_new_version_when_rates_are_unchanged(self) -> None:
        first = build_update(
            _previous_snapshot(),
            _upstream_payload(),
            _policy(),
            fetched_at=FETCHED_AT,
        )
        first_candidate = first.candidate
        self.assertIsNotNone(first_candidate)

        second = build_update(
            first_candidate or {},
            _upstream_payload(),
            _policy(),
            fetched_at="2026-07-26T08:00:00Z",
        )

        self.assertEqual(second.report["status"], "unchanged")
        self.assertFalse(second.report["changed"])
        self.assertIsNone(second.candidate)
        self.assertEqual(second.report["snapshot_id"], first_candidate["snapshot_id"])

    def test_source_revision_changes_snapshot_identity_when_rates_are_unchanged(self) -> None:
        first = build_update(
            _previous_snapshot(),
            _upstream_payload(),
            _policy(),
            fetched_at=FETCHED_AT,
        )
        first_candidate = first.candidate
        self.assertIsNotNone(first_candidate)
        next_revision = "c" * 40

        second = build_update(
            first_candidate or {},
            _upstream_payload(),
            _policy(
                source_revision=next_revision,
                source_url=f"https://example.test/{next_revision}/prices.json",
                source_sha256="sha256:" + "d" * 64,
            ),
            fetched_at="2026-07-26T08:00:00Z",
        )

        self.assertEqual(second.report["status"], "candidate_ready")
        self.assertNotEqual(
            second.report["snapshot_id"],
            first_candidate["snapshot_id"],
        )

    def test_failed_validation_keeps_previous_snapshot_and_records_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_path = root / "pricing_snapshot.json"
            candidate_path = root / "candidate.json"
            report_path = root / "report.json"
            original = json.dumps(_previous_snapshot(), indent=2, sort_keys=True) + "\n"
            snapshot_path.write_text(original, encoding="utf-8")

            report = execute_update(
                snapshot_path=snapshot_path,
                upstream_payload=_upstream_payload(),
                policy=_policy(minimum_upstream_entry_count=100),
                fetched_at=FETCHED_AT,
                candidate_path=candidate_path,
                report_path=report_path,
                apply=True,
            )

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["applied"])
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), original)
            self.assertFalse(candidate_path.exists())
            self.assertTrue(report_path.is_file())
            self.assertIn("minimum", " ".join(report["errors"]))

    def test_apply_is_explicit_and_replaces_snapshot_only_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_path = root / "pricing_snapshot.json"
            candidate_path = root / "candidate.json"
            report_path = root / "report.json"
            snapshot_path.write_text(
                json.dumps(_previous_snapshot(), indent=2) + "\n",
                encoding="utf-8",
            )

            dry_run = execute_update(
                snapshot_path=snapshot_path,
                upstream_payload=_upstream_payload(),
                policy=_policy(),
                fetched_at=FETCHED_AT,
                candidate_path=candidate_path,
                report_path=report_path,
                apply=False,
            )
            self.assertEqual(dry_run["status"], "candidate_ready")
            self.assertEqual(
                json.loads(snapshot_path.read_text(encoding="utf-8"))["snapshot_id"],
                "pricing-v1-previous",
            )
            self.assertTrue(candidate_path.is_file())

            applied = execute_update(
                snapshot_path=snapshot_path,
                upstream_payload=_upstream_payload(),
                policy=_policy(),
                fetched_at=FETCHED_AT,
                candidate_path=candidate_path,
                report_path=report_path,
                apply=True,
            )
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["applied"])
            installed = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(installed["snapshot_id"], applied["snapshot_id"])

    def test_app_bundle_copies_only_the_validated_runtime_updater(self) -> None:
        root = Path(__file__).resolve().parent.parent
        build_source = (root / "build.sh").read_text(encoding="utf-8")

        self.assertNotIn('cp -R "devtools"', build_source)
        self.assertIn(
            'cp "devtools/pricing/updater.py" "$BACKEND_DIR/devtools/pricing/updater.py"',
            build_source,
        )
        self.assertIn(
            'cp "devtools/pricing/policy.json" "$BACKEND_DIR/devtools/pricing/policy.json"',
            build_source,
        )
        self.assertIn(
            '--hidden-import "devtools.pricing.updater"',
            build_source,
        )
        self.assertTrue((root / "scanner" / "local_pricing.py").exists())
        self.assertFalse((root / "scripts" / "update_pricing_snapshot.py").exists())

    def test_cli_can_run_directly_with_an_offline_source(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            snapshot_path = temp / "snapshot.json"
            source_path = temp / "source.json"
            policy_path = temp / "policy.json"
            candidate_path = temp / "candidate.json"
            report_path = temp / "report.json"
            snapshot_path.write_text(json.dumps(_previous_snapshot()), encoding="utf-8")
            source_document = json.dumps(_upstream_payload()).encode("utf-8")
            source_path.write_bytes(source_document)
            policy_path.write_text(
                json.dumps(
                    _policy(
                        source_sha256=(
                            "sha256:" + hashlib.sha256(source_document).hexdigest()
                        )
                    )
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "devtools" / "update_pricing_snapshot.py"),
                    "--snapshot",
                    str(snapshot_path),
                    "--source-file",
                    str(source_path),
                    "--policy",
                    str(policy_path),
                    "--candidate",
                    str(candidate_path),
                    "--report",
                    str(report_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["status"],
                "candidate_ready",
            )

    def test_cli_records_source_failure_without_replacing_snapshot(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            snapshot_path = temp / "snapshot.json"
            source_path = temp / "broken-source.json"
            policy_path = temp / "policy.json"
            report_path = temp / "report.json"
            original = json.dumps(_previous_snapshot(), indent=2) + "\n"
            snapshot_path.write_text(original, encoding="utf-8")
            source_document = b"{"
            source_path.write_bytes(source_document)
            policy_path.write_text(
                json.dumps(
                    _policy(
                        source_sha256=(
                            "sha256:" + hashlib.sha256(source_document).hexdigest()
                        )
                    )
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "devtools" / "update_pricing_snapshot.py"),
                    "--snapshot",
                    str(snapshot_path),
                    "--source-file",
                    str(source_path),
                    "--policy",
                    str(policy_path),
                    "--report",
                    str(report_path),
                    "--apply",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["applied"])
            self.assertTrue(report["errors"])
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), original)

    def test_cli_rejects_source_file_hash_mismatch(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            snapshot_path = temp / "snapshot.json"
            source_path = temp / "source.json"
            policy_path = temp / "policy.json"
            report_path = temp / "report.json"
            original = json.dumps(_previous_snapshot(), indent=2) + "\n"
            snapshot_path.write_text(original, encoding="utf-8")
            source_path.write_text(json.dumps(_upstream_payload()), encoding="utf-8")
            policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "devtools" / "update_pricing_snapshot.py"),
                    "--snapshot",
                    str(snapshot_path),
                    "--source-file",
                    str(source_path),
                    "--policy",
                    str(policy_path),
                    "--report",
                    str(report_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertIn("source hash mismatch", " ".join(report["errors"]))
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
