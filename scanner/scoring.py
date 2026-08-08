from __future__ import annotations


EQUAL_SCORING_MODE = "semantic_q1_q5_equal_v2"


def uses_equal_scoring(metadata: dict[str, object] | None) -> bool:
    return str((metadata or {}).get("scoring_mode") or "") == EQUAL_SCORING_MODE
