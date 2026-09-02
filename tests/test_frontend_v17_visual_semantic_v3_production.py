from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from devtools import frontend_v17_visual_semantic_v3 as visual_v3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "frontend_v17_visual_semantic_v3"
)
CALIBRATION_ROOT = QUESTION_ROOT / "production-calibration"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _complete_source_score() -> dict[str, object]:
    balanced_contract = visual_v3.balanced.load_contract()
    dimensions = {
        dimension_id: {
            "points": rule["source_max_points"],
            "max_points": rule["source_max_points"],
        }
        for layer_id in ("behavior", "workflow")
        for dimension_id, rule in balanced_contract["layers"][layer_id][
            "dimensions"
        ].items()
    }
    score_details = [
        {"id": check_id, "points": points, "max_points": points}
        for check_id, points in balanced_contract["layers"]["visual"][
            "checks"
        ].items()
    ]
    return {
        "status": "complete",
        "validity_state": "valid",
        "failed_check_ids": [],
        "dimensions": dimensions,
        "score_details": score_details,
    }


class FrontendV17VisualSemanticV3ProductionTests(unittest.TestCase):
    def test_asset_lock_covers_frozen_scoring_dependencies(self) -> None:
        lock = json.loads(
            (QUESTION_ROOT / "asset-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            lock["schema_version"],
            "frontend_v17_visual_semantic_v3_asset_lock_v1",
        )
        self.assertEqual(lock["candidate_id"], visual_v3.CANDIDATE_ID)
        for item in lock["assets"].values():
            self.assertEqual(_sha256(PROJECT_ROOT / item["path"]), item["sha256"])

    def test_frozen_positive_packs_score_reference_without_an_llm(self) -> None:
        screenshots = sorted(CALIBRATION_ROOT.glob("packs/*/*.png"))
        self.assertEqual(len(screenshots), 35)
        contract = visual_v3.load_contract()
        self.assertFalse(contract["feature_config"]["llm_judge"])
        with tempfile.TemporaryDirectory() as temporary:
            source_score = Path(temporary) / "automatic-score.json"
            source_score.write_text(
                json.dumps(_complete_source_score()),
                encoding="utf-8",
            )
            result = visual_v3.score_saved_evidence(
                source_score=source_score,
                screenshot_root=CALIBRATION_ROOT / "packs" / "reference",
                calibration_root=CALIBRATION_ROOT,
            )
        self.assertEqual(result["score_state"], "complete")
        self.assertEqual(result["raw_score"], 100)
        self.assertEqual(result["display_score"], 100)
        self.assertEqual(result["official_score"], 100)
        self.assertEqual(result["layers"]["behavior"]["points"], 33)
        self.assertEqual(result["layers"]["workflow"]["points"], 22)
        self.assertEqual(result["layers"]["visual"]["points"], 45)
        self.assertEqual(result["contract_sha256"], _sha256(visual_v3.CONTRACT_PATH))


if __name__ == "__main__":
    unittest.main()
