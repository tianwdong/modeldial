from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import scanner.graders as graders
import scanner.black_box_regression_grader as black_box_regression_grader
import scanner.candidate_sandbox as candidate_sandbox
from scanner.bounded_subprocess import BoundedSubprocessOutputError
from scanner.black_box_session_store_reference import _schema_errors, _snapshot
from scanner.graders import (
    _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE,
    _mutation_cache_test_mutants,
    _micro_repo_cache_patch_cases,
    _micro_unified_diff_cases,
    grade_answer,
)
from scanner.compact_session_repair_grader import CLUSTER_LABELS, MAX_SCORE


CORRECT_UNIFIED_DIFF_SOLUTION = r'''
def apply_unified_diff(files, diff_text):
    import copy
    out = copy.deepcopy(files)
    lines = diff_text.splitlines(True)
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git a/"):
            raise ValueError("missing diff header")
        header_parts = lines[i].rstrip("\n").split()
        if len(header_parts) != 4:
            raise ValueError("bad diff header")
        git_old = header_parts[2][2:] if header_parts[2].startswith("a/") else header_parts[2]
        git_new = header_parts[3][2:] if header_parts[3].startswith("b/") else header_parts[3]
        i += 1
        if i + 1 >= len(lines):
            raise ValueError("missing file header")
        old_header = lines[i].rstrip("\n")
        new_header = lines[i + 1].rstrip("\n")
        i += 2
        if not old_header.startswith("--- ") or not new_header.startswith("+++ "):
            raise ValueError("bad file header")
        old_path = old_header[4:]
        new_path = new_header[4:]
        if old_path.startswith("a/"):
            old_path = old_path[2:]
        if new_path.startswith("b/"):
            new_path = new_path[2:]
        is_new = old_path == "/dev/null"
        is_delete = new_path == "/dev/null"
        if not is_new and git_old != old_path:
            raise ValueError("old path mismatch")
        if not is_delete and git_new != new_path:
            raise ValueError("new path mismatch")
        if is_new:
            if new_path in out:
                raise ValueError("new file exists")
            old_lines = []
            target_path = new_path
        else:
            if old_path not in out:
                raise ValueError("missing old file")
            if not is_delete and old_path != new_path:
                raise ValueError("rename unsupported")
            old_lines = out[old_path].splitlines(True)
            target_path = old_path
        result = []
        old_index = 0
        saw_hunk = False
        while i < len(lines) and not lines[i].startswith("diff --git a/"):
            header = lines[i].rstrip("\n")
            if not header.startswith("@@ ") or " @@" not in header:
                raise ValueError("bad hunk")
            body = header.split(" @@")[0][3:]
            old_part, new_part = body.split(" ")
            def parse(part):
                sign = part[0]
                rest = part[1:]
                if "," in rest:
                    start, count = rest.split(",", 1)
                    return int(start), int(count)
                return int(rest), 1
            old_start, old_count = parse(old_part)
            new_start, new_count = parse(new_part)
            start_index = old_start if old_count == 0 else max(old_start - 1, 0)
            if start_index < old_index:
                raise ValueError("overlap")
            result.extend(old_lines[old_index:start_index])
            old_index = start_index
            i += 1
            saw_hunk = True
            old_seen = 0
            new_seen = 0
            while i < len(lines) and not lines[i].startswith("diff --git a/") and not lines[i].startswith("@@ "):
                line = lines[i]
                if not line:
                    raise ValueError("bad line")
                prefix = line[0]
                content = line[1:]
                if prefix == " ":
                    if old_index >= len(old_lines) or old_lines[old_index] != content:
                        raise ValueError("context mismatch")
                    result.append(content)
                    old_index += 1
                    old_seen += 1
                    new_seen += 1
                elif prefix == "-":
                    if is_new:
                        raise ValueError("new file old line")
                    if old_index >= len(old_lines) or old_lines[old_index] != content:
                        raise ValueError("delete mismatch")
                    old_index += 1
                    old_seen += 1
                elif prefix == "+":
                    if is_delete:
                        raise ValueError("deleted file addition")
                    result.append(content)
                    new_seen += 1
                else:
                    raise ValueError("bad prefix")
                i += 1
            if old_seen != old_count or new_seen != new_count:
                raise ValueError("hunk count mismatch")
        if not saw_hunk:
            raise ValueError("no hunks")
        result.extend(old_lines[old_index:])
        if is_delete:
            del out[old_path]
        else:
            out[target_path] = "".join(result)
    return out
'''


CORRECT_CACHE_RUNNER_SOURCE = r'''from copy import deepcopy

from cache_policy import build_cache_entry, choose_invalidation_reason

REASON_ORDER = [
    "force_rescan",
    "not_cached",
    "corrupted",
    "file_changed",
    "config_changed",
    "profile_changed",
    "options_changed",
    "expired",
]


def run_scan(files, cache, params):
    """
    files: list of dicts with:
      path, content_hash, issue_count

    cache: dict mapping path -> cache entry

    params: dict with:
      current_day
      config_hash
      profile_name
      profile_hash
      options_key
      cache_expiry_days
      force_rescan
      warm_cache

    Returns:
      {
        "cache": new_cache,
        "metrics": {
          "cache_hits": int,
          "cache_misses": int,
          "invalidation_counts": dict,
          "scanned_files": list[str],
          "reported_issues": list[[path, issue_count]],
          "total_reported_issues": int
        }
      }
    """
    new_cache = deepcopy(cache)

    cache_hits = 0
    cache_misses = 0
    invalidation_counts = {}
    scanned_files = []
    reported_issues = []

    warm_cache = bool(params.get("warm_cache"))

    for f in files:
        path = f["path"]
        entry = new_cache.get(path)
        reason = choose_invalidation_reason(f, entry, params)

        if reason is None:
            cache_hits += 1
            issue_count = int(entry.get("issue_count", 0))
        else:
            cache_misses += 1
            invalidation_counts[reason] = invalidation_counts.get(reason, 0) + 1
            scanned_files.append(path)
            issue_count = int(f.get("issue_count", 0))

            new_cache[path] = build_cache_entry(f, params, issue_count)

        if not warm_cache and issue_count > 0:
            reported_issues.append([path, issue_count])

    reported_issues.sort(key=lambda item: item[0])
    scanned_files.sort()

    return {
        "cache": new_cache,
        "metrics": {
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "invalidation_counts": invalidation_counts,
            "scanned_files": scanned_files,
            "reported_issues": reported_issues,
            "total_reported_issues": sum(item[1] for item in reported_issues),
        },
}
'''


CORRECT_CACHE_POLICY_SOURCE = r'''REASON_ORDER = [
    "force_rescan",
    "not_cached",
    "corrupted",
    "file_changed",
    "config_changed",
    "profile_changed",
    "options_changed",
    "expired",
]


def choose_invalidation_reason(file_info, entry, params):
    if params.get("force_rescan"):
        return "force_rescan"
    if entry is None:
        return "not_cached"
    if entry.get("corrupted"):
        return "corrupted"
    if entry.get("path") != file_info.get("path") or entry.get("content_hash") != file_info.get("content_hash"):
        return "file_changed"
    if entry.get("config_hash") != params.get("config_hash"):
        return "config_changed"
    if (
        entry.get("profile_name") != params.get("profile_name")
        or entry.get("profile_hash") != params.get("profile_hash")
    ):
        return "profile_changed"
    if entry.get("options_key") != params.get("options_key"):
        return "options_changed"

    expiry_days = int(params.get("cache_expiry_days", 7))
    age_days = int(params["current_day"]) - int(entry.get("stored_day", 0))
    if expiry_days == 0 or age_days > expiry_days:
        return "expired"
    return None


def build_cache_entry(file_info, params, issue_count):
    return {
        "path": file_info["path"],
        "content_hash": file_info["content_hash"],
        "config_hash": params["config_hash"],
        "profile_name": params["profile_name"],
        "profile_hash": params["profile_hash"],
        "options_key": params["options_key"],
        "stored_day": params["current_day"],
        "issue_count": issue_count,
        "corrupted": False,
    }
'''


def _correct_cache_runner_patch() -> str:
    return "".join(
        difflib.unified_diff(
            _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE.splitlines(True),
            CORRECT_CACHE_RUNNER_SOURCE.splitlines(True),
            fromfile="a/cache_runner.py",
            tofile="b/cache_runner.py",
        )
    )


def _cache_runner_patch_without_options_check() -> str:
    faulty_source = CORRECT_CACHE_RUNNER_SOURCE.replace(
        '        elif entry.get("options_key") != params.get("options_key"):\n'
        '            reason = "options_changed"\n',
        "",
    )
    return "".join(
        difflib.unified_diff(
            _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE.splitlines(True),
            faulty_source.splitlines(True),
            fromfile="a/cache_runner.py",
            tofile="b/cache_runner.py",
        )
    )


def _correct_cache_runner_search_replace_patch() -> str:
    initial_files = getattr(graders, "_MICRO_REPO_CACHE_PATCH_INITIAL_FILES", {})
    return (
        "*** Begin Patch\n"
        "*** Update File: cache_policy.py\n"
        "<<<<<<< SEARCH\n"
        f"{initial_files.get('cache_policy.py', '')}"
        "=======\n"
        f"{CORRECT_CACHE_POLICY_SOURCE}"
        ">>>>>>> REPLACE\n"
        "*** Update File: cache_runner.py\n"
        "<<<<<<< SEARCH\n"
        f"{initial_files.get('cache_runner.py', _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE)}"
        "=======\n"
        f"{CORRECT_CACHE_RUNNER_SOURCE}"
        ">>>>>>> REPLACE\n"
        "*** End Patch\n"
    )


def _cache_runner_search_replace_patch_without_options_check() -> str:
    faulty_source = CORRECT_CACHE_RUNNER_SOURCE.replace(
        '        elif entry.get("options_key") != params.get("options_key"):\n'
        '            reason = "options_changed"\n',
        "",
    )
    return (
        "*** Begin Patch\n"
        "*** Update File: cache_runner.py\n"
        "<<<<<<< SEARCH\n"
        f"{_MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE}"
        "=======\n"
        f"{faulty_source}"
        ">>>>>>> REPLACE\n"
        "*** End Patch\n"
    )


def _base_entry_for_mutation(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "path": "a.py",
        "content_hash": "ha",
        "config_hash": "cfg",
        "profile_name": "strict",
        "profile_hash": "p1",
        "options_key": "opt",
        "stored_day": 10,
        "issue_count": 2,
        "corrupted": False,
    }
    entry.update(overrides)
    return entry


def _base_params_for_mutation(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "current_day": 12,
        "config_hash": "cfg",
        "profile_name": "strict",
        "profile_hash": "p1",
        "options_key": "opt",
        "cache_expiry_days": 7,
        "force_rescan": False,
        "warm_cache": False,
    }
    params.update(overrides)
    return params


class GradersTest(unittest.TestCase):
    def test_black_box_regression_reference_uses_external_backend_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external_reference = (
                Path(temp_dir) / "scanner" / "black_box_session_store_reference.py"
            )
            external_reference.parent.mkdir()
            external_reference.write_text("REFERENCE_SOURCE = True\n", encoding="utf-8")
            with (
                mock.patch.dict(
                    "os.environ",
                    {"MODELDIAL_BACKEND_ROOT": temp_dir},
                    clear=False,
                ),
                mock.patch.object(
                    black_box_regression_grader,
                    "REFERENCE_PATH",
                    Path(temp_dir) / "missing" / "black_box_session_store_reference.py",
                ),
            ):
                source = black_box_regression_grader._reference_source()

        self.assertEqual(source, "REFERENCE_SOURCE = True\n")

    def test_black_box_regression_disclosed_fixture_contract_matches_reference(self) -> None:
        nested = MappingProxyType({"answer": 42})
        metadata = MappingProxyType(
            {
                "format": "session-bundle",
                "format_version": 1,
                "event_count": 2,
                "nested": nested,
            }
        )
        events = [
            {
                "type": "cell",
                "seq": 1,
                "code": "value = 1",
                "success": True,
                "execution_count": 1,
                "stdout": "",
                "stderr": "",
                "execute_result": {
                    "text/plain": "42",
                    "application/json": nested,
                },
            },
            {
                "type": "cell",
                "seq": 2,
                "code": "raise RuntimeError('boom')",
                "success": False,
                "execution_count": None,
                "stdout": "",
                "stderr": "",
                "execute_result": {},
                "error": {
                    "ename": "RuntimeError",
                    "evalue": "boom",
                    "traceback": ["RuntimeError: boom"],
                },
            },
        ]

        self.assertEqual(_schema_errors(metadata, events), [])
        json.dumps(_snapshot({"metadata": metadata, "events": events}))

    def test_black_box_regression_accepts_safe_stdlib_and_ordinary_attributes(self) -> None:
        black_box_regression_grader._validate_test_source(
            "import hashlib\n"
            "import itertools\n"
            "import threading\n"
            "class Helper: pass\n"
            "helper = Helper()\n"
            "helper.__dict__['ready'] = True\n"
            "setattr(helper, 'value', 1)\n"
            "assert getattr(helper, 'value') == 1\n"
            "with open(__file__, encoding='utf-8') as stream:\n"
            "    assert stream.read(0) == ''\n"
        )

        with self.assertRaisesRegex(ValueError, "forbidden_test_import:requests"):
            black_box_regression_grader._validate_test_source("import requests\n")
        with self.assertRaisesRegex(ValueError, "forbidden_test_attribute:__code__"):
            black_box_regression_grader._validate_test_source(
                "def helper(): pass\nhelper.__code__\n"
            )
        with self.assertRaisesRegex(ValueError, "forbidden_test_attribute:__code__"):
            black_box_regression_grader._validate_test_source(
                "def helper(): pass\ngetattr(helper, '__code__')\n"
            )

        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: test_session_store.py\n"
            "<<<<<<< SEARCH\n"
            "import json\n"
            "=======\n"
            "import json\n"
            "import hashlib\n"
            "import itertools\n"
            "import threading\n\n"
            "class Helper: pass\n"
            "helper = Helper()\n"
            "helper.__dict__['ready'] = True\n"
            ">>>>>>> REPLACE\n"
            "*** End Patch\n"
        )
        integration_result = grade_answer(
            patch_text,
            {
                "kind": "black_box_regression_proof",
                "test_suite": "black_box_regression_v3",
                "pass_threshold": 20,
            },
        )
        self.assertEqual(integration_result.diagnostics["status"], "semantic_failed")
        self.assertTrue(integration_result.diagnostics["regression_proof"]["valid"])
        self.assertNotIn("forbidden_test_attribute", integration_result.summary)

    def test_black_box_regression_uses_disclosed_single_and_combined_timeouts(self) -> None:
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                black_box_regression_grader,
                "SANDBOX_EXECUTABLE",
                "/usr/bin/sandbox-exec",
            ),
            mock.patch.object(
                black_box_regression_grader.subprocess,
                "run",
                return_value=completed,
            ) as run_mock,
        ):
            black_box_regression_grader._run_submitted_tests(
                black_box_regression_grader._reference_source(),
                "import unittest\n",
                ["test_session_store.T.test_one"],
            )
            single_timeout = run_mock.call_args.kwargs["timeout"]
            black_box_regression_grader._run_submitted_tests(
                black_box_regression_grader._reference_source(),
                "import unittest\n",
                [f"test_session_store.T.test_{index}" for index in range(6)],
            )
            combined_timeout = run_mock.call_args.kwargs["timeout"]

        self.assertEqual(single_timeout, 2.5)
        self.assertEqual(combined_timeout, 10.0)

    def test_black_box_regression_rejects_reference_incompatible_test_combinations(self) -> None:
        test_source = (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_first(self): self.assertTrue(True)\n"
            "    def test_second(self): self.assertTrue(True)\n"
        )

        def run_tests(_implementation, _source, test_ids):  # type: ignore[no-untyped-def]
            if len(test_ids) > 1:
                return False, "test_timeout"
            return True, ""

        with (
            mock.patch.object(
                black_box_regression_grader,
                "_apply_response_patch",
                return_value=(test_source, 1),
            ),
            mock.patch.object(
                black_box_regression_grader,
                "_run_submitted_tests",
                side_effect=run_tests,
            ),
            mock.patch.object(
                black_box_regression_grader,
                "_mutant_sources",
                return_value={},
            ),
        ):
            result = black_box_regression_grader.grade_response("unused")

        proof = result["regression_proof"]
        self.assertEqual(proof["valid_tests"], ["test_session_store.T.test_first"])
        self.assertEqual(
            proof["invalid_tests"],
            [
                {
                    "test_id": "test_session_store.T.test_second",
                    "reference_error": "combined_reference_error:test_timeout",
                }
            ],
        )

    def test_black_box_regression_reports_sandbox_failure_as_unscored(self) -> None:
        test_source = (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n"
        )
        grader = {
            "kind": "black_box_regression_proof",
            "test_suite": "black_box_regression_v2",
            "pass_threshold": 20,
        }
        with (
            mock.patch.object(
                black_box_regression_grader,
                "_apply_response_patch",
                return_value=(test_source, 1),
            ),
            mock.patch.object(
                black_box_regression_grader,
                "_run_submitted_tests",
                return_value=(False, "sandbox_unavailable:FileNotFoundError"),
            ),
        ):
            result = grade_answer("unused", grader)

        self.assertFalse(result.ok)
        self.assertIsNone(result.score)
        self.assertEqual(result.max_score, 20)
        self.assertEqual(result.summary, "black_box_regression_v2 grader_unavailable")
        self.assertEqual(result.diagnostics["status"], "grader_unavailable")
        self.assertNotIn("semantic_passed", result.diagnostics)

    def test_black_box_regression_allows_time_and_separates_validation_failure(self) -> None:
        grader = {
            "kind": "black_box_regression_proof",
            "test_suite": "black_box_regression_v2",
            "pass_threshold": 20,
        }
        time_patch = (
            "*** Begin Patch\n"
            "*** Update File: test_session_store.py\n"
            "<<<<<<< SEARCH\n"
            "import json\n"
            "=======\n"
            "import json\n"
            "import time\n"
            ">>>>>>> REPLACE\n"
            "*** End Patch\n"
        )

        time_result = grade_answer(time_patch, grader)

        self.assertEqual("semantic_failed", time_result.diagnostics["status"])
        self.assertNotIn("forbidden_test_import:time", time_result.summary)

        forbidden_patch = time_patch.replace("import time", "import socket")
        forbidden_result = grade_answer(forbidden_patch, grader)

        self.assertEqual(
            "submission_validation_failed",
            forbidden_result.diagnostics["status"],
        )
        self.assertEqual(
            "black_box_regression_v2 submission_validation_failed",
            forbidden_result.summary,
        )
        self.assertIn(
            "forbidden_test_import:socket",
            forbidden_result.diagnostics["failure_summary"],
        )

        invalid_patch_result = grade_answer("not a patch", grader)

        self.assertEqual(
            "patch_apply_failed",
            invalid_patch_result.diagnostics["status"],
        )
        self.assertEqual(
            "black_box_regression_v2 patch_apply_failed",
            invalid_patch_result.summary,
        )

    def test_session_bundle_reference_patch_passes_all_hidden_cases(self) -> None:
        patch_text = (
            Path(__file__).parent / "fixtures" / "session_bundle_reference.patch"
        ).read_text(encoding="utf-8")

        result = grade_answer(
            patch_text,
            {
                "kind": "session_bundle_patch",
                "test_suite": "compact_session_repair_v1",
                "timeout_seconds": 8,
                "pass_threshold": 10,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, MAX_SCORE)
        self.assertEqual(result.max_score, MAX_SCORE)
        self.assertEqual("passed", result.diagnostics["status"])
        self.assertTrue(result.diagnostics["patch_format_ok"])
        self.assertTrue(result.diagnostics["patch_applies"])

    def test_session_bundle_incomplete_patch_keeps_partial_score_but_fails(self) -> None:
        result = grade_answer(
            """*** Begin Patch
*** Update File: session_store.py
<<<<<<< SEARCH
def _exact_int(value: object) -> bool:
    return isinstance(value, int)
=======
def _exact_int(value: object) -> bool:
    return type(value) is int
>>>>>>> REPLACE
*** End Patch
""",
            {
                "kind": "session_bundle_patch",
                "test_suite": "compact_session_repair_v1",
                "timeout_seconds": 8,
                "pass_threshold": 10,
            },
        )

        self.assertFalse(result.ok)
        self.assertGreater(result.score or 0, 0)
        self.assertLess(result.score or 0, MAX_SCORE)
        self.assertEqual("semantic_failed", result.diagnostics["status"])
        self.assertTrue(result.diagnostics["patch_applies"])

    def test_session_bundle_patch_allows_safe_stdlib_imports_used_by_candidates(self) -> None:
        for module in ("math", "zlib"):
            with self.subTest(module=module):
                result = grade_answer(
                    f"""*** Begin Patch
*** Update File: session_store.py
<<<<<<< SEARCH
import json
import os
=======
import json
import {module}
import os
>>>>>>> REPLACE
*** End Patch
""",
                    {
                        "kind": "session_bundle_patch",
                        "test_suite": "compact_session_repair_v1",
                        "timeout_seconds": 8,
                        "pass_threshold": 10,
                    },
                )

                self.assertNotEqual("runner_error", result.diagnostics["status"])
                self.assertTrue(result.diagnostics["patch_applies"])

    def test_session_bundle_nonunique_search_is_format_valid_but_does_not_apply(self) -> None:
        result = grade_answer(
            """*** Begin Patch
*** Update File: session_store.py
<<<<<<< SEARCH
    return errors
=======
    return list(errors)
>>>>>>> REPLACE
*** End Patch
""",
            {
                "kind": "session_bundle_patch",
                "test_suite": "compact_session_repair_v1",
                "timeout_seconds": 8,
                "pass_threshold": 10,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(0, result.score)
        self.assertEqual("patch_apply_failed", result.diagnostics["status"])
        self.assertTrue(result.diagnostics["patch_format_ok"])
        self.assertFalse(result.diagnostics["patch_applies"])

    def test_session_bundle_patch_rejects_files_outside_the_starter_repo(self) -> None:
        result = grade_answer(
            """*** Begin Patch
*** Update File: unrelated.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
*** End Patch
""",
            {
                "kind": "session_bundle_patch",
                "test_suite": "compact_session_repair_v1",
                "timeout_seconds": 8,
                "pass_threshold": 10,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(0, result.score)
        self.assertEqual("patch_apply_failed", result.diagnostics["status"])
        self.assertFalse(result.diagnostics["patch_applies"])

    def test_session_bundle_hidden_case_mix_is_fixed(self) -> None:
        self.assertEqual(MAX_SCORE, 10)
        self.assertEqual(len(CLUSTER_LABELS), 10)
        self.assertEqual(
            list(CLUSTER_LABELS),
            [
                "archive_cardinality",
                "json_diagnostics",
                "schema_order",
                "nested_aggregation",
                "validation_contract",
                "overwrite_precedence",
                "snapshot_ownership",
                "atomic_failure",
                "actual_replay",
                "history_restoration",
            ],
        )

    def test_regex_grader_accepts_the_expected_integer(self) -> None:
        result = grade_answer(
            "主人，21",
            {"kind": "regex", "pattern": r"(?<!\d)21(?!\d)"},
        )
        self.assertTrue(result.ok)

    def test_json_exact_grader_requires_structural_match(self) -> None:
        result = grade_answer(
            '{"budget": 8, "gap": 12}',
            {"kind": "json_exact", "expected": {"budget": 8, "gap": 12}},
        )
        self.assertTrue(result.ok)

    def test_json_exact_grader_reports_nested_mismatch_paths(self) -> None:
        result = grade_answer(
            '{"largest_gap":{"budget":7,"gap":12},"extra":true}',
            {
                "kind": "json_exact",
                "expected": {
                    "largest_gap": {"budget": 8, "gap": 12},
                    "second_largest_gap": {"budget": 7, "gap": 8},
                },
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.diagnostics["mismatch_paths"],
            ["$.extra", "$.largest_gap.budget", "$.second_largest_gap"],
        )

    def test_retry_counterexample_v2_grader_scores_ten_independent_mutants(self) -> None:
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "retry_planner_counterexamples.json").read_text(
                encoding="utf-8"
            )
        )
        result = grade_answer(
            json.dumps(payload),
            {
                "kind": "retry_counterexample_design",
                "test_suite": "retry_planner_mutants_v2",
                "pass_threshold": 10,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))
        self.assertEqual(result.diagnostics["semantic_passed"], 10)
        self.assertEqual(len(result.diagnostics["score_details"]), 10)

        payload["counterexamples"] = payload["counterexamples"][:1]
        partial = grade_answer(
            json.dumps(payload),
            {
                "kind": "retry_counterexample_design",
                "test_suite": "retry_planner_mutants_v2",
                "pass_threshold": 10,
            },
        )
        self.assertFalse(partial.ok)
        self.assertEqual((partial.score, partial.max_score), (9, 10))
        self.assertEqual(
            partial.diagnostics["survived_mutants"],
            ["quota_backfill"],
        )

        invalid = grade_answer(
            "not json",
            {
                "kind": "retry_counterexample_design",
                "test_suite": "retry_planner_mutants_v2",
                "pass_threshold": 10,
            },
        )
        self.assertEqual((invalid.score, invalid.max_score), (0, 10))
        self.assertEqual(invalid.diagnostics["status"], "invalid_counterexamples")

    def test_retry_counterexample_v1_grader_remains_compatible(self) -> None:
        payload = (
            Path(__file__).parent / "fixtures" / "retry_planner_counterexamples_v1.json"
        ).read_text(encoding="utf-8")

        result = grade_answer(
            payload,
            {
                "kind": "retry_counterexample_design",
                "test_suite": "retry_planner_mutants_v1",
                "pass_threshold": 10,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))

    def test_retry_counterexample_accepts_every_prompt_legal_string_and_empty_records(self) -> None:
        grader = {
            "kind": "retry_counterexample_design",
            "test_suite": "retry_planner_mutants_v3",
            "pass_threshold": 20,
        }
        params = {
            "now": 0,
            "max_attempts": 0,
            "global_limit": 0,
            "per_group_limit": 0,
        }
        empty = grade_answer(
            json.dumps(
                {
                    "counterexamples": [
                        {"name": "same.name/重复", "records": [], "params": params},
                        {"name": "same.name/重复", "records": [], "params": params},
                    ]
                }
            ),
            grader,
        )
        self.assertEqual(empty.diagnostics["status"], "semantic_failed")

        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "retry_planner_counterexamples.json").read_text(
                encoding="utf-8"
            )
        )
        payload["counterexamples"][0]["records"][0]["job_id"] = "job/" + ("长" * 80)
        payload["counterexamples"][0]["records"][0]["group"] = "tenant.core/生产"
        long_identifiers = grade_answer(json.dumps(payload), grader)
        self.assertNotEqual(long_identifiers.diagnostics["status"], "invalid_counterexamples")

    def test_dual_coverage_grader_scores_both_repositories(self) -> None:
        payload = {
            "frontier": [
                {"budget": 13, "greedy": ["A", "B", "C", "G", "H"], "greedy_score": 84, "optimal": ["A", "G", "H", "I", "K"], "optimal_score": 95, "gap": 11},
                {"budget": 7, "greedy": ["A", "B", "G"], "greedy_score": 57, "optimal": ["A", "E", "G"], "optimal_score": 67, "gap": 10},
            ],
            "mirror": [
                {"budget": 6, "greedy": ["A", "F", "H", "I"], "greedy_score": 54, "optimal": ["A", "F", "G", "I"], "optimal_score": 80, "gap": 26},
                {"budget": 9, "greedy": ["A", "F", "G", "H", "I"], "greedy_score": 80, "optimal": ["A", "D", "F", "G", "I"], "optimal_score": 91, "gap": 11},
            ],
        }

        result = grade_answer(
            json.dumps(payload),
            {"kind": "coverage_dual_instance", "test_suite": "coverage_dual_v1", "pass_threshold": 10},
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))
        self.assertEqual(len(result.diagnostics["score_details"]), 10)

        payload["mirror"][1]["gap"] = 10
        partial = grade_answer(
            json.dumps(payload),
            {"kind": "coverage_dual_instance", "test_suite": "coverage_dual_v1", "pass_threshold": 10},
        )
        self.assertFalse(partial.ok)
        self.assertEqual((partial.score, partial.max_score), (9, 10))

    def test_bounded_ci_replan_grader_scores_two_failure_scenarios(self) -> None:
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "bounded_ci_replan.json").read_text(
                encoding="utf-8"
            )
        )
        grader = {
            "kind": "bounded_ci_replan",
            "test_suite": "bounded_ci_replan_v1",
            "pass_threshold": 10,
        }

        result = grade_answer(json.dumps(payload), grader)

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))
        self.assertEqual(len(result.diagnostics["score_details"]), 10)

        payload["scenarios"]["beta"]["fallback"]["score"] -= 1
        partial = grade_answer(json.dumps(payload), grader)
        self.assertFalse(partial.ok)
        self.assertEqual((partial.score, partial.max_score), (9, 10))
        self.assertEqual(partial.diagnostics["failed_components"], ["beta.fallback"])

        invalid = grade_answer("not json", grader)
        self.assertEqual((invalid.score, invalid.max_score), (0, 10))
        self.assertEqual(invalid.diagnostics["status"], "invalid_json")

    def test_ci_optimality_certificate_scores_ten_independent_certificates(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "ci_optimality_certificate.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        grader = {
            "kind": "ci_optimality_certificate",
            "test_suite": "ci_optimality_certificate_v1",
            "pass_threshold": 10,
        }

        reference = grade_answer(json.dumps(payload), grader)

        self.assertTrue(reference.ok)
        self.assertEqual((reference.score, reference.max_score), (10, 10))
        self.assertEqual(len(reference.diagnostics["score_details"]), 10)

        comparison_error = json.loads(fixture.read_text(encoding="utf-8"))
        comparison_error["comparisons"]["D"]["a_value"] = 8
        partial = grade_answer(json.dumps(comparison_error), grader)
        self.assertFalse(partial.ok)
        self.assertEqual((partial.score, partial.max_score), (9, 10))
        self.assertEqual(partial.diagnostics["failed_components"], ["comparison.D"])

        counterfactual_error = json.loads(fixture.read_text(encoding="utf-8"))
        counterfactual_error["counterfactuals"]["private_b_public_c"]["runner_up"] = "A"
        partial = grade_answer(json.dumps(counterfactual_error), grader)
        self.assertFalse(partial.ok)
        self.assertEqual((partial.score, partial.max_score), (9, 10))
        self.assertEqual(
            partial.diagnostics["failed_components"],
            ["counterfactual.private_b_public_c"],
        )

        invalid = grade_answer("not json", grader)
        self.assertEqual((invalid.score, invalid.max_score), (0, 10))
        self.assertEqual(invalid.diagnostics["status"], "invalid_json")

    def test_ci_adversarial_audit_scores_hidden_implementations(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "ci_adversarial_audit.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        grader = {
            "kind": "ci_adversarial_audit",
            "test_suite": "ci_adversarial_audit_v1",
            "pass_threshold": 10,
        }

        reference = grade_answer(json.dumps(payload), grader)

        self.assertTrue(reference.ok)
        self.assertEqual((reference.score, reference.max_score), (10, 10))
        self.assertEqual(reference.diagnostics["semantic_passed"], 10)
        self.assertEqual(len(reference.diagnostics["score_details"]), 10)

        first_only = grade_answer(json.dumps({"scenarios": payload["scenarios"][:1]}), grader)
        second_only = grade_answer(json.dumps({"scenarios": payload["scenarios"][1:]}), grader)
        self.assertEqual((first_only.score, second_only.score), (7, 3))

        invalid_json = grade_answer("not json", grader)
        invalid_schema = grade_answer(json.dumps({"scenarios": []}), grader)
        self.assertEqual((invalid_json.score, invalid_schema.score), (0, 0))
        self.assertEqual(invalid_json.diagnostics["status"], "invalid_json")
        self.assertEqual(invalid_schema.diagnostics["status"], "invalid_schema")

    def test_ci_adversarial_audit_tolerates_json_fences_and_repeated_references(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "ci_adversarial_audit.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        first = payload["scenarios"][0]
        first["modules"][2]["deps"].append(first["modules"][2]["deps"][0])
        first["jobs"][0]["covers"].append(first["jobs"][0]["covers"][0])
        first["jobs"][1]["requires"].append(first["jobs"][1]["requires"][0])
        grader = {
            "kind": "ci_adversarial_audit",
            "test_suite": "ci_adversarial_audit_v1",
            "pass_threshold": 10,
        }

        result = grade_answer(
            "```json\n" + json.dumps(payload) + "\n```",
            grader,
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))

    def test_ci_adversarial_audit_accepts_prompt_legal_identifiers(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "ci_adversarial_audit.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        scenario = payload["scenarios"][0]
        scenario["name"] = "audit/core.api/" + ("场景" * 30)
        old_id = scenario["modules"][0]["id"]
        new_id = "core.api/" + ("模块" * 30)
        scenario["modules"][0]["id"] = new_id
        scenario["changes"][new_id] = scenario["changes"].pop(old_id)
        for module in scenario["modules"]:
            module["deps"] = [new_id if item == old_id else item for item in module["deps"]]
        for job in scenario["jobs"]:
            job["covers"] = [new_id if item == old_id else item for item in job["covers"]]

        result = grade_answer(
            json.dumps(payload),
            {
                "kind": "ci_adversarial_audit",
                "test_suite": "ci_adversarial_audit_v1",
                "pass_threshold": 10,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))

    def test_scalar_cross_loop_flight_patch_scores_ten_contracts(self) -> None:
        patch_text = (
            Path(__file__).parent / "fixtures" / "scalar_cross_loop_flight_reference.patch"
        ).read_text(encoding="utf-8")
        grader = {
            "kind": "scalar_cross_loop_flight_patch",
            "test_suite": "scalar_cross_loop_flight_v1",
            "timeout_seconds": 60,
            "pass_threshold": 10,
        }

        result = grade_answer(patch_text, grader)

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))
        self.assertEqual(result.diagnostics["semantic_passed"], 10)
        self.assertEqual(len(result.diagnostics["score_details"]), 10)

        invalid = grade_answer("not a patch", grader)
        self.assertFalse(invalid.ok)
        self.assertEqual((invalid.score, invalid.max_score), (0, 10))
        self.assertEqual(invalid.diagnostics["status"], "patch_apply_failed")

    def test_transaction_regression_design_scores_hidden_state_machine_mutants(self) -> None:
        answer = (
            Path(__file__).parent / "fixtures" / "transaction_regression_design.json"
        ).read_text(encoding="utf-8")
        grader = {
            "kind": "transaction_regression_design",
            "test_suite": "transaction_replay_mutants_v1",
            "pass_threshold": 10,
        }

        result = grade_answer(answer, grader)

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))
        self.assertEqual(result.diagnostics["semantic_passed"], 10)
        self.assertEqual(len(result.diagnostics["score_details"]), 10)
        self.assertEqual(result.diagnostics["survived_mutants"], [])

        simple = grade_answer(
            json.dumps(
                {
                    "tests": [
                        {
                            "name": "one_absent_delete",
                            "frames": [
                                {
                                    "id": "A",
                                    "after": [],
                                    "ops": [{"op": "delete", "key": "x"}],
                                }
                            ],
                        }
                    ]
                }
            ),
            grader,
        )
        self.assertFalse(simple.ok)
        self.assertEqual((simple.score, simple.max_score), (1, 10))
        self.assertEqual(
            simple.diagnostics["killed_mutants"],
            ["absent_delete_keeps_zero_version"],
        )

        invalid = grade_answer('{"tests": []}', grader)
        self.assertFalse(invalid.ok)
        self.assertEqual((invalid.score, invalid.max_score), (0, 10))
        self.assertEqual(invalid.diagnostics["status"], "invalid_test_cases")

    def test_transaction_regression_design_accepts_long_prompt_legal_name(self) -> None:
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "transaction_regression_design.json").read_text(
                encoding="utf-8"
            )
        )
        payload["tests"][0]["name"] = "transaction/" + ("回归" * 40)

        result = grade_answer(
            json.dumps(payload),
            {
                "kind": "transaction_regression_design",
                "test_suite": "transaction_replay_mutants_v1",
                "pass_threshold": 10,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (10, 10))

    def test_expression_24_grader_accepts_a_valid_expression(self) -> None:
        result = grade_answer(
            "8*(2*5-7)",
            {
                "kind": "expression_24",
                "numbers": [2, 5, 7, 8],
                "target": 24,
                "allowed_operators": ["+", "-", "*", "/"],
            },
        )
        self.assertTrue(result.ok)

    def test_expression_24_grader_rejects_digit_concatenation(self) -> None:
        result = grade_answer(
            "8*(25-7)",
            {
                "kind": "expression_24",
                "numbers": [2, 5, 7, 8],
                "target": 24,
                "allowed_operators": ["+", "-", "*", "/"],
            },
        )
        self.assertFalse(result.ok)

    def test_micro_unified_diff_scores_hidden_cases(self) -> None:
        result = grade_answer(
            CORRECT_UNIFIED_DIFF_SOLUTION,
            {
                "kind": "python_function",
                "function_name": "apply_unified_diff",
                "test_suite": "micro_unified_diff",
                "timeout_seconds": 3,
                "pass_threshold": 16,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, 20)
        self.assertEqual(result.max_score, 20)
        self.assertIn("micro_unified_diff 20/20", result.summary)

    def test_candidate_source_rejects_file_network_and_reflection_escape_hatches(self) -> None:
        for source in (
            "import socket\n",
            "open('outside.txt')\n",
            "object.__subclasses__()\n",
            "factory.__globals__\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    graders._validate_python_source(source)

    def test_candidate_grader_fails_closed_when_sandbox_unavailable(self) -> None:
        with mock.patch.object(
            graders,
            "run_sandboxed_candidate_worker",
            return_value=(None, "sandbox_unavailable"),
        ):
            result = grade_answer(
                "def apply_unified_diff(files, diff_text):\n    return files\n",
                {
                    "kind": "python_function",
                    "function_name": "apply_unified_diff",
                    "test_suite": "micro_unified_diff",
                    "timeout_seconds": 3,
                },
            )

        self.assertFalse(result.ok)
        self.assertIsNone(result.score)
        self.assertEqual(result.diagnostics["status"], "grader_unavailable")
        self.assertEqual(result.diagnostics["failure_summary"], "sandbox_unavailable")

    def test_candidate_sandbox_launch_failure_is_unscored(self) -> None:
        with mock.patch.object(candidate_sandbox, "SANDBOX_EXECUTABLE", "/missing/sandbox-exec"):
            payload, status = candidate_sandbox.run_sandboxed_candidate_worker(
                "python_function",
                {
                    "source": "def apply_unified_diff(files, diff_text):\n    return files\n",
                    "function_name": "apply_unified_diff",
                    "test_suite": "micro_unified_diff",
                },
                1,
            )

        self.assertIsNone(payload)
        self.assertTrue(status.startswith("sandbox_unavailable"))

    def test_candidate_sandbox_output_budget_fails_closed(self) -> None:
        with (
            mock.patch.object(candidate_sandbox, "SANDBOX_EXECUTABLE", "/sandbox-exec"),
            mock.patch.object(
                candidate_sandbox,
                "run_bounded_process",
                side_effect=BoundedSubprocessOutputError(
                    ["worker"],
                    output_limit_bytes=128,
                    total_output_bytes=129,
                ),
            ),
        ):
            payload, status = candidate_sandbox.run_sandboxed_candidate_worker(
                "python_function",
                {},
                1,
            )

        self.assertIsNone(payload)
        self.assertEqual(status, "sandbox_unavailable:output_limit_exceeded")

    def test_black_box_output_budget_fails_closed(self) -> None:
        with (
            mock.patch.object(
                black_box_regression_grader,
                "SANDBOX_EXECUTABLE",
                "/sandbox-exec",
            ),
            mock.patch.object(
                black_box_regression_grader,
                "run_bounded_process",
                side_effect=BoundedSubprocessOutputError(
                    ["worker"],
                    output_limit_bytes=128,
                    total_output_bytes=129,
                ),
            ),
        ):
            passed, detail = black_box_regression_grader._run_submitted_tests(
                black_box_regression_grader._reference_source(),
                "import unittest\n",
                ["test_session_store.T.test_one"],
            )

        self.assertFalse(passed)
        self.assertEqual(detail, "sandbox_unavailable:output_limit_exceeded")

    def test_frozen_candidate_forwards_only_valid_backend_scanner_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-backend-") as backend_temp:
            backend = Path(backend_temp)
            scanner = backend / "scanner"
            scanner.mkdir()
            (scanner / "pricing_snapshot.json").write_text(
                (candidate_sandbox.SCANNER_ROOT / "pricing_snapshot.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def fake_profile(
                root: Path,
                *,
                read_roots: tuple[Path, ...],
                allow_process_fork: bool,
            ) -> str:
                captured["read_roots"] = read_roots
                captured["allow_process_fork"] = allow_process_fork
                return "(version 1)(deny default)"

            def fake_run(*args: object, **kwargs: object) -> mock.Mock:
                captured["command"] = args[0]
                captured["environment"] = dict(kwargs["env"])
                output = kwargs["stdout"]
                output.write(
                    (
                        candidate_sandbox.RESULT_MARKER
                        + '{"score":1,"max_score":1}'
                    ).encode("utf-8")
                )
                output.flush()
                return mock.Mock(returncode=0)

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "MODELDIAL_BACKEND_ROOT": str(backend),
                        "MODELDIAL_DATA_DIR": "/must-not-pass",
                        "MODELDIAL_UNRELATED": "must-not-pass",
                    },
                    clear=True,
                ),
                mock.patch.object(candidate_sandbox, "is_frozen_runtime", return_value=True),
                mock.patch.object(candidate_sandbox, "SANDBOX_EXECUTABLE", "/sandbox-exec"),
                mock.patch.object(candidate_sandbox, "_sandbox_profile", side_effect=fake_profile),
                mock.patch.object(candidate_sandbox.subprocess, "run", side_effect=fake_run),
            ):
                payload, status = candidate_sandbox.run_sandboxed_candidate_worker(
                    "python_function",
                    {},
                    1,
                )

        self.assertEqual(status, "ok")
        self.assertEqual(payload, {"score": 1, "max_score": 1})
        read_roots = captured["read_roots"]
        self.assertEqual(
            set(read_roots),
            {candidate_sandbox.SCANNER_ROOT, scanner.resolve()},
        )
        self.assertNotIn(backend, read_roots)
        environment = captured["environment"]
        self.assertEqual(environment["MODELDIAL_BACKEND_ROOT"], str(backend.resolve()))
        self.assertNotIn("MODELDIAL_DATA_DIR", environment)
        self.assertNotIn("MODELDIAL_UNRELATED", environment)

    def test_frozen_candidate_missing_or_invalid_backend_root_fails_closed(self) -> None:
        for configured in (None, "/missing/modeldial-backend"):
            with self.subTest(configured=configured):
                values = {} if configured is None else {"MODELDIAL_BACKEND_ROOT": configured}
                with (
                    mock.patch.dict(os.environ, values, clear=True),
                    mock.patch.object(candidate_sandbox, "is_frozen_runtime", return_value=True),
                    mock.patch.object(candidate_sandbox, "SANDBOX_EXECUTABLE", "/sandbox-exec"),
                ):
                    payload, status = candidate_sandbox.run_sandboxed_candidate_worker(
                        "python_function",
                        {},
                        1,
                    )

                self.assertIsNone(payload)
                self.assertTrue(status.startswith("sandbox_unavailable:backend_root_"))

    def test_frozen_candidate_rejects_backend_scanner_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-backend-escape-") as temp:
            root = Path(temp)
            backend = root / "Backend"
            outside_scanner = root / "outside" / "scanner"
            backend.mkdir()
            outside_scanner.mkdir(parents=True)
            (outside_scanner / "pricing_snapshot.json").write_text("{}", encoding="utf-8")
            try:
                (backend / "scanner").symlink_to(outside_scanner, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            with (
                mock.patch.dict(
                    os.environ,
                    {"MODELDIAL_BACKEND_ROOT": str(backend)},
                    clear=True,
                ),
                mock.patch.object(candidate_sandbox, "is_frozen_runtime", return_value=True),
            ):
                backend_root, status = candidate_sandbox._validated_backend_root()

        self.assertIsNone(backend_root)
        self.assertEqual(status, "sandbox_unavailable:backend_root_invalid")

    def test_candidate_sandbox_denies_outside_files_and_network(self) -> None:
        if candidate_sandbox.SANDBOX_EXECUTABLE is None:
            self.skipTest("sandbox-exec is unavailable on this platform")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as secret:
            secret.write("must stay outside the candidate sandbox")
            secret.flush()
            with tempfile.TemporaryDirectory(prefix="candidate-sandbox-test-") as temp:
                root = Path(temp)
                (root / "scratch").mkdir()
                code = (
                    "from pathlib import Path\n"
                    "import socket\n"
                    f"file_blocked = False\n"
                    f"try:\n    Path({secret.name!r}).read_text()\n"
                    "except PermissionError:\n    file_blocked = True\n"
                    "network_blocked = False\n"
                    "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=0.1)\n"
                    "except PermissionError:\n    network_blocked = True\n"
                    "except OSError:\n    pass\n"
                    "raise SystemExit(0 if file_blocked and network_blocked else 1)\n"
                )
                command = [
                    candidate_sandbox.SANDBOX_EXECUTABLE,
                    "-p",
                    black_box_regression_grader._sandbox_profile(
                        root,
                        read_roots=(candidate_sandbox.SCANNER_ROOT,),
                    ),
                    str(Path(sys.executable).resolve()),
                    "-c",
                    code,
                ]
                environment = black_box_regression_grader._sandbox_environment(root)
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_candidate_profile_only_allows_process_fork_for_cross_loop_workers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-sandbox-profile-") as temp:
            root = Path(temp)
            default_profile = black_box_regression_grader._sandbox_profile(root)
            cross_loop_profile = black_box_regression_grader._sandbox_profile(
                root,
                read_roots=(candidate_sandbox.SCANNER_ROOT,),
                allow_process_fork=True,
            )

        self.assertNotIn("(allow process-fork)", default_profile)
        self.assertIn("(allow process-fork)", cross_loop_profile)

    def test_micro_unified_diff_uses_fixed_challenge_case_mix(self) -> None:
        self.assertEqual(
            [str(case["name"]) for case in _micro_unified_diff_cases()],
            [
                "single_line_replace",
                "multiple_hunks_same_file",
                "new_file",
                "delete_file",
                "empty_new_file",
                "new_file_already_exists_must_fail",
                "delete_file_then_recreate_same_path",
                "zero_old_count_insert_middle",
                "hunk_after_prior_insertion_offset",
                "hunk_after_prior_deletion_offset",
                "hunk_count_mismatch_must_fail",
                "new_count_mismatch_must_fail",
                "header_count_zero_delete_at_start",
                "same_file_multiple_sections_sequential",
                "repeated_line_position",
                "overlapping_hunks_must_fail",
                "out_of_order_hunks_must_fail",
                "file_section_header_mismatch_must_fail",
                "delete_file_with_addition_must_fail",
                "atomic_failure_after_create",
            ],
        )

    def test_micro_unified_diff_keeps_partial_score_for_bad_solution(self) -> None:
        result = grade_answer(
            "def apply_unified_diff(files, diff_text):\n    return dict(files)\n",
            {
                "kind": "python_function",
                "function_name": "apply_unified_diff",
                "test_suite": "micro_unified_diff",
                "timeout_seconds": 3,
                "pass_threshold": 16,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.max_score, 20)
        self.assertIn("micro_unified_diff", result.summary)

    def test_micro_unified_diff_reports_failure_labels_and_categories(self) -> None:
        result = grade_answer(
            "def apply_unified_diff(files, diff_text):\n    return dict(files)\n",
            {
                "kind": "python_function",
                "function_name": "apply_unified_diff",
                "test_suite": "micro_unified_diff",
                "timeout_seconds": 3,
                "pass_threshold": 16,
            },
        )

        self.assertFalse(result.ok)
        details = result.failure_details or []
        self.assertIn(
            {
                "case_id": "zero_old_count_insert_middle",
                "label": "零行插入",
                "category": "hunk_semantics",
                "category_label": "hunk 边界",
            },
            details,
        )

    def test_micro_repo_cache_patch_scores_hidden_cases(self) -> None:
        result = grade_answer(
            _correct_cache_runner_search_replace_patch(),
            {
                "kind": "search_replace_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, 12)
        self.assertEqual(result.max_score, 12)
        self.assertIn("micro_repo_cache_patch 12/12", result.summary)

    def test_micro_repo_cache_patch_uses_fixed_case_mix(self) -> None:
        self.assertEqual(
            [str(case["name"]) for case in _micro_repo_cache_patch_cases()],
            [
                "hit_and_not_cached",
                "profile_changed",
                "options_changed",
                "corrupted_beats_file_changed",
                "force_rescan_beats_everything",
                "force_rescan_with_warm_cache_reports_nothing",
                "warm_cache_scans_but_reports_nothing",
                "warm_cache_updates_then_next_run_hits",
                "corrupted_entry_repaired_then_next_run_hits",
                "hit_uses_cached_issue_count",
                "reported_issues_sorted_and_excludes_zero",
                "no_input_mutation_and_unlisted_preserved",
            ],
        )

    def test_micro_repo_cache_patch_is_two_file_repair_task(self) -> None:
        initial_files = getattr(graders, "_MICRO_REPO_CACHE_PATCH_INITIAL_FILES", {})

        self.assertIn("cache_runner.py", initial_files)
        self.assertIn("cache_policy.py", initial_files)
        self.assertIn("from cache_policy import", initial_files["cache_runner.py"])
        self.assertIn("def choose_invalidation_reason", initial_files["cache_policy.py"])

    def test_micro_repo_cache_patch_reports_failure_details(self) -> None:
        result = grade_answer(
            "",
            {
                "kind": "unified_diff_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.max_score, 12)
        self.assertIn("micro_repo_cache_patch", result.summary)
        self.assertTrue(result.failure_details)

    def test_micro_repo_cache_patch_reports_patch_apply_diagnostics(self) -> None:
        result = grade_answer(
            "--- a/cache_runner.py\n+++ b/cache_runner.py\n@@ -1,1 +1,1 @@\n-bad\n+bad\n",
            {
                "kind": "unified_diff_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            {
                "patch_applies": False,
                "semantic_passed": 0,
                "semantic_total": 12,
                "status": "patch_apply_failed",
            },
            {
                key: result.diagnostics[key]
                for key in (
                    "patch_applies",
                    "semantic_passed",
                    "semantic_total",
                    "status",
                )
            },
        )
        self.assertTrue(str(result.diagnostics["failure_summary"]))

    def test_micro_repo_cache_patch_reports_semantic_diagnostics(self) -> None:
        result = grade_answer(
            _cache_runner_search_replace_patch_without_options_check(),
            {
                "kind": "search_replace_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertEqual(True, result.diagnostics["patch_applies"])
        self.assertEqual("semantic_failed", result.diagnostics["status"])
        self.assertLess(result.diagnostics["semantic_passed"], 12)
        self.assertIn("options_changed", result.diagnostics["failed_cases"])

    def test_search_replace_cache_patch_scores_hidden_cases(self) -> None:
        result = grade_answer(
            _correct_cache_runner_search_replace_patch(),
            {
                "kind": "search_replace_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, 12)
        self.assertEqual(result.max_score, 12)
        self.assertEqual(
            {
                "patch_format_ok": True,
                "patch_applies": True,
                "semantic_passed": 12,
                "semantic_total": 12,
                "status": "passed",
            },
            {
                key: result.diagnostics[key]
                for key in (
                    "patch_format_ok",
                    "patch_applies",
                    "semantic_passed",
                    "semantic_total",
                    "status",
                )
            },
        )

    def test_search_replace_cache_patch_reports_apply_failure_separately(self) -> None:
        result = grade_answer(
            "*** Begin Patch\n"
            "*** Update File: cache_runner.py\n"
            "<<<<<<< SEARCH\n"
            "not present\n"
            "=======\n"
            "replacement\n"
            ">>>>>>> REPLACE\n"
            "*** End Patch\n",
            {
                "kind": "search_replace_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual("patch_apply_failed", result.diagnostics["status"])
        self.assertEqual(True, result.diagnostics["patch_format_ok"])
        self.assertEqual(False, result.diagnostics["patch_applies"])
        self.assertEqual(0, result.diagnostics["semantic_passed"])

    def test_search_replace_cache_patch_reports_semantic_failure(self) -> None:
        result = grade_answer(
            _cache_runner_search_replace_patch_without_options_check(),
            {
                "kind": "search_replace_patch",
                "test_suite": "micro_repo_cache_patch",
                "timeout_seconds": 3,
                "pass_threshold": 8,
            },
        )

        self.assertEqual("semantic_failed", result.diagnostics["status"])
        self.assertEqual(True, result.diagnostics["patch_format_ok"])
        self.assertEqual(True, result.diagnostics["patch_applies"])
        self.assertIn("options_changed", result.diagnostics["failed_cases"])

    def test_mutation_cache_tests_kill_hidden_mutants(self) -> None:
        answer = json.dumps(
            {
                "tests": [
                    {
                        "name": "identity_priority_and_preservation",
                        "files": [
                            {"path": "a.py", "content_hash": "ha", "issue_count": 4},
                            {"path": "b.py", "content_hash": "hb", "issue_count": 0},
                            {"path": "c.py", "content_hash": "hc", "issue_count": 6},
                            {"path": "d.py", "content_hash": "hd", "issue_count": 5},
                            {"path": "e.py", "content_hash": "he", "issue_count": 99},
                        ],
                        "cache": {
                            "a.py": _base_entry_for_mutation(content_hash="old", config_hash="old", corrupted=True),
                            "b.py": _base_entry_for_mutation(path="shadow.py", content_hash="hb", issue_count=5),
                            "c.py": _base_entry_for_mutation(path="c.py", content_hash="hc", profile_hash="old"),
                            "d.py": _base_entry_for_mutation(
                                path="d.py",
                                content_hash="hd",
                                options_key="old",
                                stored_day=0,
                            ),
                            "e.py": _base_entry_for_mutation(
                                path="e.py",
                                content_hash="he",
                                issue_count=2,
                            ),
                            "unlisted.py": _base_entry_for_mutation(
                                path="unlisted.py",
                                content_hash="hu",
                                stored_day=0,
                            ),
                        },
                        "params": _base_params_for_mutation(),
                    },
                    {
                        "name": "force_warm_refresh_priority",
                        "files": [
                            {"path": "a.py", "content_hash": "ha2", "issue_count": 7},
                        ],
                        "cache": {
                            "a.py": _base_entry_for_mutation(
                                content_hash="ha1",
                                profile_hash="p1",
                                issue_count=2,
                                corrupted=True,
                            ),
                        },
                        "params": _base_params_for_mutation(
                            profile_hash="p2",
                            force_rescan=True,
                            warm_cache=True,
                        ),
                    },
                    {
                        "name": "warm_hit_reporting",
                        "files": [
                            {"path": "a.py", "content_hash": "ha", "issue_count": 9},
                        ],
                        "cache": {
                            "a.py": _base_entry_for_mutation(issue_count=2),
                        },
                        "params": _base_params_for_mutation(warm_cache=True),
                    },
                ]
            }
        )

        result = grade_answer(
            answer,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "timeout_seconds": 3,
                "pass_threshold": 7,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, 10)
        self.assertEqual(result.max_score, 10)
        self.assertEqual("passed", result.diagnostics["status"])
        self.assertIn("cache_regression_mutants 10/10", result.summary)

    def test_mutation_cache_tests_reports_surviving_mutants(self) -> None:
        answer = json.dumps(
            {
                "tests": [
                    {
                        "name": "only_not_cached",
                        "files": [{"path": "a.py", "content_hash": "ha", "issue_count": 1}],
                        "cache": {},
                        "params": _base_params_for_mutation(),
                    }
                ]
            }
        )

        result = grade_answer(
            answer,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "timeout_seconds": 3,
                "pass_threshold": 7,
            },
        )

        self.assertFalse(result.ok)
        self.assertLess(result.score, 10)
        self.assertEqual("semantic_failed", result.diagnostics["status"])
        self.assertTrue(result.failure_details)

    def test_mutation_cache_literal_examples_do_not_create_six_point_floor(self) -> None:
        answer = json.dumps(
            {
                "tests": [
                    {
                        "name": "force_hit",
                        "files": [{"path": "a.py", "content_hash": "ha", "issue_count": 9}],
                        "cache": {"a.py": _base_entry_for_mutation(issue_count=2)},
                        "params": _base_params_for_mutation(force_rescan=True),
                    },
                    {
                        "name": "warm_miss",
                        "files": [{"path": "b.py", "content_hash": "hb", "issue_count": 4}],
                        "cache": {},
                        "params": _base_params_for_mutation(warm_cache=True),
                    },
                    {
                        "name": "hit_and_preserve",
                        "files": [{"path": "a.py", "content_hash": "ha", "issue_count": 9}],
                        "cache": {
                            "a.py": _base_entry_for_mutation(issue_count=2),
                            "unlisted.py": _base_entry_for_mutation(
                                path="unlisted.py",
                                content_hash="hu",
                            ),
                        },
                        "params": _base_params_for_mutation(),
                    },
                ]
            }
        )

        result = grade_answer(
            answer,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "timeout_seconds": 3,
                "pass_threshold": 7,
            },
        )

        self.assertEqual(result.score, 1)
        self.assertEqual(result.max_score, 10)

    def test_mutation_cache_test_case_mix_is_fixed(self) -> None:
        self.assertEqual(
            [mutant["id"] for mutant in _mutation_cache_test_mutants()],
            [
                "priority_config_before_corrupted",
                "entry_path_mismatch_ignored",
                "profile_hash_change_ignored",
                "options_change_ignored",
                "priority_expiry_before_options",
                "priority_corrupted_before_force",
                "force_warm_keeps_stale_profile_hash",
                "warm_cache_reports_hits",
                "hit_uses_current_issue_count",
                "purges_expired_unlisted_entries",
            ],
        )

    def test_mutation_cache_v2_rebalances_dead_and_free_items(self) -> None:
        self.assertEqual(
            [
                mutant["id"]
                for mutant in _mutation_cache_test_mutants("cache_regression_mutants_v2")
            ],
            [
                "priority_config_before_corrupted",
                "entry_path_mismatch_ignored",
                "profile_hash_change_ignored",
                "options_change_ignored",
                "expiry_boundary_inclusive",
                "priority_corrupted_before_force",
                "expiry_zero_never_expires",
                "warm_cache_reports_hits",
                "profile_name_change_ignored",
                "purges_expired_unlisted_entries",
            ],
        )

    def test_mutation_cache_v2_reference_cases_kill_all_mutants(self) -> None:
        answer = json.dumps(
            {
                "tests": [
                    {
                        "name": "identity_priority_and_boundary",
                        "files": [
                            {"path": "a.py", "content_hash": "ha", "issue_count": 4},
                            {"path": "b.py", "content_hash": "hb", "issue_count": 0},
                            {"path": "c.py", "content_hash": "hc", "issue_count": 6},
                            {"path": "d.py", "content_hash": "hd", "issue_count": 5},
                            {"path": "e.py", "content_hash": "he", "issue_count": 9},
                            {"path": "f.py", "content_hash": "hf", "issue_count": 3},
                        ],
                        "cache": {
                            "a.py": _base_entry_for_mutation(
                                content_hash="old",
                                config_hash="old",
                                corrupted=True,
                            ),
                            "b.py": _base_entry_for_mutation(path="shadow.py", content_hash="hb"),
                            "c.py": _base_entry_for_mutation(
                                path="c.py",
                                content_hash="hc",
                                profile_hash="old",
                            ),
                            "d.py": _base_entry_for_mutation(
                                path="d.py",
                                content_hash="hd",
                                options_key="old",
                                issue_count=1,
                            ),
                            "e.py": _base_entry_for_mutation(
                                path="e.py",
                                content_hash="he",
                                stored_day=5,
                                issue_count=2,
                            ),
                            "f.py": _base_entry_for_mutation(
                                path="f.py",
                                content_hash="hf",
                                profile_name="legacy",
                            ),
                            "unlisted.py": _base_entry_for_mutation(
                                path="unlisted.py",
                                content_hash="hu",
                                stored_day=0,
                            ),
                        },
                        "params": _base_params_for_mutation(warm_cache=True),
                    },
                    {
                        "name": "force_refresh_priority",
                        "files": [
                            {"path": "a.py", "content_hash": "ha2", "issue_count": 7},
                        ],
                        "cache": {
                            "a.py": _base_entry_for_mutation(
                                content_hash="ha1",
                                profile_hash="p1",
                                issue_count=2,
                                corrupted=True,
                            ),
                        },
                        "params": _base_params_for_mutation(
                            profile_hash="p2",
                            force_rescan=True,
                        ),
                    },
                    {
                        "name": "zero_day_expiry",
                        "files": [
                            {"path": "a.py", "content_hash": "ha", "issue_count": 9},
                        ],
                        "cache": {"a.py": _base_entry_for_mutation(issue_count=2)},
                        "params": _base_params_for_mutation(
                            cache_expiry_days=0,
                            warm_cache=True,
                        ),
                    },
                ]
            }
        )

        result = grade_answer(
            answer,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants_v2",
                "timeout_seconds": 3,
                "pass_threshold": 7,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.score, 10)
        self.assertEqual(result.max_score, 10)
        self.assertEqual("passed", result.diagnostics["status"])
        facets = {
            str(item["id"]): item
            for item in graders.mutation_test_design_facets(result.diagnostics)
        }
        self.assertEqual((facets["cache_key"]["passed"], facets["cache_key"]["total"]), (4, 4))
        self.assertEqual((facets["expiry"]["passed"], facets["expiry"]["total"]), (2, 2))

    def test_mutation_cache_tests_accept_at_most_three_cases(self) -> None:
        answer = json.dumps(
            {
                "tests": [
                    {
                        "name": f"case_{index}",
                        "files": [{"path": f"{index}.py", "content_hash": "h", "issue_count": 0}],
                        "cache": {},
                        "params": _base_params_for_mutation(),
                    }
                    for index in range(4)
                ]
            }
        )

        result = grade_answer(
            answer,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "timeout_seconds": 3,
                "pass_threshold": 7,
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual("invalid_test_cases", result.diagnostics["status"])
        self.assertIn("too_many_tests", result.summary)

    def test_mutation_cache_tests_enforce_prompt_limits_and_json_integer_types(self) -> None:
        grader = {
            "kind": "mutation_test_design",
            "test_suite": "cache_regression_mutants_v3",
            "timeout_seconds": 3,
            "pass_threshold": 20,
        }

        def payload() -> dict[str, object]:
            return {
                "tests": [
                    {
                        "name": "bounded",
                        "files": [
                            {"path": "a.py", "content_hash": "ha", "issue_count": 1}
                        ],
                        "cache": {},
                        "params": _base_params_for_mutation(),
                    }
                ]
            }

        too_many_files = payload()
        too_many_files["tests"][0]["files"] = [
            {"path": f"{index}.py", "content_hash": "h", "issue_count": 0}
            for index in range(9)
        ]
        file_result = grade_answer(json.dumps(too_many_files), grader)
        self.assertEqual(file_result.diagnostics["status"], "invalid_test_cases")
        self.assertIn("files_must_have_at_most_8_items", file_result.summary)

        too_many_cache = payload()
        too_many_cache["tests"][0]["cache"] = {
            f"{index}.py": _base_entry_for_mutation(path=f"{index}.py")
            for index in range(11)
        }
        cache_result = grade_answer(json.dumps(too_many_cache), grader)
        self.assertEqual(cache_result.diagnostics["status"], "invalid_test_cases")
        self.assertIn("cache_must_have_at_most_10_entries", cache_result.summary)

        boolean_integer = payload()
        boolean_integer["tests"][0]["files"][0]["issue_count"] = True
        bool_result = grade_answer(json.dumps(boolean_integer), grader)
        self.assertEqual(bool_result.diagnostics["status"], "invalid_test_cases")
        self.assertIn("issue_count_must_be_non_negative_int", bool_result.summary)


if __name__ == "__main__":
    unittest.main()
