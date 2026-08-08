from __future__ import annotations

from pathlib import Path

from scanner.question_bank import QuestionBank


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTION_PACK = QuestionBank(PROJECT_ROOT / "questions").load()
DEFAULT_QUESTION_IDS = DEFAULT_QUESTION_PACK.enabled_question_ids
DEFAULT_QUESTION_COUNT = DEFAULT_QUESTION_PACK.question_count
DEFAULT_EVALUATION_COUNT = DEFAULT_QUESTION_COUNT
DEFAULT_QUESTION_PACK_VERSION = DEFAULT_QUESTION_PACK.metadata.question_pack_version


def expected_calls_for(model: str, effort: str) -> list[tuple[str, str, str]]:
    return [(model, effort, question_id) for question_id in DEFAULT_QUESTION_IDS]


def expected_question_attempts() -> list[tuple[str, int]]:
    return [
        (question_id, attempt_index)
        for attempt_index, question_id in enumerate(DEFAULT_QUESTION_IDS, start=1)
    ]


def planned_attempts(labels: list[str] | set[str], *, count: int | None = None) -> dict[str, int]:
    attempts = DEFAULT_QUESTION_COUNT if count is None else count
    return {label: attempts for label in labels}
