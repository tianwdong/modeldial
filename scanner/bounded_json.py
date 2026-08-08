"""Small, shared guard for model-produced JSON grader payloads."""

from __future__ import annotations

import json
import math


MAX_RESPONSE_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_COLLECTION_ITEMS = 512
MAX_STRING_BYTES = 64 * 1024
MAX_INTEGER_ABS = 10**18
MAX_INTEGER_DIGITS = 19


class BoundedJSONError(ValueError):
    """Raised when JSON is valid but exceeds the grader input budget."""


def _parse_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_INTEGER_DIGITS:
        raise BoundedJSONError("integer_too_large")
    parsed = int(value)
    if abs(parsed) > MAX_INTEGER_ABS:
        raise BoundedJSONError("integer_too_large")
    return parsed


def _parse_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:  # pragma: no cover - json normally rejects this first
        raise BoundedJSONError("number_invalid") from exc
    if not math.isfinite(parsed) or abs(parsed) > MAX_INTEGER_ABS:
        raise BoundedJSONError("number_too_large")
    return parsed


def _reject_constant(value: str) -> None:
    raise BoundedJSONError("non_standard_number")


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _validate_tree(value: object, *, depth: int, state: list[int]) -> None:
    if depth > MAX_JSON_DEPTH:
        raise BoundedJSONError("json_depth_exceeded")
    state[0] += 1
    if state[0] > MAX_JSON_NODES:
        raise BoundedJSONError("json_nodes_exceeded")

    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise BoundedJSONError("string_too_large")
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if abs(value) > MAX_INTEGER_ABS:
            raise BoundedJSONError("integer_too_large")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > MAX_INTEGER_ABS:
            raise BoundedJSONError("number_too_large")
        return
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise BoundedJSONError("collection_too_large")
        for key, child in value.items():
            _validate_tree(key, depth=depth + 1, state=state)
            _validate_tree(child, depth=depth + 1, state=state)
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise BoundedJSONError("collection_too_large")
        for child in value:
            _validate_tree(child, depth=depth + 1, state=state)


def bounded_json_loads(text: str, *, strip_code_fence: bool = False) -> object:
    """Decode JSON only when its encoded and decoded shape fits the shared budget."""

    try:
        response_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BoundedJSONError("response_not_utf8") from exc
    if response_bytes > MAX_RESPONSE_BYTES:
        raise BoundedJSONError("response_too_large")
    if strip_code_fence:
        text = _strip_code_fence(text)

    try:
        value = json.loads(
            text,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
    except BoundedJSONError:
        raise
    except RecursionError as exc:
        raise BoundedJSONError("json_depth_exceeded") from exc

    _validate_tree(value, depth=0, state=[0])
    return value
