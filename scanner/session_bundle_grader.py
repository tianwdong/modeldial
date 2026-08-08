from __future__ import annotations

import ast
import importlib
import json
import re
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile


SOURCE_FILES = (
    "session_bundle/__init__.py",
    "session_bundle/errors.py",
    "session_bundle/storage.py",
    "session_bundle/replay.py",
)
FACET_LABELS = {
    "schema": "结构与顺序校验",
    "archive": "归档完整性",
    "atomicity": "覆盖与原子写入",
    "replay": "真实回放语义",
    "api": "公开 API 契约",
}
CASE_SPECS = (
    ("schema", "valid_round_trip_without_mutation", "往返保存不修改输入"),
    ("schema", "booleans_are_not_integers", "布尔值不冒充整数"),
    ("schema", "sequence_uses_physical_order", "序号遵循物理顺序"),
    ("schema", "nested_result_and_error_shapes", "嵌套结果与错误结构"),
    ("archive", "member_cardinality_and_allowlist", "成员基数与白名单"),
    ("archive", "malformed_json_is_reported", "损坏 JSON 归一化"),
    ("archive", "blank_jsonl_lines_are_invalid", "空白 JSONL 行非法"),
    ("archive", "independent_errors_are_aggregated", "独立错误全部聚合"),
    ("archive", "nonstandard_json_constants_are_rejected", "拒绝非标准 JSON 常量"),
    ("archive", "loading_never_executes_code", "加载不执行代码"),
    ("atomicity", "existing_target_precedes_validation", "已有目标优先拒绝"),
    ("atomicity", "existing_target_does_not_consume_events", "已有目标不消费事件流"),
    ("atomicity", "invalid_overwrite_preserves_target", "非法覆盖保留原文件"),
    ("atomicity", "serialization_failure_is_atomic", "序列化失败保持原子性"),
    ("atomicity", "single_pass_iterable_and_owned_inputs", "单次消费且不修改输入"),
    ("atomicity", "reused_mapping_is_snapshotted_per_yield", "复用映射逐次快照"),
    ("replay", "history_enabled_advances_once_per_cell", "历史模式逐单元推进"),
    ("replay", "history_disabled_restores_counter", "关闭历史恢复计数"),
    ("replay", "run_cell_exception_restores_counter", "执行异常恢复计数"),
    ("replay", "actual_failure_controls_stop", "真实失败控制停止"),
    ("replay", "recorded_failure_does_not_control_stop", "记录失败不控制停止"),
    ("api", "public_exports_are_complete", "公开导出完整"),
    ("api", "validation_exception_keeps_path_and_errors", "异常保留路径与错误"),
    ("api", "event_count_is_optional_and_zero_is_exact", "可选计数与零值精确"),
    ("api", "unreadable_bundle_uses_validation_contract", "不可读归档遵循验证契约"),
)
CLUSTERS = {
    "schema_types": (
        "booleans_are_not_integers",
        "nested_result_and_error_shapes",
    ),
    "physical_order_and_count": (
        "sequence_uses_physical_order",
        "event_count_is_optional_and_zero_is_exact",
    ),
    "archive_integrity": (
        "member_cardinality_and_allowlist",
        "malformed_json_is_reported",
        "blank_jsonl_lines_are_invalid",
        "unreadable_bundle_uses_validation_contract",
    ),
    "error_aggregation_and_standard_json": (
        "independent_errors_are_aggregated",
        "nonstandard_json_constants_are_rejected",
    ),
    "input_ownership_and_snapshot": (
        "valid_round_trip_without_mutation",
        "single_pass_iterable_and_owned_inputs",
        "reused_mapping_is_snapshotted_per_yield",
    ),
    "overwrite_precedence": (
        "existing_target_precedes_validation",
        "existing_target_does_not_consume_events",
    ),
    "transactional_failure": (
        "invalid_overwrite_preserves_target",
        "serialization_failure_is_atomic",
    ),
    "actual_replay_semantics": (
        "history_enabled_advances_once_per_cell",
        "actual_failure_controls_stop",
        "recorded_failure_does_not_control_stop",
    ),
    "history_and_exception_restoration": (
        "history_disabled_restores_counter",
        "run_cell_exception_restores_counter",
    ),
    "public_api_and_safe_loading": (
        "public_exports_are_complete",
        "validation_exception_keeps_path_and_errors",
        "loading_never_executes_code",
    ),
}
CLUSTER_LABELS = {
    "schema_types": "类型契约",
    "physical_order_and_count": "物理顺序与计数",
    "archive_integrity": "归档完整性",
    "error_aggregation_and_standard_json": "错误聚合与标准 JSON",
    "input_ownership_and_snapshot": "输入所有权与快照",
    "overwrite_precedence": "覆盖优先级",
    "transactional_failure": "事务失败保护",
    "actual_replay_semantics": "真实回放语义",
    "history_and_exception_restoration": "历史与异常恢复",
    "public_api_and_safe_loading": "公开 API 与安全加载",
}
RAW_MAX_SCORE = len(CASE_SPECS)
MAX_SCORE = len(CLUSTERS)

STARTER_FILES = {
    "session_bundle/__init__.py": '''from .errors import SessionBundleValidationError
from .replay import replay_session_bundle
from .storage import load_session_bundle, save_session_bundle, validate_session_bundle

__all__ = (
    "load_session_bundle",
    "replay_session_bundle",
    "save_session_bundle",
)
''',
    "session_bundle/errors.py": '''from pathlib import Path
from typing import Iterable


class SessionBundleValidationError(ValueError):
    def __init__(self, bundle_path: str | Path, errors: Iterable[str]) -> None:
        self.bundle_path = str(bundle_path)
        self.errors = tuple(errors)
        super().__init__("invalid session bundle")
''',
    "session_bundle/storage.py": '''from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

from .errors import SessionBundleValidationError


def _schema_errors(metadata: object, events: list[object]) -> list[str]:
    errors: list[str] = []
    if not isinstance(metadata, dict):
        return ["metadata must be an object"]
    if metadata.get("format") != "session-bundle":
        errors.append("metadata format is invalid")
    version = metadata.get("format_version")
    if not isinstance(version, int) or version < 1:
        errors.append("format_version must be a positive integer")
    if metadata.get("event_count") and metadata["event_count"] != len(events):
        errors.append("event_count does not match events")

    sequences: list[int] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            errors.append(f"event {index} must be an object")
            continue
        if isinstance(event.get("seq"), int):
            sequences.append(event["seq"])
        else:
            errors.append(f"event {index} seq must be an integer")
        if event.get("type") != "cell":
            errors.append(f"event {index} type is invalid")
        if not isinstance(event.get("code"), str):
            errors.append(f"event {index} code must be a string")
        if not isinstance(event.get("success"), bool):
            errors.append(f"event {index} success must be boolean")
        if event.get("success") is False and "error" not in event:
            errors.append(f"event {index} error is missing")
    if sorted(sequences) != list(range(1, len(events) + 1)):
        errors.append("event sequence is not contiguous")
    return errors


def _read_bundle(path: Path) -> tuple[object, list[object], list[str]]:
    errors: list[str] = []
    with ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        if names != {"metadata.json", "events.jsonl"}:
            errors.append("bundle members are invalid")
        metadata = json.loads(archive.read("metadata.json"))
        text = archive.read("events.jsonl").decode("utf-8")
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
    errors.extend(_schema_errors(metadata, events))
    return metadata, events, errors


def validate_session_bundle(path: str | Path, *, strict: bool = True) -> list[str]:
    bundle_path = Path(path)
    _, _, errors = _read_bundle(bundle_path)
    if strict and errors:
        raise SessionBundleValidationError(bundle_path, errors)
    return errors


def load_session_bundle(path: str | Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    bundle_path = Path(path)
    metadata, events, errors = _read_bundle(bundle_path)
    if errors:
        raise SessionBundleValidationError(bundle_path, errors)
    return metadata, events


def save_session_bundle(
    path: str | Path,
    metadata: Mapping[str, object],
    events: Iterable[Mapping[str, object]],
    *,
    overwrite: bool = False,
) -> Path:
    bundle_path = Path(path)
    if bundle_path.exists() and not overwrite:
        raise FileExistsError(bundle_path)
    event_list = list(events)
    metadata["event_count"] = len(event_list)
    errors = _schema_errors(metadata, event_list)
    if errors:
        raise SessionBundleValidationError(bundle_path, errors)
    with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, sort_keys=True))
        archive.writestr(
            "events.jsonl",
            "".join(json.dumps(event, sort_keys=True) + "\\n" for event in event_list),
        )
    return bundle_path
''',
    "session_bundle/replay.py": '''from __future__ import annotations

from pathlib import Path

from .storage import load_session_bundle


def replay_session_bundle(
    shell: object,
    path: str | Path,
    *,
    stop_on_error: bool = True,
    store_history: bool = True,
) -> list[dict[str, object]]:
    _, events = load_session_bundle(path)
    outcomes: list[dict[str, object]] = []
    for event in events:
        shell.run_cell(event["code"], store_history=True)
        success = bool(event["success"])
        outcomes.append({"seq": event["seq"], "success": success})
        if stop_on_error and not success:
            break
    return outcomes
''',
}

ALLOWED_MODULES = {
    "__future__",
    "collections",
    "contextlib",
    "copy",
    "dataclasses",
    "datetime",
    "io",
    "json",
    "math",
    "os",
    "pathlib",
    "shutil",
    "tempfile",
    "typing",
    "uuid",
    "zipfile",
    "zlib",
}
DANGEROUS_ATTRIBUTES = {
    "__class__",
    "__closure__",
    "__code__",
    "__func__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__subclasses__",
}
DANGEROUS_CALL_ATTRIBUTES = {
    "execv",
    "execve",
    "fork",
    "popen",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "system",
}


@dataclass(frozen=True)
class Case:
    case_id: str
    facet: str
    run: Callable[[], None]


def case_details() -> list[dict[str, str]]:
    return [
        {
            "case_id": case_id,
            "label": label,
            "category": facet,
            "category_label": FACET_LABELS[facet],
        }
        for facet, case_id, label in CASE_SPECS
    ]


def _case_detail(case_id: str) -> dict[str, str]:
    return next(detail for detail in case_details() if detail["case_id"] == case_id)


def _strip_outer_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip() + "\n"
    return text


def _skip_blank_lines(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _find_marker(lines: list[str], start: int, marker: str) -> int:
    for index in range(start, len(lines)):
        if lines[index].strip() == marker:
            return index
    raise ValueError(f"missing_marker:{marker}")


def _apply_context_patch(source_files: dict[str, str], patch_text: str) -> dict[str, str]:
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").splitlines(True)
    index = _skip_blank_lines(lines, 0)
    if index >= len(lines) or lines[index].strip() != "*** Begin Patch":
        raise ValueError("missing_begin_patch")
    index = _skip_blank_lines(lines, index + 1)
    hunks: list[tuple[str, str, str]] = []
    while index < len(lines) and lines[index].strip() != "*** End Patch":
        header = lines[index].strip()
        if header.startswith("*** Delete File: "):
            path = header.removeprefix("*** Delete File: ").strip()
            if path not in source_files:
                raise ValueError(f"unknown_delete_file:{path}")
            index = _skip_blank_lines(lines, index + 1)
            if index >= len(lines) or not lines[index].strip().startswith("*** Add File: "):
                raise ValueError("missing_add_file")
            add_path = lines[index].strip().removeprefix("*** Add File: ").strip()
            if add_path != path:
                raise ValueError(f"delete_add_path_mismatch:{path}:{add_path}")
            index += 1
            replacement_lines: list[str] = []
            while index < len(lines) and not lines[index].startswith("*** "):
                if not lines[index].startswith("+"):
                    raise ValueError("invalid_add_file_line")
                replacement_lines.append(lines[index][1:])
                index += 1
            hunks.append((path, source_files[path], "".join(replacement_lines)))
            index = _skip_blank_lines(lines, index)
            continue
        if not header.startswith("*** Update File: "):
            raise ValueError("missing_update_file")
        path = header.removeprefix("*** Update File: ").strip()
        if path not in source_files:
            raise ValueError(f"unknown_update_file:{path}")
        index = _skip_blank_lines(lines, index + 1)
        saw_hunk = False
        while index < len(lines):
            marker = lines[index].strip()
            if marker == "*** End Patch" or marker.startswith("*** Update File: "):
                break
            if not marker.startswith("@@"):
                raise ValueError("missing_context_hunk")
            index += 1
            search_lines: list[str] = []
            replacement_lines = []
            while index < len(lines):
                line = lines[index]
                if line.startswith("@@") or line.startswith("*** Update File: ") or line.strip() == "*** End Patch":
                    break
                if line.startswith(" "):
                    search_lines.append(line[1:])
                    replacement_lines.append(line[1:])
                elif line.startswith("-"):
                    search_lines.append(line[1:])
                elif line.startswith("+"):
                    replacement_lines.append(line[1:])
                else:
                    raise ValueError("invalid_context_hunk_line")
                index += 1
            search = "".join(search_lines)
            if not search:
                raise ValueError("empty_context_hunk")
            hunks.append((path, search, "".join(replacement_lines)))
            saw_hunk = True
            index = _skip_blank_lines(lines, index)
        if not saw_hunk:
            raise ValueError("empty_update_file")
    if index >= len(lines) or lines[index].strip() != "*** End Patch":
        raise ValueError("missing_end_patch")
    if not hunks:
        raise ValueError("empty_patch")
    if "".join(lines[index + 1 :]).strip():
        raise ValueError("trailing_content")
    patched = dict(source_files)
    for path, search, replacement in hunks:
        count = patched[path].count(search)
        if count != 1:
            raise ValueError(f"context_hunk_not_unique:{path}:{count}")
        patched[path] = patched[path].replace(search, replacement, 1)
    return patched


def _apply_patch(source_files: dict[str, str], patch_text: str) -> dict[str, str]:
    patch_text = _strip_outer_fence(patch_text)
    if "<<<<<<< SEARCH" not in patch_text and (
        re.search(r"(?m)^@@(?:\s|$)", patch_text) or "*** Delete File: " in patch_text
    ):
        return _apply_context_patch(source_files, patch_text)
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").splitlines(True)
    index = _skip_blank_lines(lines, 0)
    if index >= len(lines) or lines[index].strip() != "*** Begin Patch":
        raise ValueError("missing_begin_patch")
    index = _skip_blank_lines(lines, index + 1)
    replacements: list[tuple[str, str, str]] = []
    while index < len(lines) and lines[index].strip() != "*** End Patch":
        header = lines[index].strip()
        if not header.startswith("*** Update File: "):
            raise ValueError("missing_update_file")
        path = header.removeprefix("*** Update File: ").strip()
        if path not in source_files:
            raise ValueError(f"unknown_update_file:{path}")
        index = _skip_blank_lines(lines, index + 1)
        saw_block = False
        while index < len(lines):
            marker = lines[index].strip()
            if marker == "*** End Patch" or marker.startswith("*** Update File: "):
                break
            if marker != "<<<<<<< SEARCH":
                raise ValueError("missing_search_marker")
            separator = _find_marker(lines, index + 1, "=======")
            replace_end = _find_marker(lines, separator + 1, ">>>>>>> REPLACE")
            search = "".join(lines[index + 1 : separator])
            replacement = "".join(lines[separator + 1 : replace_end])
            if not search:
                raise ValueError("empty_search_block")
            replacements.append((path, search, replacement))
            saw_block = True
            index = _skip_blank_lines(lines, replace_end + 1)
        if not saw_block:
            raise ValueError("empty_update_file")
    if index >= len(lines) or lines[index].strip() != "*** End Patch":
        raise ValueError("missing_end_patch")
    if not replacements:
        raise ValueError("empty_patch")
    if "".join(lines[index + 1 :]).strip():
        raise ValueError("trailing_content")
    patched = dict(source_files)
    for path, search, replacement in replacements:
        count = patched[path].count(search)
        if count != 1:
            raise ValueError(f"search_block_not_unique:{path}:{count}")
        patched[path] = patched[path].replace(search, replacement, 1)
    return patched


def _validate_sources(source_files: dict[str, str]) -> None:
    banned_calls = {"__import__", "breakpoint", "compile", "eval", "exec", "globals", "input", "locals"}
    for relative, source in source_files.items():
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ValueError(f"syntax_error:{relative}:{exc.msg}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] not in ALLOWED_MODULES:
                        raise ValueError(f"forbidden_import:{relative}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = (node.module or "").split(".", 1)[0]
                if module not in ALLOWED_MODULES:
                    raise ValueError(f"forbidden_import:{relative}:{node.module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned_calls:
                raise ValueError(f"forbidden_call:{relative}:{node.func.id}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in DANGEROUS_CALL_ATTRIBUTES:
                raise ValueError(f"forbidden_call:{relative}:{node.func.attr}")
            elif isinstance(node, ast.Attribute) and node.attr in DANGEROUS_ATTRIBUTES:
                raise ValueError(f"forbidden_dunder_access:{relative}:{node.attr}")


def _materialize(source_files: dict[str, str], root: Path) -> None:
    for relative, source in source_files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "from __future__ import annotations" not in source:
            source = "from __future__ import annotations\n\n" + source
        destination.write_text(source, encoding="utf-8")


def _metadata(*, event_count: object = ...) -> dict[str, object]:
    value: dict[str, object] = {"format": "session-bundle", "format_version": 1}
    if event_count is not ...:
        value["event_count"] = event_count
    return value


def _event(
    seq: object = 1,
    *,
    code: object = "value = 1",
    success: object = True,
    execution_count: object = 1,
    execute_result: object | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "cell",
        "seq": seq,
        "code": code,
        "success": success,
        "execution_count": execution_count,
        "stdout": "",
        "stderr": "",
        "execute_result": {} if execute_result is None else execute_result,
    }
    if success is False:
        event["error"] = {"ename": "RuntimeError", "evalue": "boom", "traceback": ["RuntimeError: boom"]}
    return event


def _write_archive(
    path: Path,
    metadata: object,
    event_lines: list[object] | str,
    *,
    duplicate_metadata: bool = False,
    unexpected_member: bool = False,
) -> None:
    event_text = event_lines if isinstance(event_lines, str) else "".join(json.dumps(event) + "\n" for event in event_lines)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata))
            if duplicate_metadata:
                archive.writestr("metadata.json", json.dumps(metadata))
            archive.writestr("events.jsonl", event_text)
            if unexpected_member:
                archive.writestr("notes.txt", "unexpected")


class _ReplayResult:
    def __init__(self, success: bool) -> None:
        self.success = success


class _ReplayShell:
    def __init__(self, results: list[bool], execution_count: int = 40) -> None:
        self._results = iter(results)
        self.execution_count = execution_count
        self.calls: list[tuple[str, bool]] = []

    def run_cell(self, code: str, *, store_history: bool) -> _ReplayResult:
        self.calls.append((code, store_history))
        self.execution_count += 1
        return _ReplayResult(next(self._results))


def _expect_raises(expected: type[BaseException], operation: Callable[[], object]) -> BaseException:
    try:
        operation()
    except expected as exc:
        return exc
    except BaseException as exc:
        raise AssertionError(f"expected {expected.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {expected.__name__}")


def _build_cases(module: object) -> list[Case]:
    cases: list[Case] = []

    def register(facet: str, case_id: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
        def decorator(operation: Callable[[], None]) -> Callable[[], None]:
            cases.append(Case(case_id, facet, operation))
            return operation
        return decorator

    @register("schema", "valid_round_trip_without_mutation")
    def valid_round_trip_without_mutation() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ok.bundle"
            metadata = _metadata()
            events = [_event()]
            before_metadata = dict(metadata)
            before_events = json.loads(json.dumps(events))
            assert module.save_session_bundle(path, metadata, events) == path
            loaded_metadata, loaded_events = module.load_session_bundle(path)
            assert loaded_metadata in (before_metadata, {**before_metadata, "event_count": 1})
            assert loaded_events == before_events
            assert metadata == before_metadata and events == before_events

    @register("schema", "booleans_are_not_integers")
    def booleans_are_not_integers() -> None:
        variants = [
            (_metadata(), [_event(seq=True)]),
            ({"format": "session-bundle", "format_version": True}, [_event()]),
            (_metadata(event_count=True), [_event()]),
            (_metadata(), [_event(execution_count=True)]),
        ]
        with tempfile.TemporaryDirectory() as temp:
            for index, (metadata, events) in enumerate(variants):
                path = Path(temp) / f"bool-{index}.bundle"
                _write_archive(path, metadata, events)
                assert module.validate_session_bundle(path, strict=False)

    @register("schema", "sequence_uses_physical_order")
    def sequence_uses_physical_order() -> None:
        with tempfile.TemporaryDirectory() as temp:
            swapped = Path(temp) / "swapped.bundle"
            gap = Path(temp) / "gap.bundle"
            _write_archive(swapped, _metadata(event_count=2), [_event(2), _event(1)])
            _write_archive(gap, _metadata(event_count=2), [_event(1), _event(3)])
            assert module.validate_session_bundle(swapped, strict=False)
            assert module.validate_session_bundle(gap, strict=False)

    @register("schema", "nested_result_and_error_shapes")
    def nested_result_and_error_shapes() -> None:
        missing_text = _event(execute_result={"image/png": "abc"})
        bad_error = _event(success=False)
        bad_error["error"] = {"ename": 7, "evalue": "boom", "traceback": []}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested.bundle"
            _write_archive(path, _metadata(event_count=2), [missing_text, {**bad_error, "seq": 2}])
            errors = module.validate_session_bundle(path, strict=False)
            assert any("text/plain" in error for error in errors)
            assert any("traceback" in error for error in errors)
            assert any("ename" in error for error in errors)

    @register("archive", "member_cardinality_and_allowlist")
    def member_cardinality_and_allowlist() -> None:
        with tempfile.TemporaryDirectory() as temp:
            duplicate = Path(temp) / "duplicate.bundle"
            unexpected = Path(temp) / "unexpected.bundle"
            _write_archive(duplicate, _metadata(event_count=0), [], duplicate_metadata=True)
            _write_archive(unexpected, _metadata(event_count=0), [], unexpected_member=True)
            assert module.validate_session_bundle(duplicate, strict=False)
            assert module.validate_session_bundle(unexpected, strict=False)

    @register("archive", "malformed_json_is_reported")
    def malformed_json_is_reported() -> None:
        with tempfile.TemporaryDirectory() as temp:
            bad_metadata = Path(temp) / "bad-meta.bundle"
            bad_events = Path(temp) / "bad-events.bundle"
            with ZipFile(bad_metadata, "w") as archive:
                archive.writestr("metadata.json", "{")
                archive.writestr("events.jsonl", "")
            _write_archive(bad_events, _metadata(), "{not json}\n")
            assert module.validate_session_bundle(bad_metadata, strict=False)
            assert module.validate_session_bundle(bad_events, strict=False)

    @register("archive", "blank_jsonl_lines_are_invalid")
    def blank_jsonl_lines_are_invalid() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "blank.bundle"
            _write_archive(path, _metadata(event_count=1), json.dumps(_event()) + "\n\n")
            assert any("blank" in error for error in module.validate_session_bundle(path, strict=False))

    @register("archive", "independent_errors_are_aggregated")
    def independent_errors_are_aggregated() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "many-errors.bundle"
            invalid_event = {
                "type": "cell",
                "seq": 1,
                "code": 7,
                "success": "yes",
                "execution_count": True,
                "stdout": [],
                "stderr": None,
                "execute_result": 9,
            }
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("metadata.json", "{")
                archive.writestr(
                    "events.jsonl",
                    json.dumps(invalid_event) + "\n{not json}\n\n",
                )
            errors = module.validate_session_bundle(path, strict=False)
            assert any("metadata" in error for error in errors)
            assert any("invalid" in error and "line" in error for error in errors)
            assert any("blank" in error for error in errors)
            assert any("code" in error for error in errors)
            assert any("success" in error for error in errors)
            assert len(errors) >= 7

    @register("archive", "nonstandard_json_constants_are_rejected")
    def nonstandard_json_constants_are_rejected() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nan.bundle"
            metadata = '{"format":"session-bundle","format_version":1,"extension":NaN}'
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("metadata.json", metadata)
                archive.writestr("events.jsonl", "")
            errors = module.validate_session_bundle(path, strict=False)
            assert errors
            _expect_raises(
                module.SessionBundleValidationError,
                lambda: module.load_session_bundle(path),
            )

    @register("archive", "loading_never_executes_code")
    def loading_never_executes_code() -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "executed.txt"
            event = _event(code=f"open({str(marker)!r}, 'w').write('bad')")
            path = Path(temp) / "safe.bundle"
            _write_archive(path, _metadata(event_count=1), [event])
            _, loaded = module.load_session_bundle(path)
            assert loaded[0]["code"] == event["code"] and not marker.exists()

    @register("atomicity", "existing_target_precedes_validation")
    def existing_target_precedes_validation() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.bundle"
            path.write_bytes(b"do-not-touch")
            _expect_raises(FileExistsError, lambda: module.save_session_bundle(path, {}, [], overwrite=False))
            assert path.read_bytes() == b"do-not-touch"

    @register("atomicity", "existing_target_does_not_consume_events")
    def existing_target_does_not_consume_events() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.bundle"
            original = b"old"
            path.write_bytes(original)

            def events():
                raise AssertionError("event iterable was consumed")
                yield _event()

            _expect_raises(
                FileExistsError,
                lambda: module.save_session_bundle(path, {}, events(), overwrite=False),
            )
            assert path.read_bytes() == original

    @register("atomicity", "invalid_overwrite_preserves_target")
    def invalid_overwrite_preserves_target() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.bundle"
            path.write_bytes(b"old-valid-or-not")
            _expect_raises(module.SessionBundleValidationError, lambda: module.save_session_bundle(path, {}, [], overwrite=True))
            assert path.read_bytes() == b"old-valid-or-not"

    @register("atomicity", "serialization_failure_is_atomic")
    def serialization_failure_is_atomic() -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "existing.bundle"
            path.write_bytes(b"original-bytes")
            metadata = {**_metadata(event_count=0), "extension": {1, 2}}
            _expect_raises(TypeError, lambda: module.save_session_bundle(path, metadata, [], overwrite=True))
            assert path.read_bytes() == b"original-bytes"
            assert list(root.glob(f".{path.name}.*.tmp")) == []

    @register("atomicity", "single_pass_iterable_and_owned_inputs")
    def single_pass_iterable_and_owned_inputs() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generator.bundle"
            metadata = _metadata()
            event = _event()
            def generate():
                yield event
            module.save_session_bundle(path, metadata, generate())
            loaded_metadata, loaded_events = module.load_session_bundle(path)
            assert loaded_metadata in (_metadata(), _metadata(event_count=1))
            assert loaded_events == [event] and metadata == _metadata() and event == _event()

    @register("atomicity", "reused_mapping_is_snapshotted_per_yield")
    def reused_mapping_is_snapshotted_per_yield() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reused.bundle"
            reusable = _event(1, code="first")

            def events():
                yield reusable
                reusable.clear()
                reusable.update(_event(2, code="second"))
                yield reusable

            module.save_session_bundle(path, _metadata(event_count=2), events())
            _, loaded = module.load_session_bundle(path)
            assert [event["seq"] for event in loaded] == [1, 2]
            assert [event["code"] for event in loaded] == ["first", "second"]

    @register("replay", "history_enabled_advances_once_per_cell")
    def history_enabled_advances_once_per_cell() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "replay.bundle"
            module.save_session_bundle(path, _metadata(event_count=2), [_event(1, code="a = 1"), _event(2, code="b = 2")])
            shell = _ReplayShell([True, True])
            outcomes = module.replay_session_bundle(shell, path, store_history=True)
            assert shell.execution_count == 42
            assert shell.calls == [("a = 1", True), ("b = 2", True)]
            assert outcomes == [{"seq": 1, "success": True}, {"seq": 2, "success": True}]

    @register("replay", "history_disabled_restores_counter")
    def history_disabled_restores_counter() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "replay.bundle"
            module.save_session_bundle(path, _metadata(event_count=2), [_event(1, code="a = 1"), _event(2, code="b = 2")])
            shell = _ReplayShell([True, True])
            module.replay_session_bundle(shell, path, store_history=False)
            assert shell.execution_count == 40
            assert shell.calls == [("a = 1", False), ("b = 2", False)]

    @register("replay", "run_cell_exception_restores_counter")
    def run_cell_exception_restores_counter() -> None:
        class ReplayBoom(RuntimeError):
            pass

        class Shell:
            def __init__(self) -> None:
                self.execution_count = 40
                self.calls: list[tuple[str, bool]] = []

            def run_cell(self, code: str, *, store_history: bool):
                self.calls.append((code, store_history))
                self.execution_count += 1
                raise ReplayBoom("boom")

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "replay.bundle"
            module.save_session_bundle(path, _metadata(event_count=1), [_event()])
            shell = Shell()
            _expect_raises(
                ReplayBoom,
                lambda: module.replay_session_bundle(shell, path, store_history=False),
            )
            assert shell.execution_count == 40
            assert shell.calls == [("value = 1", False)]

    @register("replay", "actual_failure_controls_stop")
    def actual_failure_controls_stop() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "replay.bundle"
            module.save_session_bundle(path, _metadata(event_count=2), [_event(1, code="first"), _event(2, code="second")])
            shell = _ReplayShell([False, True])
            outcomes = module.replay_session_bundle(shell, path, stop_on_error=True)
            assert shell.calls == [("first", True)]
            assert outcomes == [{"seq": 1, "success": False}]

    @register("replay", "recorded_failure_does_not_control_stop")
    def recorded_failure_does_not_control_stop() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "replay.bundle"
            module.save_session_bundle(path, _metadata(event_count=2), [_event(1, code="first", success=False), _event(2, code="second")])
            shell = _ReplayShell([True, True])
            outcomes = module.replay_session_bundle(shell, path, stop_on_error=True)
            assert shell.calls == [("first", True), ("second", True)]
            assert outcomes == [{"seq": 1, "success": True}, {"seq": 2, "success": True}]

    @register("api", "public_exports_are_complete")
    def public_exports_are_complete() -> None:
        expected = {"SessionBundleValidationError", "load_session_bundle", "replay_session_bundle", "save_session_bundle", "validate_session_bundle"}
        assert set(module.__all__) == expected and all(hasattr(module, name) for name in expected)

    @register("api", "validation_exception_keeps_path_and_errors")
    def validation_exception_keeps_path_and_errors() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.bundle"
            _write_archive(path, {}, [])
            errors = module.validate_session_bundle(path, strict=False)
            exc = _expect_raises(module.SessionBundleValidationError, lambda: module.validate_session_bundle(str(path), strict=True))
            assert exc.bundle_path == path and type(exc.errors) is list and exc.errors == errors
            assert all(isinstance(error, str) and error for error in errors)

    @register("api", "event_count_is_optional_and_zero_is_exact")
    def event_count_is_optional_and_zero_is_exact() -> None:
        with tempfile.TemporaryDirectory() as temp:
            absent = Path(temp) / "absent.bundle"
            zero = Path(temp) / "zero.bundle"
            invalid = Path(temp) / "invalid.bundle"
            module.save_session_bundle(absent, _metadata(), [])
            module.save_session_bundle(zero, _metadata(event_count=0), [])
            assert module.load_session_bundle(absent)[0].get("event_count", 0) == 0
            assert module.load_session_bundle(zero)[0]["event_count"] == 0
            _write_archive(invalid, _metadata(event_count=True), [])
            assert module.validate_session_bundle(invalid, strict=False)

    @register("api", "unreadable_bundle_uses_validation_contract")
    def unreadable_bundle_uses_validation_contract() -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "not-a-zip.bundle"
            path.write_text("not a zip", encoding="utf-8")
            errors = module.validate_session_bundle(str(path), strict=False)
            assert errors and all(isinstance(error, str) for error in errors)
            _expect_raises(module.SessionBundleValidationError, lambda: module.validate_session_bundle(path, strict=True))
            _expect_raises(module.SessionBundleValidationError, lambda: module.load_session_bundle(path))

    assert [(case.facet, case.case_id) for case in cases] == [(facet, case_id) for facet, case_id, _ in CASE_SPECS]
    return cases


def _run_cases(root: Path) -> dict[str, object]:
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    for name in list(sys.modules):
        if name == "session_bundle" or name.startswith("session_bundle."):
            sys.modules.pop(name, None)
    try:
        module = importlib.import_module("session_bundle")
        cases = _build_cases(module)
        facets = {
            facet: {
                "label": label,
                "score": 0,
                "max_score": sum(1 for item_facet, _, _ in CASE_SPECS if item_facet == facet),
            }
            for facet, label in FACET_LABELS.items()
        }
        failures: list[dict[str, str]] = []
        for case in cases:
            try:
                case.run()
            except BaseException as exc:
                failures.append({**_case_detail(case.case_id), "error": f"{type(exc).__name__}:{exc}"})
            else:
                facets[case.facet]["score"] += 1
        failed_ids = {item["case_id"] for item in failures}
        clusters = [
            {
                "id": cluster_id,
                "label": CLUSTER_LABELS[cluster_id],
                "case_ids": list(case_ids),
                "points": 0 if any(case_id in failed_ids for case_id in case_ids) else 1,
                "max_points": 1,
                "passed": not any(case_id in failed_ids for case_id in case_ids),
            }
            for cluster_id, case_ids in CLUSTERS.items()
        ]
        score = sum(int(cluster["points"]) for cluster in clusters)
        return {
            "status": "passed" if score == MAX_SCORE else "semantic_failed",
            "score": score,
            "max_score": MAX_SCORE,
            "failure_details": failures,
            "facets": facets,
            "clusters": clusters,
            "raw_score": RAW_MAX_SCORE - len(failures),
            "raw_max_score": RAW_MAX_SCORE,
        }
    finally:
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if name == "session_bundle" or name.startswith("session_bundle."):
                sys.modules.pop(name, None)


def grade_patch(patch_text: str) -> dict[str, object]:
    patch_format_ok = False
    patch_applies = False
    try:
        patched = _apply_patch(STARTER_FILES, patch_text)
        patch_format_ok = True
        patch_applies = True
        _validate_sources(patched)
        with tempfile.TemporaryDirectory(prefix="session-bundle-grade-") as temp:
            root = Path(temp)
            _materialize(patched, root)
            payload = _run_cases(root)
        payload.update({"patch_format_ok": True, "patch_applies": True})
        return payload
    except BaseException as exc:
        error = f"{type(exc).__name__}:{exc}"
        if "_not_unique:" in error:
            patch_format_ok = True
        return {
            "status": "patch_apply_failed" if not patch_applies else "runner_error",
            "score": 0,
            "max_score": MAX_SCORE,
            "failure_details": case_details(),
            "facets": {
                facet: {
                    "label": label,
                    "score": 0,
                    "max_score": sum(
                        1 for item_facet, _, _ in CASE_SPECS if item_facet == facet
                    ),
                }
                for facet, label in FACET_LABELS.items()
            },
            "clusters": [
                {
                    "id": cluster_id,
                    "label": CLUSTER_LABELS[cluster_id],
                    "case_ids": list(case_ids),
                    "points": 0,
                    "max_points": 1,
                    "passed": False,
                }
                for cluster_id, case_ids in CLUSTERS.items()
            ],
            "raw_score": 0,
            "raw_max_score": RAW_MAX_SCORE,
            "patch_format_ok": patch_format_ok,
            "patch_applies": patch_applies,
            "error": error,
        }
