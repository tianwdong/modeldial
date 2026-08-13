from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import scanner.reference_snapshot as reference_snapshot_module
from devtools.reference_snapshots.build_development_seed import (
    build_seed_snapshots,
    _seed_reference_cost_summary,
    _seed_reference_usage,
)
from scanner.active_run_store import ActiveRunStore
from scanner.advisor_v2 import build_advisor_evidence_context
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.native_bridge import build_snapshot, refresh_reference_snapshots
from scanner.reference_snapshot import (
    DEFAULT_REFERENCE_SNAPSHOT_URL,
    DEFAULT_REFERENCE_SNAPSHOT_TIMEOUT_SECONDS,
    MAX_REFERENCE_ELAPSED_MS,
    _HttpJsonResponse,
    ReferenceSnapshotDownloadError,
    build_reference_snapshot_leaderboard_projection,
    build_reference_snapshot_pairwise_comparisons,
    load_reference_snapshot_feed,
    load_reference_snapshot_feed_for_app,
    read_reference_snapshot_feed_for_app,
    reference_snapshot_hash,
    reference_snapshot_to_advisor_source,
    validate_reference_snapshot,
)


NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
CURRENT_ID = "codex-local-default:gpt-5.6-sol:high"
CANDIDATE_ID = "codex-local-default:gpt-5.6-sol:xhigh"
SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "scanner" / "reference_snapshots"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serve_directory(
    root: Path,
    request_log: list[str] | None = None,
    *,
    index_request_headers: list[str | None] | None = None,
    index_request_cache_controls: list[str | None] | None = None,
):
    class Handler(_QuietHandler):
        def do_GET(self) -> None:
            if request_log is not None:
                request_log.append(self.path)
            if self.path == "/index.json" and index_request_headers is not None:
                index_request_headers.append(self.headers.get("If-None-Match"))
                if index_request_cache_controls is not None:
                    index_request_cache_controls.append(
                        self.headers.get("Cache-Control")
                    )
                body = (root / "index.json").read_bytes()
                etag = f'"{hashlib.sha256(body).hexdigest()}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    handler = partial(Handler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _first_party_snapshot_fixture() -> dict[str, object]:
    snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
    snapshot["kind"] = "first_party_snapshot"
    snapshot["runner_commit"] = "a" * 40
    snapshot["environment"] = {
        "os": "macOS 15.5",
        "app_version": "0.1.0",
        "codex_version": "0.1.0",
        "machine_profile": "test-machine",
    }
    snapshot["retry_policy"] = {
        "schema_version": 1,
        "mode": "app_rules_v1",
        "selective_score_retry": False,
        "rules": {},
    }
    snapshot["grader_replay"] = {
        "status": "matched",
        "method": "independent_regrade",
        "regraded_at": "2026-07-24T14:00:00Z",
        "raw_answer_bundle_sha256": "sha256:" + "b" * 64,
        "manifest_sha256": "sha256:" + "c" * 64,
    }
    snapshot["planned_configuration_ids"] = [
        entry["model_configuration_id"] for entry in snapshot["entries"]
    ]
    snapshot["question_ids"] = [
        question["id"] for question in snapshot["leaderboard_projection"]["questions"]
    ]
    for entry in snapshot["entries"]:
        entry["score_integrity"] = "first_party_controlled"
        entry["route_identity"] = "first_party_controlled"
        entry["run_manifest_sha256"] = "sha256:" + "d" * 64
    snapshot["provenance"] = {
        "kind": "first_party_snapshot",
        "source": "owner_controlled_run",
        "target_run_id": "run-test",
        "comparison_group_id": "run-test",
        "public_official_snapshot": True,
    }
    # The development projection's compatibility hash includes `kind`; the
    # official fixture only needs to exercise advisor-source provenance.
    snapshot.pop("leaderboard_projection", None)
    snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)
    return snapshot


class ReferenceSnapshotTests(unittest.TestCase):
    def test_advisor_source_rejects_development_seed(self) -> None:
        snapshot = deepcopy(load_reference_snapshot_feed()["latest"])

        source = reference_snapshot_to_advisor_source(snapshot)

        self.assertIsNone(source)

    def test_advisor_source_preserves_canonical_identity_for_first_party_snapshot(self) -> None:
        snapshot = _first_party_snapshot_fixture()

        source = reference_snapshot_to_advisor_source(snapshot)

        self.assertIsNotNone(source)
        assert source is not None
        source_rows = {
            row["source_model_configuration_id"]: row
            for row in source["rows"]
        }
        first_entry = next(
            entry for entry in snapshot["entries"] if entry["advisor_eligible"]
        )
        row = source_rows[first_entry["model_configuration_id"]]
        self.assertEqual(
            row["provider_id"],
            first_entry["model_configuration"]["provider_id"],
        )
        self.assertEqual(
            row["canonical_model_id"],
            first_entry["model_configuration"]["canonical_model_id"],
        )
        self.assertEqual(
            row["reasoning_effort"],
            first_entry["model_configuration"]["reasoning_effort"],
        )
        self.assertEqual(row["route_identity"], first_entry["route_identity"])

    def test_default_remote_feed_is_disabled_in_the_open_source_app(self) -> None:
        self.assertEqual(DEFAULT_REFERENCE_SNAPSHOT_URL, "")

    def test_remote_http_feed_requires_https(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feed = load_reference_snapshot_feed_for_app(
                cache_root=root / "empty-cache",
                base_url="http://reference.example.test/reference-snapshots",
            )
            read_feed = read_reference_snapshot_feed_for_app(
                cache_root=root / "empty-cache",
                base_url="http://reference.example.test/reference-snapshots",
            )
            cached_root = root / "cached"
            shutil.copytree(SNAPSHOT_ROOT, cached_root)
            cached_read_feed = read_reference_snapshot_feed_for_app(
                cache_root=cached_root,
                base_url="http://reference.example.test/reference-snapshots",
            )

        self.assertEqual(feed["delivery"]["source"], "bundled")
        self.assertEqual(feed["delivery"]["refresh_status"], "failed")
        self.assertEqual(feed["delivery"]["error_code"], "https_required")
        self.assertEqual(read_feed["delivery"]["source"], "bundled")
        self.assertEqual(read_feed["delivery"]["refresh_status"], "failed")
        self.assertEqual(read_feed["delivery"]["error_code"], "https_required")
        self.assertEqual(cached_read_feed["delivery"]["source"], "bundled")
        self.assertEqual(cached_read_feed["delivery"]["refresh_status"], "failed")
        self.assertEqual(cached_read_feed["delivery"]["error_code"], "https_required")

    def test_seed_backfills_missing_cost_and_ignores_failed_calls(self) -> None:
        completed = SimpleNamespace(
            model="grok-4.5",
            input_tokens=1_000,
            cached_input_tokens=200,
            cache_write_input_tokens=None,
            output_tokens=500,
            reasoning_tokens=100,
            reference_cost_usd=None,
            execution_trace={"terminal_state": "completed_response"},
            error_message=None,
        )
        failed = SimpleNamespace(
            model="grok-4.5",
            input_tokens=None,
            cached_input_tokens=None,
            cache_write_input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            reference_cost_usd=None,
            execution_trace={"terminal_state": "timeout"},
            error_message="endpoint request failed: timeout",
        )

        cost, coverage = _seed_reference_cost_summary([completed, failed])

        self.assertAlmostEqual(cost, 0.0047)
        self.assertEqual(coverage, "complete")

    def test_seed_usage_only_sums_completed_model_calls(self) -> None:
        completed = SimpleNamespace(
            input_tokens=1_000,
            cached_input_tokens=200,
            cache_write_input_tokens=50,
            output_tokens=500,
            reasoning_tokens=100,
            execution_trace={"terminal_state": "completed_response"},
            error_message=None,
        )
        failed = SimpleNamespace(
            input_tokens=9_000,
            cached_input_tokens=8_000,
            cache_write_input_tokens=7_000,
            output_tokens=6_000,
            reasoning_tokens=5_000,
            execution_trace={"terminal_state": "timeout"},
            error_message="endpoint request failed: timeout",
        )

        self.assertEqual(
            _seed_reference_usage([completed, failed]),
            {
                "input_tokens": 1_000,
                "cached_input_tokens": 200,
                "cache_write_input_tokens": 50,
                "output_tokens": 500,
                "reasoning_tokens": 100,
            },
        )

    def test_bundled_development_feed_preserves_four_exported_snapshots(self) -> None:
        feed = load_reference_snapshot_feed()

        self.assertEqual(feed["status"], "loaded")
        self.assertEqual(feed["kind"], "development_seed")
        self.assertEqual(
            [snapshot["entry_count"] for snapshot in feed["snapshots"]],
            [12, 13, 15, 18],
        )
        self.assertEqual(
            [snapshot["question_pack_version"] for snapshot in feed["snapshots"]],
            ["coding-fast-v4.10"] * 4,
        )
        self.assertEqual(
            {snapshot["score_baseline_id"] for snapshot in feed["snapshots"]},
            {"coding-fast-v4.10:synthetic-v1"},
        )
        self.assertEqual(feed["latest"]["entry_count"], 18)

    def test_development_seed_is_deterministic_and_anonymous(self) -> None:
        snapshots = build_seed_snapshots()

        self.assertEqual(
            [snapshot["batch_id"] for snapshot in snapshots],
            [
                "synthetic-reference-seed-v1-01",
                "synthetic-reference-seed-v1-02",
                "synthetic-reference-seed-v1-03",
                "synthetic-reference-seed-v1-04",
            ],
        )
        self.assertEqual(
            {snapshot["provenance"]["source"] for snapshot in snapshots},
            {"synthetic_fixture"},
        )
        self.assertEqual(
            {snapshot["question_pack_version"] for snapshot in snapshots},
            {"coding-fast-v4.10"},
        )
        for snapshot in snapshots:
            self.assertEqual(
                snapshot["retry_policy"]["mode"],
                "synthetic_app_policy_v1",
            )
            self.assertEqual(snapshot["pricing_snapshot_id"], "synthetic-pricing-v1")
            for entry in snapshot["entries"]:
                self.assertEqual(entry["score_integrity"], "synthetic_fixture")
                self.assertEqual(entry["route_identity"], "synthetic_fixture")
                self.assertIsNone(entry["source_evidence_group_id"])
        self.assertEqual(snapshots, build_seed_snapshots())
        self.assertEqual(snapshots[0]["entries"][0]["score"], 92)
        self.assertEqual(snapshots[0]["entries"][0]["elapsed_ms"], 235_000)
        self.assertEqual(
            snapshots[0]["entries"][0]["usage"],
            {
                "input_tokens": 18_200,
                "cached_input_tokens": 3_640,
                "cache_write_input_tokens": 0,
                "output_tokens": 4_100,
                "reasoning_tokens": 2_075,
            },
        )
        for index, snapshot in enumerate(snapshots, start=1):
            archive = json.loads(
                (
                    SNAPSHOT_ROOT
                    / "archive"
                    / f"synthetic-reference-seed-v1-{index:02d}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot, archive)

    def test_leaderboard_projection_uses_the_app_decision_tag_rules(self) -> None:
        snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
        entries = snapshot["entries"][:4]
        cases = (
            (90, 20_000, 1.2),
            (84, 30_000, 0.4),
            (80, 10_000, 1.1),
            (65, 9_000, 0.3),
        )
        for entry, (score, elapsed_ms, cost) in zip(entries, cases):
            entry["score"] = score
            entry["elapsed_ms"] = elapsed_ms
            entry["estimated_api_cost_usd"] = cost
            entry["cost_coverage"] = "complete"
            entry["hard_failure_count"] = 0
        snapshot["entries"] = entries

        projection = build_reference_snapshot_leaderboard_projection(
            snapshot,
            question_semantics=snapshot["leaderboard_projection"]["questions"],
        )

        kinds_by_id = {
            row["model_configuration_id"]: [
                tag["kind"] for tag in row["decision_tags"]
            ]
            for row in projection["rows"]
        }
        self.assertEqual(kinds_by_id[entries[0]["model_configuration_id"]], ["recommended"])
        self.assertEqual(kinds_by_id[entries[1]["model_configuration_id"]], ["value"])
        self.assertEqual(kinds_by_id[entries[2]["model_configuration_id"]], ["speed"])
        self.assertEqual(kinds_by_id[entries[3]["model_configuration_id"]], ["lightweight"])

    def test_reference_snapshot_pairwise_covers_every_ordered_candidate_pair(self) -> None:
        snapshot = deepcopy(load_reference_snapshot_feed()["latest"])

        pairs = build_reference_snapshot_pairwise_comparisons(snapshot)

        self.assertEqual(
            len(pairs),
            snapshot["entry_count"] * (snapshot["entry_count"] - 1),
        )
        pair = next(
            item
            for item in pairs
            if item["baseline_candidate_id"].endswith(":ultra")
            and item["candidate_id"].endswith(":xhigh")
        )
        self.assertEqual(pair["comparison_status"], "comparable")
        self.assertTrue(pair["is_comparable"])
        self.assertEqual(pair["quality_delta_points"], -2)
        self.assertEqual(pair["baseline_elapsed_seconds"], 220)
        self.assertEqual(pair["candidate_elapsed_seconds"], 255)
        self.assertEqual(pair["cost_delta_percent"], -31.25)
        self.assertEqual(pair["warning_question_ids"], [])
        self.assertEqual(pair["baseline_token_totals"]["input_tokens"], 18_800)
        self.assertEqual(pair["candidate_token_totals"]["input_tokens"], 19_900)

    def test_snapshot_validator_rejects_noncanonical_decision_tags(self) -> None:
        snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
        snapshot["leaderboard_projection"]["rows"][0]["decision_tags"] = [
            {"kind": "speed"}
        ]
        snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

        with self.assertRaisesRegex(ValueError, "decision tags"):
            validate_reference_snapshot(snapshot)

    def test_snapshot_validator_accepts_grouped_execution_policy(self) -> None:
        snapshot = _first_party_snapshot_fixture()
        snapshot["retry_policy"].update(
            {
                "schema_version": 2,
                "max_concurrent_targets": 8,
                "max_concurrent_targets_by_connection": {
                    "first-party": 8,
                    "deepseek": 2,
                },
            }
        )
        snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

        validated = validate_reference_snapshot(snapshot)

        self.assertEqual(validated["retry_policy"]["schema_version"], 2)

    def test_snapshot_validator_rejects_malformed_grouped_execution_policy(
        self,
    ) -> None:
        cases = (
            {
                "schema_version": 2,
                "max_concurrent_targets_by_connection": {"deepseek": 2},
            },
            {
                "schema_version": 2,
                "max_concurrent_targets": 8,
                "max_concurrent_targets_by_connection": {},
            },
            {
                "schema_version": 2,
                "max_concurrent_targets": 8,
                "max_concurrent_targets_by_connection": {"deepseek": 0},
            },
            {
                "schema_version": 1,
                "max_concurrent_targets": 8,
                "max_concurrent_targets_by_connection": {"deepseek": 2},
            },
        )
        for policy_update in cases:
            with self.subTest(policy_update=policy_update):
                snapshot = _first_party_snapshot_fixture()
                snapshot["retry_policy"].update(policy_update)
                snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

                with self.assertRaisesRegex(ValueError, "retry policy"):
                    validate_reference_snapshot(snapshot)

    def test_snapshot_validator_rejects_entries_missing_consumer_contract_fields(self) -> None:
        cases = (
            ("advisor_eligible", None),
            ("cost_coverage", None),
            ("elapsed_ms", -1),
            ("attempt_count", 0),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
                entry = snapshot["entries"][0]
                if replacement is None:
                    entry.pop(field)
                else:
                    entry[field] = replacement
                snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

                with self.assertRaises(ValueError):
                    validate_reference_snapshot(snapshot)

    def test_snapshot_validator_rejects_elapsed_duration_beyond_swift_safe_range(self) -> None:
        for value, should_raise in (
            (MAX_REFERENCE_ELAPSED_MS, False),
            (MAX_REFERENCE_ELAPSED_MS * 2, True),
            (1e308, True),
        ):
            with self.subTest(value=value):
                snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
                snapshot["entries"][0]["elapsed_ms"] = value
                snapshot.pop("leaderboard_projection", None)
                snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

                if should_raise:
                    with self.assertRaisesRegex(ValueError, "numeric field is out of range"):
                        validate_reference_snapshot(snapshot)
                else:
                    validate_reference_snapshot(snapshot)

    def test_snapshot_validator_rejects_incomplete_model_and_usage_contracts(self) -> None:
        for field_path in (
            ("model_configuration", "provider_id"),
            ("usage", "input_tokens"),
            ("question_scores", ""),
        ):
            with self.subTest(field_path=field_path):
                snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
                parent, field = field_path
                entry = snapshot["entries"][0]
                if parent == "model_configuration":
                    entry[parent].pop(field)
                elif parent == "question_scores":
                    entry[parent][next(iter(entry[parent]))] = -1
                else:
                    entry[parent][field] = -1
                snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)

                with self.assertRaises(ValueError):
                    validate_reference_snapshot(snapshot)

    def test_latest_seed_does_not_become_actionable_official_evidence(self) -> None:
        feed = load_reference_snapshot_feed()
        source = reference_snapshot_to_advisor_source(feed["latest"])

        self.assertIsNone(source)
        context = build_advisor_evidence_context(
            source_mode="official_snapshot",
            current_configuration_id=CURRENT_ID,
            configurations=[
                {
                    "model_configuration_id": CURRENT_ID,
                    "enabled": True,
                    "identity_resolved": True,
                    "connection_ready": True,
                    "route_fingerprint": None,
                },
                {
                    "model_configuration_id": CANDIDATE_ID,
                    "enabled": True,
                    "identity_resolved": True,
                    "connection_ready": True,
                    "route_fingerprint": None,
                },
            ],
            local_evaluation=None,
            official_snapshot=source,
            now=NOW,
        )

        self.assertIsNone(context["resolved_data_source"])
        self.assertEqual(context["current_status"], "needs_test")
        self.assertEqual(context["eligible_candidate_ids"], [])

    def test_snapshot_hash_rejects_tampered_payload(self) -> None:
        snapshot = deepcopy(load_reference_snapshot_feed()["latest"])
        snapshot["generated_at"] = "2026-07-28T00:00:00Z"

        with self.assertRaisesRegex(ValueError, "batch hash mismatch"):
            validate_reference_snapshot(snapshot)

    def test_local_feed_rejects_non_object_and_duplicate_summaries(self) -> None:
        for case in ("non_object", "duplicate"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir) / "feed"
                shutil.copytree(SNAPSHOT_ROOT, root)
                index_path = root / "index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                if case == "non_object":
                    index["snapshots"].append("garbage")
                else:
                    index["snapshots"].append(deepcopy(index["snapshots"][0]))
                index_path.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                feed = load_reference_snapshot_feed(root)

                self.assertEqual(feed["status"], "invalid")
                self.assertEqual(feed["snapshots"], [])

    def test_local_feed_rejects_index_and_archive_kind_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "feed"
            shutil.copytree(SNAPSHOT_ROOT, root)
            index_path = root / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["kind"] = "first_party_snapshot"
            index_path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            feed = load_reference_snapshot_feed(root)

            self.assertEqual(feed["status"], "invalid")
            self.assertEqual(feed["snapshots"], [])

    def test_local_feed_rejects_a_stale_latest_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "feed"
            shutil.copytree(SNAPSHOT_ROOT, root)
            index = json.loads((root / "index.json").read_text(encoding="utf-8"))
            stale_archive = root / index["snapshots"][0]["path"]
            shutil.copyfile(stale_archive, root / "latest.json")

            feed = load_reference_snapshot_feed(root)

            self.assertEqual(feed["status"], "invalid")
            self.assertEqual(feed["snapshots"], [])

    def test_explicit_missing_root_is_reported_without_falling_back(self) -> None:
        missing_root = Path("/tmp/modeldial-reference-snapshot-does-not-exist")

        feed = load_reference_snapshot_feed(missing_root)

        self.assertEqual(feed["status"], "missing")
        self.assertIsNone(feed["latest"])
        self.assertEqual(feed["snapshots"], [])

    def test_http_feed_downloads_and_atomically_populates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            request_log: list[str] = []
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            index_path = remote_root / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["latest_path"] = "/latest.json"
            for summary in index["snapshots"]:
                summary["path"] = f"/{summary['path']}"
            index_path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with _serve_directory(remote_root, request_log) as base_url:
                feed = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )

            self.assertEqual(feed["status"], "loaded")
            self.assertEqual(feed["delivery"]["source"], "http")
            self.assertEqual(feed["delivery"]["refresh_status"], "refreshed")
            self.assertEqual(feed["latest"]["entry_count"], 18)
            cached = load_reference_snapshot_feed(cache_root)
            self.assertEqual(cached["status"], "loaded")
            bundle = json.loads(
                (cache_root / ".http-cache-bundle.json").read_text(encoding="utf-8")
            )
            cached_index = bundle["index"]
            self.assertEqual(cached_index["latest_path"], "latest.json")
            self.assertTrue(
                all(
                    summary["path"].startswith("archive/")
                    for summary in cached_index["snapshots"]
                )
            )
            self.assertEqual(
                cached["latest"]["batch_sha256"],
                feed["latest"]["batch_sha256"],
            )
            self.assertEqual(
                request_log,
                ["/index.json"]
                + [
                    f"/{str(summary['path']).lstrip('/')}"
                    for summary in index["snapshots"]
                ],
            )

    def test_http_cache_is_bound_to_the_normalized_index_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            with _serve_directory(remote_root) as base_url_a:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url_a,
                )
                matching = read_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url_a,
                )
            mismatched_read = read_reference_snapshot_feed_for_app(
                cache_root=cache_root,
                base_url="https://reference-b.example.test/reference-snapshots",
            )
            mismatched_refresh = load_reference_snapshot_feed_for_app(
                cache_root=cache_root,
                base_url="https://127.0.0.1:1/reference-snapshots",
                timeout_seconds=0.2,
            )

        self.assertEqual(first["delivery"]["source"], "http")
        self.assertEqual(matching["delivery"]["source"], "http")
        self.assertEqual(matching["delivery"]["refresh_status"], "cached")
        self.assertEqual(mismatched_read["delivery"]["source"], "bundled")
        self.assertEqual(mismatched_read["delivery"]["refresh_status"], "not_cached")
        self.assertEqual(mismatched_refresh["delivery"]["source"], "bundled")
        self.assertEqual(mismatched_refresh["delivery"]["refresh_status"], "failed")
        self.assertNotEqual(
            mismatched_refresh["delivery"].get("error_code"),
            "cache_write_failed",
        )

    def test_legacy_http_metadata_binds_existing_root_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cache"
            shutil.copytree(SNAPSHOT_ROOT, root)
            (root / ".http-cache.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "index_url": "https://reference-a.example.test/reference-snapshots/index.json",
                        "index_etag": '"legacy"',
                    }
                ),
                encoding="utf-8",
            )
            matching = read_reference_snapshot_feed_for_app(
                cache_root=root,
                base_url="https://reference-a.example.test/reference-snapshots",
            )
            mismatched = read_reference_snapshot_feed_for_app(
                cache_root=root,
                base_url="https://reference-b.example.test/reference-snapshots",
            )

        self.assertEqual(matching["delivery"]["source"], "http")
        self.assertEqual(matching["delivery"]["refresh_status"], "cached")
        self.assertEqual(mismatched["delivery"]["source"], "bundled")
        self.assertEqual(mismatched["delivery"]["refresh_status"], "not_cached")

    def test_corrupt_new_bundle_does_not_fall_back_to_legacy_other_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cache"
            shutil.copytree(SNAPSHOT_ROOT, root)
            (root / ".http-cache.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "index_url": "https://reference-a.example.test/reference-snapshots/index.json",
                        "index_etag": '"legacy"',
                    }
                ),
                encoding="utf-8",
            )
            (root / ".http-cache-bundle.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "index_url": "https://reference-b.example.test/reference-snapshots/index.json",
                        "index": {},
                        "latest": {},
                        "snapshots": [],
                    }
                ),
                encoding="utf-8",
            )
            result = read_reference_snapshot_feed_for_app(
                cache_root=root,
                base_url="https://reference-b.example.test/reference-snapshots",
            )

        self.assertEqual(result["delivery"]["source"], "bundled")
        self.assertEqual(result["delivery"]["refresh_status"], "not_cached")

    def test_broken_bundle_symlink_does_not_fall_back_to_same_source_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cache"
            shutil.copytree(SNAPSHOT_ROOT, root)
            (root / ".http-cache.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "index_url": "https://reference-a.example.test/reference-snapshots/index.json",
                        "index_etag": '"legacy"',
                    }
                ),
                encoding="utf-8",
            )
            try:
                os.symlink(
                    "missing-http-cache-bundle.json",
                    root / ".http-cache-bundle.json",
                )
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink unavailable: {error}")
            result = read_reference_snapshot_feed_for_app(
                cache_root=root,
                base_url="https://reference-a.example.test/reference-snapshots",
            )

        self.assertEqual(result["delivery"]["source"], "bundled")
        self.assertEqual(result["delivery"]["refresh_status"], "not_cached")

    def test_failed_bundle_write_keeps_previous_cache_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            with _serve_directory(remote_root) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                index_path = remote_root / "index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                index["latest_batch_id"] = index["snapshots"][0]["batch_id"]
                index_path.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                original_write = __import__(
                    "scanner.reference_snapshot", fromlist=["_write_json_atomic"]
                )._write_json_atomic

                def fail_bundle_write(path: Path, payload: dict[str, object]) -> None:
                    if path.name == ".http-cache-bundle.json":
                        raise OSError("injected bundle write failure")
                    original_write(path, payload)

                with patch(
                    "scanner.reference_snapshot._write_json_atomic",
                    side_effect=fail_bundle_write,
                ):
                    fallback = load_reference_snapshot_feed_for_app(
                        cache_root=cache_root,
                        base_url=base_url,
                    )
            loaded = load_reference_snapshot_feed(cache_root)

        self.assertEqual(fallback["delivery"]["source"], "cache")
        self.assertEqual(fallback["delivery"]["refresh_status"], "failed")
        self.assertEqual(
            fallback["latest"]["batch_sha256"],
            first["latest"]["batch_sha256"],
        )
        self.assertEqual(loaded["status"], "loaded")
        self.assertEqual(
            loaded["latest"]["batch_sha256"],
            first["latest"]["batch_sha256"],
        )

    def test_failed_atomic_replace_keeps_previous_cache_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            with _serve_directory(remote_root) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                bundle_before = (cache_root / ".http-cache-bundle.json").read_bytes()
                index_path = remote_root / "index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                index["latest_batch_id"] = index["snapshots"][0]["batch_id"]
                index_path.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                original_replace = Path.replace

                def fail_bundle_replace(self: Path, target: Path) -> Path:
                    if target.name == ".http-cache-bundle.json":
                        raise OSError("injected bundle replace failure")
                    return original_replace(self, target)

                with patch.object(Path, "replace", new=fail_bundle_replace):
                    fallback = load_reference_snapshot_feed_for_app(
                        cache_root=cache_root,
                        base_url=base_url,
                    )
            bundle_after = (cache_root / ".http-cache-bundle.json").read_bytes()
            loaded = load_reference_snapshot_feed(cache_root)

        self.assertEqual(fallback["delivery"]["source"], "cache")
        self.assertEqual(fallback["delivery"]["refresh_status"], "failed")
        self.assertEqual(bundle_after, bundle_before)
        self.assertEqual(loaded["status"], "loaded")
        self.assertEqual(
            loaded["latest"]["batch_sha256"],
            first["latest"]["batch_sha256"],
        )

    def test_legacy_source_is_not_rebound_when_new_bundle_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            remote_root = root / "remote"
            shutil.copytree(SNAPSHOT_ROOT, cache_root)
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            (cache_root / ".http-cache.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "index_url": "https://reference-a.example.test/reference-snapshots/index.json",
                        "index_etag": '"legacy"',
                    }
                ),
                encoding="utf-8",
            )
            with _serve_directory(remote_root) as base_url_b:
                original_replace = Path.replace

                def fail_bundle_replace(self: Path, target: Path) -> Path:
                    if target.name == ".http-cache-bundle.json":
                        raise OSError("injected bundle replace failure")
                    return original_replace(self, target)

                with patch.object(Path, "replace", new=fail_bundle_replace):
                    result = load_reference_snapshot_feed_for_app(
                        cache_root=cache_root,
                        base_url=base_url_b,
                    )

        self.assertEqual(result["delivery"]["source"], "bundled")
        self.assertEqual(result["delivery"]["refresh_status"], "failed")
        self.assertEqual(result["delivery"]["error_code"], "cache_write_failed")

    def test_successful_bundle_switch_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            with _serve_directory(remote_root) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                index_path = remote_root / "index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                index["latest_batch_id"] = index["snapshots"][0]["batch_id"]
                index_path.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                second = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
            bundle = json.loads(
                (cache_root / ".http-cache-bundle.json").read_text(encoding="utf-8")
            )
            reloaded = load_reference_snapshot_feed(cache_root)

        self.assertEqual(second["delivery"]["source"], "http")
        self.assertEqual(second["delivery"]["refresh_status"], "refreshed")
        self.assertNotEqual(second["latest"]["batch_sha256"], first["latest"]["batch_sha256"])
        self.assertEqual(reloaded["latest"]["batch_sha256"], second["latest"]["batch_sha256"])
        self.assertEqual(bundle["schema_version"], 1)

    def test_default_http_timeout_is_applied_to_index_and_archives(self) -> None:
        index = json.loads(
            (SNAPSHOT_ROOT / "index.json").read_text(encoding="utf-8")
        )
        timeout_calls: list[tuple[str, float]] = []

        def read_http_json(
            url: str,
            *,
            timeout_seconds: float,
            max_bytes: int,
            if_none_match: str | None = None,
            allow_not_modified: bool = False,
        ) -> _HttpJsonResponse:
            del max_bytes, if_none_match, allow_not_modified
            timeout_calls.append((url, timeout_seconds))
            if url.endswith("/index.json"):
                return _HttpJsonResponse(payload=index, etag='"index"')
            relative_path = url.split(
                "/reference-snapshots/", maxsplit=1
            )[1]
            payload = json.loads(
                (SNAPSHOT_ROOT / relative_path).read_text(encoding="utf-8")
            )
            return _HttpJsonResponse(payload=payload, etag=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "scanner.reference_snapshot._read_http_json",
                side_effect=read_http_json,
            ):
                feed = load_reference_snapshot_feed_for_app(
                    cache_root=Path(temp_dir) / "cache",
                    base_url="https://reference.example.test/reference-snapshots",
                )

        self.assertEqual(feed["delivery"]["refresh_status"], "refreshed")
        self.assertEqual(
            len(timeout_calls),
            1 + len(index["snapshots"]),
        )
        self.assertTrue(timeout_calls[0][0].endswith("/index.json"))
        self.assertTrue(
            all(
                url.endswith(".json") and not url.endswith("/index.json")
                for url, _ in timeout_calls[1:]
            )
        )
        self.assertEqual(
            [timeout for _, timeout in timeout_calls],
            [DEFAULT_REFERENCE_SNAPSHOT_TIMEOUT_SECONDS] * len(timeout_calls),
        )

    def test_http_feed_retries_one_transient_download_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            original_read = reference_snapshot_module._read_http_json
            call_count = 0

            def flaky_read(*args: object, **kwargs: object) -> _HttpJsonResponse:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ReferenceSnapshotDownloadError("unavailable")
                return original_read(*args, **kwargs)

            with _serve_directory(remote_root) as base_url:
                with patch(
                    "scanner.reference_snapshot._read_http_json",
                    side_effect=flaky_read,
                ):
                    feed = load_reference_snapshot_feed_for_app(
                        cache_root=cache_root,
                        base_url=base_url,
                    )

        self.assertGreater(call_count, 1)
        self.assertEqual(feed["delivery"]["source"], "http")
        self.assertEqual(feed["delivery"]["refresh_status"], "refreshed")

    def test_http_feed_accepts_a_full_index_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            request_log: list[str] = []
            shutil.copytree(SNAPSHOT_ROOT, remote_root)

            with _serve_directory(remote_root, request_log) as base_url:
                feed = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=f"{base_url}/index.json",
                )

            self.assertEqual(feed["delivery"]["refresh_status"], "refreshed")
            self.assertEqual(request_log[0], "/index.json")

    def test_http_feed_uses_etag_and_keeps_cache_on_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            request_log: list[str] = []
            index_request_headers: list[str | None] = []
            index_request_cache_controls: list[str | None] = []
            shutil.copytree(SNAPSHOT_ROOT, remote_root)

            with _serve_directory(
                remote_root,
                request_log,
                index_request_headers=index_request_headers,
                index_request_cache_controls=index_request_cache_controls,
            ) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                first_request_count = len(request_log)
                second = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )

            self.assertEqual(first["delivery"]["refresh_status"], "refreshed")
            self.assertEqual(second["delivery"]["refresh_status"], "not_modified")
            self.assertEqual(request_log[first_request_count:], ["/index.json"])
            self.assertIsNone(index_request_headers[0])
            self.assertTrue(index_request_headers[1])
            self.assertEqual(
                index_request_cache_controls,
                ["no-cache", "no-cache"],
            )
            self.assertEqual(
                second["latest"]["batch_sha256"],
                first["latest"]["batch_sha256"],
            )

    def test_http_feed_downloads_only_new_archives_after_index_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            request_log: list[str] = []
            index_request_headers: list[str | None] = []
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            index_path = remote_root / "index.json"
            complete_index = json.loads(index_path.read_text(encoding="utf-8"))
            initial_index = deepcopy(complete_index)
            initial_index["snapshots"] = initial_index["snapshots"][:2]
            initial_latest = initial_index["snapshots"][-1]
            initial_index["latest_batch_id"] = initial_latest["batch_id"]
            index_path.write_text(
                json.dumps(initial_index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            shutil.copyfile(
                remote_root / initial_latest["path"],
                remote_root / "latest.json",
            )

            with _serve_directory(
                remote_root,
                request_log,
                index_request_headers=index_request_headers,
            ) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                index_path.write_text(
                    json.dumps(complete_index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                shutil.copyfile(SNAPSHOT_ROOT / "latest.json", remote_root / "latest.json")
                second_request_start = len(request_log)
                second = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )

            self.assertEqual(len(first["snapshots"]), 2)
            self.assertEqual(len(second["snapshots"]), len(complete_index["snapshots"]))
            self.assertEqual(
                request_log[second_request_start:],
                ["/index.json"]
                + [
                    f"/{str(summary['path']).lstrip('/')}"
                    for summary in complete_index["snapshots"][2:]
                ],
            )

    def test_http_feed_recovers_from_a_corrupt_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            request_log: list[str] = []
            index_request_headers: list[str | None] = []
            shutil.copytree(SNAPSHOT_ROOT, remote_root)

            with _serve_directory(
                remote_root,
                request_log,
                index_request_headers=index_request_headers,
            ) as base_url:
                load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                (cache_root / ".http-cache-bundle.json").write_text(
                    "{}\n",
                    encoding="utf-8",
                )
                second_request_start = len(request_log)
                recovered = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )

            self.assertEqual(recovered["delivery"]["refresh_status"], "refreshed")
            self.assertEqual(request_log[second_request_start], "/index.json")
            self.assertIsNone(index_request_headers[-1])

    def test_tampered_http_feed_keeps_last_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            cache_root = root / "cache"
            shutil.copytree(SNAPSHOT_ROOT, remote_root)
            with _serve_directory(remote_root) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
                expected_hash = first["latest"]["batch_sha256"]
                index = json.loads(
                    (remote_root / "index.json").read_text(encoding="utf-8")
                )
                latest_path = remote_root / next(
                    summary["path"]
                    for summary in index["snapshots"]
                    if summary["batch_id"] == index["latest_batch_id"]
                )
                latest = json.loads(latest_path.read_text(encoding="utf-8"))
                latest["entries"][0]["score"] += 1
                latest_path.write_text(
                    json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                for summary in index["snapshots"]:
                    if summary["batch_id"] == index["latest_batch_id"]:
                        summary["batch_sha256"] = "sha256:" + ("0" * 64)
                (remote_root / "index.json").write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                fallback = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )

            self.assertEqual(fallback["status"], "loaded")
            self.assertEqual(fallback["delivery"]["source"], "cache")
            self.assertEqual(fallback["delivery"]["refresh_status"], "failed")
            self.assertEqual(fallback["delivery"]["error_code"], "invalid_payload")
            self.assertEqual(fallback["latest"]["batch_sha256"], expected_hash)

    def test_offline_http_feed_keeps_last_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir) / "cache"
            with _serve_directory(SNAPSHOT_ROOT) as base_url:
                first = load_reference_snapshot_feed_for_app(
                    cache_root=cache_root,
                    base_url=base_url,
                )
            fallback = load_reference_snapshot_feed_for_app(
                cache_root=cache_root,
                base_url=base_url,
                timeout_seconds=0.2,
            )

            self.assertEqual(fallback["status"], "loaded")
            self.assertEqual(fallback["delivery"]["source"], "cache")
            self.assertEqual(fallback["delivery"]["refresh_status"], "failed")
            self.assertEqual(fallback["delivery"]["error_code"], "unavailable")
            self.assertEqual(
                fallback["latest"]["batch_sha256"],
                first["latest"]["batch_sha256"],
            )

    def test_native_snapshot_keeps_seed_feed_but_does_not_pass_it_to_v2_as_official(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            config = config_store.load()
            config.recommendation.current_model_mode = "manual"
            config.recommendation.current_default_candidate_id = CURRENT_ID
            config.recommendation.source_mode_by_configuration_id = {
                CURRENT_ID: "official_snapshot"
            }
            config_store.save(config)

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=ActiveRunStore(root / "active_run.json"),
            )

        self.assertEqual(snapshot["reference_snapshot_feed"]["status"], "loaded")
        self.assertIsNone(snapshot["advisor_v2_evidence"]["source_snapshot_id"])
        self.assertIsNone(snapshot["advisor_v2_evidence"]["resolved_data_source"])
        pairwise = snapshot["reference_snapshot_feed"]["latest"][
            "pairwise_comparisons"
        ]
        self.assertEqual(len(pairwise), 18 * 17)
        self.assertTrue(
            any(
                item["baseline_candidate_id"].endswith(":ultra")
                and item["candidate_id"].endswith(":xhigh")
                for item in pairwise
            )
        )

    def test_reference_command_refreshes_http_feed_before_pure_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            request_log: list[str] = []
            with _serve_directory(SNAPSHOT_ROOT, request_log=request_log) as base_url:
                with patch.dict(
                    os.environ,
                    {"MODELDIAL_REFERENCE_SNAPSHOT_URL": base_url},
                ):
                    response = refresh_reference_snapshots(
                        config_store,
                        history_store,
                        active_run_store,
                    )
                    requests_after_refresh = len(request_log)
                    snapshot = build_snapshot(
                        config_store,
                        history_store,
                        active_run_store,
                    )

            refreshed_feed = response["state"]["reference_snapshot_feed"]
            feed = snapshot["reference_snapshot_feed"]
            self.assertGreater(requests_after_refresh, 0)
            self.assertEqual(len(request_log), requests_after_refresh)
            self.assertEqual(response["status"], "refreshed")
            self.assertEqual(refreshed_feed["delivery"]["source"], "http")
            self.assertEqual(
                refreshed_feed["delivery"]["refresh_status"],
                "refreshed",
            )
            self.assertEqual(feed["delivery"]["source"], "http")
            self.assertEqual(feed["delivery"]["refresh_status"], "cached")
            self.assertTrue(
                (root / "reference_snapshots" / ".http-cache-bundle.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
