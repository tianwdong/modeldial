from __future__ import annotations

import unittest

from scanner.models import RunMetadata
from scanner.scoring import EQUAL_SCORING_MODE, uses_equal_scoring


class EqualScoringTest(unittest.TestCase):
    def test_current_metadata_uses_one_equal_q1_to_q5_protocol(self) -> None:
        metadata = RunMetadata(
            run_id="run-equal",
            question_pack_id="coding-fast",
            question_pack_version="coding-fast-v3.10",
            started_at="2026-07-21T10:00:00+08:00",
            completed_at=None,
            candidate_count=2,
            question_count=5,
            status="running",
            scoring_mode=EQUAL_SCORING_MODE,
        )

        restored = RunMetadata.from_dict(metadata.to_dict())

        self.assertTrue(uses_equal_scoring(restored.to_dict()))
        self.assertEqual(restored.question_count, 5)
        self.assertFalse(
            any(
                key.startswith(("quick_", "review_", "challenge_"))
                for key in restored.to_dict()
            )
        )

    def test_other_protocol_does_not_match_equal_scoring(self) -> None:
        self.assertFalse(uses_equal_scoring({"scoring_mode": "legacy"}))


if __name__ == "__main__":
    unittest.main()
