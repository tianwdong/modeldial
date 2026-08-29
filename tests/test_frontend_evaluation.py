from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scanner.frontend_evaluation import (
    FRONTEND_JUDGE_EFFORT,
    FRONTEND_JUDGE_MODEL,
    FRONTEND_PACKAGE_ID,
    FRONTEND_VISUAL_JUDGMENT_SCHEMA,
    FrontendEvaluationError,
    apply_automatic_frontend_points,
    build_visual_judge_prompt,
    load_frontend_question,
    merge_visual_judgment,
    normalize_frontend_html,
    request_visual_judgment,
    validate_visual_judgment,
)


QUESTION_ROOT = (
    Path(__file__).resolve().parents[1]
    / "questions"
    / "frontend"
    / "case_stream_explorer_v17"
)
ALIAS = "F0123456789AB"


def _package():
    return load_frontend_question(QUESTION_ROOT)


def _passing_browser_payload(package) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    for dimension in package.contract["dimensions"]:
        for check in dimension["checks"]:
            if check["mode"] != "visual_judge":
                checks[check["id"]] = {
                    "passed": True,
                    "evidence": f"{check['id']} passed",
                }
    return {
        "check_results": checks,
        "diagnostics": {
            "pageErrors": [],
            "consoleErrors": [],
            "externalRequests": [],
        },
        "app_shell_rendered": True,
        "initial_data_rendered": True,
    }


def _evidence_manifest(*, demonstrated: set[str] | None = None) -> dict[str, object]:
    state_ids = [
        "default_desktop",
        "default_tablet",
        "default_mobile",
        "selected_saving",
        "failure",
        "desktop_inspector",
        "mobile_inspector",
    ]
    visible = set(state_ids) if demonstrated is None else demonstrated
    return {
        "schema_version": "frontend_visual_evidence_manifest_v1",
        "states": [
            {
                "id": state_id,
                "filename": f"{state_id}.png",
                "demonstrated": state_id in visible,
            }
            for state_id in state_ids
        ],
        "contact_sheet": {
            "filename": "contact-sheet.png",
            "sha256": "sha256:" + "a" * 64,
        },
    }


def _visual_judgment(
    *,
    alias: str = ALIAS,
    points: tuple[int, int, int, int] = (4, 4, 4, 3),
) -> dict[str, object]:
    ids = ("V03", "V04", "V05", "V06")
    return {
        "schema_version": FRONTEND_VISUAL_JUDGMENT_SCHEMA,
        "candidate_alias": alias,
        "checks": [
            {
                "id": check_id,
                "points": score,
                "evidence": f"Visible evidence for {check_id}.",
            }
            for check_id, score in zip(ids, points, strict=True)
        ],
        "total": sum(points),
        "summary": "The anonymous candidate is visually complete.",
    }


class FrontendEvaluationTests(unittest.TestCase):
    def test_v17_package_freezes_score_and_judge_contract(self) -> None:
        package = _package()

        self.assertEqual(package.contract["candidate_id"], FRONTEND_PACKAGE_ID)
        self.assertEqual(package.contract["total_points"], 100)
        self.assertEqual(package.contract["automated_points"], 85)
        self.assertEqual(package.contract["visual_judge_points"], 15)
        self.assertEqual(package.visual_rubric["judge_model"], FRONTEND_JUDGE_MODEL)
        self.assertEqual(
            package.visual_rubric["judge_reasoning_effort"],
            FRONTEND_JUDGE_EFFORT,
        )
        self.assertEqual(FRONTEND_JUDGE_MODEL, "gpt-5.6-sol")
        self.assertEqual(FRONTEND_JUDGE_EFFORT, "max")
        self.assertNotIn("{{STARTER_HTML}}", package.prompt)
        self.assertIn("<!doctype html>", package.prompt.lower())

    def test_v17_browser_scorer_uses_prompt_contract_not_hidden_dom_hooks(self) -> None:
        source = (_package().browser_score_script).read_text(encoding="utf-8")

        self.assertIn('getAttribute("aria-posinset")', source)
        self.assertIn("[data-open='${id}']", source)
        self.assertIn('filter({hasText:/^P[0-3]$/})', source)
        self.assertNotIn('after.focused===before.id', source)
        self.assertNotIn('getAttribute("data-saving")==="true"', source)

    def test_normalize_frontend_html_recovers_common_response_wrappers(self) -> None:
        fenced, fenced_format = normalize_frontend_html(
            "```html\n<!doctype html><html><body>ok</body></html>\n```"
        )
        prefixed, prefixed_format = normalize_frontend_html(
            "Here is the result:\n<html><body>ok</body></html> trailing"
        )
        invalid, invalid_format = normalize_frontend_html("plain answer")

        self.assertEqual(fenced_format, "html_fence_recovered")
        self.assertTrue(fenced.startswith("<!doctype html>"))
        self.assertEqual(prefixed_format, "html_prefix_recovered")
        self.assertEqual(prefixed.strip(), "<html><body>ok</body></html>")
        self.assertEqual(invalid_format, "invalid_html_wrapped")
        self.assertIn("<pre>plain answer</pre>", invalid)

    def test_automatic_score_stays_visual_pending_until_independent_judgment(self) -> None:
        package = _package()

        score = apply_automatic_frontend_points(
            _passing_browser_payload(package),
            package.contract,
        )

        self.assertEqual(score["status"], "visual_pending")
        self.assertEqual(score["validity_state"], "qualified")
        self.assertEqual(score["automatic_score"], 85)
        self.assertEqual(score["automatic_max_score"], 85)
        self.assertIsNone(score["visual_score"])
        self.assertIsNone(score["ranking_score"])
        self.assertIsNone(score["total_score"])

    def test_complete_visual_judgment_merges_to_separate_frontend_score(self) -> None:
        package = _package()
        automatic = apply_automatic_frontend_points(
            _passing_browser_payload(package),
            package.contract,
        )

        merged = merge_visual_judgment(
            automatic,
            _visual_judgment(),
            candidate_alias=ALIAS,
            contract=package.contract,
            rubric=package.visual_rubric,
            evidence_manifest=_evidence_manifest(),
        )

        self.assertEqual(merged["status"], "complete")
        self.assertEqual(merged["automatic_score"], 85)
        self.assertEqual(merged["visual_score"], 15)
        self.assertEqual(merged["diagnostic_score"], 100)
        self.assertEqual(merged["ranking_score"], 100)
        self.assertEqual(merged["total_score"], 100)
        self.assertEqual(
            merged["visual_judge"],
            {
                "model_id": "gpt-5.6-sol",
                "reasoning_effort": "max",
                "rubric_schema_version": "frontend_visual_rubric_v2",
            },
        )

    def test_invalid_output_retains_diagnostic_score_but_ranks_zero(self) -> None:
        package = _package()
        payload = _passing_browser_payload(package)
        payload["app_shell_rendered"] = False
        automatic = apply_automatic_frontend_points(payload, package.contract)

        merged = merge_visual_judgment(
            automatic,
            _visual_judgment(),
            candidate_alias=ALIAS,
            contract=package.contract,
            rubric=package.visual_rubric,
            evidence_manifest=_evidence_manifest(),
        )

        self.assertEqual(merged["validity_state"], "invalid")
        self.assertEqual(merged["diagnostic_score"], 100)
        self.assertEqual(merged["ranking_score"], 0)
        self.assertEqual(merged["total_score"], 0)

    def test_visual_evidence_caps_reject_unsupported_high_scores(self) -> None:
        package = _package()
        manifest = _evidence_manifest(
            demonstrated={
                "default_desktop",
                "default_tablet",
                "default_mobile",
            }
        )

        with self.assertRaisesRegex(
            FrontendEvaluationError,
            "evidence cap for V04",
        ):
            validate_visual_judgment(
                _visual_judgment(),
                candidate_alias=ALIAS,
                rubric=package.visual_rubric,
                evidence_manifest=manifest,
            )

        capped = validate_visual_judgment(
            _visual_judgment(points=(4, 3, 4, 2)),
            candidate_alias=ALIAS,
            rubric=package.visual_rubric,
            evidence_manifest=manifest,
        )
        self.assertEqual(capped["total"], 13)

    def test_visual_judgment_rejects_identity_and_total_drift(self) -> None:
        package = _package()
        manifest = _evidence_manifest()

        with self.assertRaisesRegex(FrontendEvaluationError, "alias changed"):
            validate_visual_judgment(
                _visual_judgment(alias="FFFFFFFFFFFFFF"),
                candidate_alias=ALIAS,
                rubric=package.visual_rubric,
                evidence_manifest=manifest,
            )

        wrong_total = deepcopy(_visual_judgment())
        wrong_total["total"] = 14
        with self.assertRaisesRegex(FrontendEvaluationError, "total is invalid"):
            validate_visual_judgment(
                wrong_total,
                candidate_alias=ALIAS,
                rubric=package.visual_rubric,
                evidence_manifest=manifest,
            )

    def test_visual_prompt_does_not_contain_model_or_automatic_score(self) -> None:
        package = _package()
        prompt = build_visual_judge_prompt(
            ALIAS,
            package.visual_rubric,
            _evidence_manifest(),
        )

        self.assertIn(ALIAS, prompt)
        self.assertNotIn("gpt-5.6-sol", prompt)
        self.assertNotIn("automatic_score", prompt)
        self.assertNotIn("85", prompt)

    def test_visual_request_sends_actual_image_to_fixed_sol_max_judge(self) -> None:
        captured: dict[str, object] = {}
        expected = _visual_judgment()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum: int) -> bytes:
                return json.dumps({"output_text": json.dumps(expected)}).encode()

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return Response()

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "contact-sheet.png"
            image.write_bytes(b"not-a-real-image-but-binary-evidence")
            result = request_visual_judgment(
                base_url="https://api.example.test/v1/",
                api_key="secret-value",
                prompt="blind rubric",
                contact_sheet_path=image,
                timeout_seconds=601,
                urlopen=fake_urlopen,
            )

        body = captured["body"]
        assert isinstance(body, dict)
        content = body["input"][0]["content"]
        self.assertEqual(result, expected)
        self.assertEqual(captured["url"], "https://api.example.test/v1/responses")
        self.assertEqual(captured["timeout"], 601)
        self.assertEqual(body["model"], "gpt-5.6-sol")
        self.assertEqual(body["reasoning"], {"effort": "max"})
        self.assertEqual(content[0], {"type": "input_text", "text": "blind rubric"})
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("secret-value", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
