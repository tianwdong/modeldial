from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


HLE_DEEP20_BENCHMARK_REF = "hle-deep-20@v2"
HLE_DEEP20_PROTOCOL_ID = "hle-deep-golden-calibration-v2"
HLE_DEEP20_PARSER_ID = "canonical-json-single-answer-v2"
HLE_RESTRICTED_BUNDLE_SCHEMA_VERSION = 1

HLE_PROMPT_TEMPLATE = """You are answering one frozen, text-only multiple-choice item from Humanity's Last Exam.
Do not use tools, web search, external files, or ask for clarification.
Reason privately. Return exactly one JSON object and nothing else:
{{\"answer\":\"<CHOICE_ID>\"}}
Replace <CHOICE_ID> with exactly one of these allowed IDs: {allowed_choices}

Question:
{question}"""

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHOICE_PATTERNS = (
    re.compile(r"(?m)^\s*([A-Z])\.\s+"),
    re.compile(r"(?m)^\s*\(([A-Z])\)\s+"),
    re.compile(r"(?m)^\s*([A-Z]):\s+"),
)


class HleEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class HleItem:
    item_id: str
    question: str
    answer_choice_id: str
    choice_ids: tuple[str, ...]
    source_content_sha256: str


@dataclass(frozen=True)
class HleBundle:
    benchmark_ref: str
    manifest_sha256: str
    item_count: int
    points_per_item: int
    maximum_score: int
    timeout_seconds: int
    items: tuple[HleItem, ...]


@dataclass(frozen=True)
class HleAnswerScore:
    valid: bool
    correct: bool
    error_kind: str | None


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_payload_sha256(
    value: Mapping[str, object],
    *,
    self_hash_field: str = "canonical_payload_sha256",
) -> str:
    payload = dict(value)
    payload.pop(self_hash_field, None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def prompt_template_sha256() -> str:
    return hashlib.sha256(HLE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


def load_hle_restricted_bundle(
    value: object,
    *,
    expected_benchmark_ref: str,
    expected_manifest_sha256: str,
) -> HleBundle:
    payload = _mapping(value, "HLE restricted bundle")
    if set(payload) != {"schema_version", "benchmark_ref", "manifest", "items"}:
        raise HleEvaluationError("HLE restricted bundle fields are invalid")
    if payload.get("schema_version") != HLE_RESTRICTED_BUNDLE_SCHEMA_VERSION:
        raise HleEvaluationError("HLE restricted bundle schema is unsupported")
    if payload.get("benchmark_ref") != expected_benchmark_ref:
        raise HleEvaluationError("HLE restricted bundle benchmark identity changed")
    manifest = _mapping(payload.get("manifest"), "HLE manifest")
    manifest_sha256 = _required_sha256(
        manifest.get("canonical_payload_sha256"),
        "HLE manifest canonical payload",
    )
    if canonical_payload_sha256(manifest) != manifest_sha256:
        raise HleEvaluationError("HLE manifest canonical payload hash mismatch")
    if manifest_sha256 != _required_sha256(
        expected_manifest_sha256,
        "expected HLE manifest",
    ):
        raise HleEvaluationError("HLE manifest identity is not frozen")
    if manifest.get("golden_set_id") != "hle-deep-20" or manifest.get("version") != "v2":
        raise HleEvaluationError("HLE manifest product identity is unsupported")
    if manifest.get("status") != "accepted":
        raise HleEvaluationError("HLE manifest is not accepted")

    protocol = _mapping(manifest.get("protocol"), "HLE protocol")
    if (
        protocol.get("id") != HLE_DEEP20_PROTOCOL_ID
        or protocol.get("parser") != HLE_DEEP20_PARSER_ID
        or protocol.get("prompt_template_sha256") != prompt_template_sha256()
        or protocol.get("only_completed_turn_can_score") is not True
        or protocol.get("invalid_timeout_or_error_scores_zero") is not True
        or protocol.get("selective_retry") is not False
    ):
        raise HleEvaluationError("HLE protocol is not the frozen Deep-20 protocol")
    timeout_seconds = _bounded_integer(
        protocol.get("timeout_seconds"),
        "HLE timeout",
        1,
        3600,
    )

    scoring = _mapping(manifest.get("scoring"), "HLE scoring")
    item_count = _bounded_integer(scoring.get("item_count"), "HLE item count", 1, 100)
    points_per_item = _bounded_integer(
        scoring.get("points_per_item"),
        "HLE points per item",
        1,
        100,
    )
    maximum_score = _bounded_integer(
        scoring.get("maximum_score"),
        "HLE maximum score",
        1,
        100,
    )
    if (
        scoring.get("partial_credit") is not False
        or item_count != 20
        or points_per_item != 5
        or maximum_score != 100
        or item_count * points_per_item != maximum_score
    ):
        raise HleEvaluationError("HLE Deep-20 scoring contract changed")

    manifest_items = manifest.get("items")
    if not isinstance(manifest_items, list) or len(manifest_items) != item_count:
        raise HleEvaluationError("HLE manifest item set is invalid")
    manifest_by_id: dict[str, Mapping[str, object]] = {}
    manifest_order: list[str] = []
    for raw in manifest_items:
        item = _mapping(raw, "HLE manifest item")
        item_id = _required_text(item.get("item_id"), "HLE manifest item ID")
        if item_id in manifest_by_id:
            raise HleEvaluationError("HLE manifest contains duplicate items")
        _required_sha256(
            item.get("source_content_sha256"),
            "HLE source content",
        )
        manifest_by_id[item_id] = item
        manifest_order.append(item_id)

    bundle_items = payload.get("items")
    if not isinstance(bundle_items, list) or len(bundle_items) != item_count:
        raise HleEvaluationError("HLE restricted bundle item set is invalid")
    loaded: list[HleItem] = []
    for index, raw in enumerate(bundle_items):
        item = _mapping(raw, "HLE restricted item")
        if set(item) != {"item_id", "question", "answer_choice_id"}:
            raise HleEvaluationError("HLE restricted item fields are invalid")
        item_id = _required_text(item.get("item_id"), "HLE restricted item ID")
        if item_id != manifest_order[index]:
            raise HleEvaluationError("HLE restricted item order changed")
        manifest_item = manifest_by_id[item_id]
        question = _required_text(item.get("question"), "HLE question")
        answer = _required_text(
            item.get("answer_choice_id"),
            "HLE answer choice",
        ).upper()
        choice_ids = extract_choice_ids(question)
        if answer not in choice_ids:
            raise HleEvaluationError("HLE answer is not an allowed choice")
        source_hash = source_content_sha256(question, answer)
        if source_hash != manifest_item.get("source_content_sha256"):
            raise HleEvaluationError("HLE restricted item content hash mismatch")
        option_count = _bounded_integer(
            manifest_item.get("option_count"),
            "HLE option count",
            2,
            26,
        )
        if option_count != len(choice_ids):
            raise HleEvaluationError("HLE restricted item option count changed")
        loaded.append(
            HleItem(
                item_id=item_id,
                question=question,
                answer_choice_id=answer,
                choice_ids=choice_ids,
                source_content_sha256=source_hash,
            )
        )

    return HleBundle(
        benchmark_ref=expected_benchmark_ref,
        manifest_sha256=manifest_sha256,
        item_count=item_count,
        points_per_item=points_per_item,
        maximum_score=maximum_score,
        timeout_seconds=timeout_seconds,
        items=tuple(loaded),
    )


def build_hle_prompt(item: HleItem) -> str:
    return HLE_PROMPT_TEMPLATE.format(
        allowed_choices=", ".join(item.choice_ids),
        question=item.question,
    )


def score_hle_response(
    response: str,
    *,
    item: HleItem,
    terminal_state: str,
) -> HleAnswerScore:
    if terminal_state != "completed_turn":
        return HleAnswerScore(
            valid=False,
            correct=False,
            error_kind=f"terminal_state:{terminal_state or 'missing'}",
        )
    valid, answer, error = parse_hle_answer(response, item.choice_ids)
    return HleAnswerScore(
        valid=valid,
        correct=bool(valid and answer == item.answer_choice_id),
        error_kind=error,
    )


def parse_hle_answer(
    response: str,
    allowed_choices: Sequence[str],
) -> tuple[bool, str | None, str | None]:
    if response != response.strip():
        return False, None, "noncanonical_whitespace"

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate_json_key")
            parsed[key] = value
        return parsed

    try:
        parsed = json.loads(response, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError):
        return False, None, "invalid_json"
    if not isinstance(parsed, dict) or set(parsed) != {"answer"}:
        return False, None, "invalid_schema"
    answer = parsed.get("answer")
    if not isinstance(answer, str):
        return False, None, "invalid_answer_type"
    if answer not in allowed_choices:
        return False, None, "invalid_choice"
    if response != canonical_json({"answer": answer}):
        return False, None, "noncanonical_json"
    return True, answer, None


def extract_choice_ids(question: str) -> tuple[str, ...]:
    for pattern in _CHOICE_PATTERNS:
        choices = tuple(dict.fromkeys(choice.upper() for choice in pattern.findall(question)))
        if len(choices) >= 2:
            return choices
    raise HleEvaluationError("HLE question has no recognizable choices")


def source_content_sha256(question: str, answer_choice_id: str) -> str:
    if "Answer Choices:" not in question:
        raise HleEvaluationError("HLE question has no Answer Choices marker")
    choices = re.findall(
        r"(?ms)^([A-Z])\.\s*(.*?)(?=\n[A-Z]\.\s|\Z)",
        question.split("Answer Choices:", 1)[1],
    )
    if len(choices) < 2:
        raise HleEvaluationError("HLE choice text cannot be canonicalized")
    canonical = {
        "question": _normalize_source_text(question),
        "choices": [
            (choice_id, _normalize_source_text(choice_text))
            for choice_id, choice_text in choices
        ],
        "answer_choice_id": answer_choice_id,
    }
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def _normalize_source_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.strip().split("\n"))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HleEvaluationError(f"{label} must contain an object")
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HleEvaluationError(f"{label} is missing")
    return value.strip()


def _required_sha256(value: object, label: str) -> str:
    text = _required_text(value, label)
    if not _SHA256.fullmatch(text):
        raise HleEvaluationError(f"{label} SHA-256 is invalid")
    return text


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise HleEvaluationError(f"{label} is invalid")
    return value
