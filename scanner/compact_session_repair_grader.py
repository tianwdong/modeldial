from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile

from .compact_session_repair_starter import STARTER_SOURCE
from .session_bundle_grader import _apply_patch, _validate_sources


SOURCE_FILE = "session_store.py"
STARTER_FILES = {SOURCE_FILE: STARTER_SOURCE}
CLUSTER_LABELS = {
    "archive_cardinality": "归档成员基数",
    "json_diagnostics": "JSON 与物理行诊断",
    "schema_order": "精确类型与物理顺序",
    "nested_aggregation": "嵌套结构与错误聚合",
    "validation_contract": "验证异常契约",
    "overwrite_precedence": "拒绝覆盖优先级",
    "snapshot_ownership": "单次迭代与输入所有权",
    "atomic_failure": "事务失败恢复",
    "actual_replay": "实际回放结果",
    "history_restoration": "历史计数异常恢复",
}
MAX_SCORE = len(CLUSTER_LABELS)


@dataclass(frozen=True)
class Case:
    case_id: str
    run: Callable[[object], None]


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
        event["error"] = {
            "ename": "RuntimeError",
            "evalue": "boom",
            "traceback": ["RuntimeError: boom"],
        }
    return event


def _write_archive(
    path: Path,
    metadata: object,
    event_lines: list[object] | str,
    *,
    duplicate_metadata: bool = False,
    unexpected_member: bool = False,
    omit_events: bool = False,
) -> None:
    event_text = (
        event_lines
        if isinstance(event_lines, str)
        else "".join(json.dumps(event) + "\n" for event in event_lines)
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata))
            if duplicate_metadata:
                archive.writestr("metadata.json", json.dumps(metadata))
            if not omit_events:
                archive.writestr("events.jsonl", event_text)
            if unexpected_member:
                archive.writestr("notes.txt", "unexpected")


def _expect_raises(expected: type[BaseException], operation: Callable[[], object]):
    try:
        operation()
    except expected as exc:
        return exc
    except BaseException as exc:
        raise AssertionError(
            f"expected {expected.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {expected.__name__}")


class _Result:
    def __init__(self, success: bool) -> None:
        self.success = success


class _Shell:
    def __init__(self, results: list[bool], *, fail_at: int | None = None) -> None:
        self.results = iter(results)
        self.fail_at = fail_at
        self.execution_count = 40
        self.calls: list[tuple[str, bool]] = []

    def run_cell(self, code: str, *, store_history: bool):
        self.calls.append((code, store_history))
        self.execution_count += 1
        if self.fail_at == len(self.calls):
            raise RuntimeError("shell failed")
        return _Result(next(self.results))


def _build_cases() -> list[Case]:
    cases: list[Case] = []

    def register(case_id: str):
        def decorator(operation: Callable[[object], None]):
            cases.append(Case(case_id, operation))
            return operation

        return decorator

    @register("archive_cardinality")
    def archive_cardinality(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            duplicate = root / "duplicate.bundle"
            missing = root / "missing.bundle"
            unexpected = root / "unexpected.bundle"
            _write_archive(duplicate, _metadata(event_count=0), [], duplicate_metadata=True)
            _write_archive(missing, _metadata(event_count=0), [], omit_events=True)
            _write_archive(unexpected, _metadata(event_count=0), [], unexpected_member=True)
            assert module.validate_bundle(duplicate, strict=False)
            assert module.validate_bundle(missing, strict=False)
            assert module.validate_bundle(unexpected, strict=False)

    @register("json_diagnostics")
    def json_diagnostics(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "invalid.bundle"
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "metadata.json",
                    '{"format":"session-bundle","format_version":1,"extra":NaN}',
                )
                archive.writestr("events.jsonl", "{not-json}\n\n")
            errors = module.validate_bundle(path, strict=False)
            joined = "\n".join(errors)
            assert "metadata.json" in joined and "non-standard" in joined
            assert "events.jsonl line 1" in joined and "invalid" in joined
            assert "line 2" in joined and "blank" in joined

            physical = root / "physical-lines.bundle"
            with ZipFile(physical, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("metadata.json", json.dumps(_metadata(event_count=2)))
                archive.writestr(
                    "events.jsonl",
                    "{not-json}\n" + json.dumps(_event(seq=2)) + "\n",
                )
            physical_joined = "\n".join(module.validate_bundle(physical, strict=False))
            assert "events.jsonl line 1" in physical_joined
            assert "event_count must equal" not in physical_joined
            assert "event 1 seq" not in physical_joined
            assert "event sequence is not contiguous" not in physical_joined

    @register("schema_order")
    def schema_order(module: object) -> None:
        variants = [
            ({"format": "session-bundle", "format_version": True}, [_event()]),
            (_metadata(event_count=True), [_event()]),
            (_metadata(event_count=1), [_event(seq=True)]),
            (_metadata(event_count=1), [_event(execution_count=True)]),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, (metadata, events) in enumerate(variants):
                path = root / f"bool-{index}.bundle"
                _write_archive(path, metadata, events)
                assert module.validate_bundle(path, strict=False)
            swapped = root / "swapped.bundle"
            _write_archive(swapped, _metadata(event_count=2), [_event(2), _event(1)])
            assert module.validate_bundle(swapped, strict=False)

    @register("nested_aggregation")
    def nested_aggregation(module: object) -> None:
        event = _event(
            seq=2,
            code=7,
            success=False,
            execution_count="bad",
            execute_result={"image/png": "abc"},
        )
        event["stdout"] = []
        event["stderr"] = None
        event["error"] = {"ename": 7, "evalue": None, "traceback": []}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "many.bundle"
            _write_archive(path, {}, [event])
            errors = module.validate_bundle(path, strict=False)
            joined = "\n".join(errors)
            for fragment in (
                "format",
                "format_version",
                "seq",
                "code",
                "execution_count",
                "stdout",
                "stderr",
                "text/plain",
                "ename",
                "evalue",
                "traceback",
            ):
                assert fragment in joined
            assert len(errors) >= 11

    @register("validation_contract")
    def validation_contract(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            invalid = root / "invalid.bundle"
            _write_archive(invalid, {}, [])
            errors = module.validate_bundle(invalid, strict=False)
            exc = _expect_raises(
                module.BundleValidationError,
                lambda: module.validate_bundle(str(invalid), strict=True),
            )
            assert exc.bundle_path == invalid
            assert type(exc.errors) is list and exc.errors == errors
            unreadable = root / "not-a-zip.bundle"
            unreadable.write_text("not a zip", encoding="utf-8")
            read_errors = module.validate_bundle(unreadable, strict=False)
            assert read_errors and all(isinstance(error, str) for error in read_errors)
            _expect_raises(module.BundleValidationError, lambda: module.load_bundle(unreadable))

    @register("overwrite_precedence")
    def overwrite_precedence(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.bundle"
            original = b"keep-me"
            path.write_bytes(original)

            def events():
                raise AssertionError("events consumed")
                yield _event()

            _expect_raises(
                FileExistsError,
                lambda: module.save_bundle(path, {}, events(), overwrite=False),
            )
            assert path.read_bytes() == original

    @register("snapshot_ownership")
    def snapshot_ownership(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "owned.bundle"
            metadata = _metadata(event_count=2)
            reusable = _event(1, code="first", execute_result={"text/plain": "first"})
            original_metadata = dict(metadata)

            def events():
                yield reusable
                reusable["execute_result"]["text/plain"] = "second"
                reusable.clear()
                reusable.update(
                    _event(2, code="second", execute_result={"text/plain": "second"})
                )
                yield reusable

            module.save_bundle(path, metadata, events())
            _, loaded = module.load_bundle(path)
            assert [event["code"] for event in loaded] == ["first", "second"]
            assert [event["execute_result"]["text/plain"] for event in loaded] == [
                "first",
                "second",
            ]
            assert metadata == original_metadata
            assert reusable == _event(
                2,
                code="second",
                execute_result={"text/plain": "second"},
            )

    @register("atomic_failure")
    def atomic_failure(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "existing.bundle"
            original = b"original"
            path.write_bytes(original)
            _expect_raises(
                module.BundleValidationError,
                lambda: module.save_bundle(path, {}, [], overwrite=True),
            )
            assert path.read_bytes() == original
            metadata = {**_metadata(event_count=0), "extension": {1, 2}}
            _expect_raises(
                TypeError,
                lambda: module.save_bundle(path, metadata, [], overwrite=True),
            )
            assert path.read_bytes() == original
            assert list(root.glob(f".{path.name}.*.tmp")) == []

    @register("actual_replay")
    def actual_replay(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "actual-failure.bundle"
            module.save_bundle(
                first,
                _metadata(event_count=2),
                [_event(1, code="first"), _event(2, code="second")],
            )
            shell = _Shell([False, True])
            outcomes = module.replay_bundle(shell, first, stop_on_error=True)
            assert shell.calls == [("first", True)]
            assert outcomes == [{"seq": 1, "success": False}]

            second = root / "recorded-failure.bundle"
            module.save_bundle(
                second,
                _metadata(event_count=2),
                [_event(1, code="first", success=False), _event(2, code="second")],
            )
            shell = _Shell([True, True])
            outcomes = module.replay_bundle(shell, second, stop_on_error=True)
            assert shell.calls == [("first", True), ("second", True)]
            assert outcomes == [
                {"seq": 1, "success": True},
                {"seq": 2, "success": True},
            ]

    @register("history_restoration")
    def history_restoration(module: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.bundle"
            module.save_bundle(
                path,
                _metadata(event_count=2),
                [_event(1, code="first"), _event(2, code="second")],
            )
            shell = _Shell([True], fail_at=2)
            _expect_raises(
                RuntimeError,
                lambda: module.replay_bundle(shell, path, store_history=False),
            )
            assert shell.execution_count == 40
            assert shell.calls == [("first", False), ("second", False)]

    assert [case.case_id for case in cases] == list(CLUSTER_LABELS)
    return cases


def _load_module(path: Path):
    module_name = "_compact_session_repair_candidate"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load candidate")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _apply_candidate_patch(patch_text: str) -> dict[str, str]:
    envelopes = re.findall(
        r"(?ms)^\*\*\* Begin Patch\s*$.*?^\*\*\* End Patch\s*$",
        patch_text,
    )
    sources = dict(STARTER_FILES)
    for envelope in envelopes or [patch_text]:
        sources = _apply_patch(sources, envelope)
    return sources


def _empty_payload(
    status: str,
    error: str,
    *,
    patch_format_ok: bool,
    patch_applies: bool,
) -> dict[str, object]:
    return {
        "status": status,
        "score": 0,
        "max_score": MAX_SCORE,
        "failure_details": [
            {
                "case_id": case_id,
                "label": label,
                "category": case_id,
                "category_label": label,
            }
            for case_id, label in CLUSTER_LABELS.items()
        ],
        "facets": {},
        "clusters": [
            {
                "id": case_id,
                "label": label,
                "case_ids": [case_id],
                "points": 0,
                "max_points": 1,
                "passed": False,
            }
            for case_id, label in CLUSTER_LABELS.items()
        ],
        "raw_score": 0,
        "raw_max_score": MAX_SCORE,
        "patch_format_ok": patch_format_ok,
        "patch_applies": patch_applies,
        "error": error,
    }


def grade_patch(patch_text: str) -> dict[str, object]:
    try:
        patched = _apply_candidate_patch(patch_text)
    except BaseException as exc:
        return _empty_payload(
            "patch_apply_failed",
            f"{type(exc).__name__}:{exc}",
            patch_format_ok="not_unique" in str(exc),
            patch_applies=False,
        )
    try:
        _validate_sources(patched)
        with tempfile.TemporaryDirectory(prefix="compact-session-repair-grade-") as temp:
            root = Path(temp)
            path = root / SOURCE_FILE
            path.write_text(patched[SOURCE_FILE], encoding="utf-8")
            module = _load_module(path)
            failures: list[dict[str, str]] = []
            clusters: list[dict[str, object]] = []
            for case in _build_cases():
                try:
                    case.run(module)
                except BaseException as exc:
                    failures.append(
                        {
                            "case_id": case.case_id,
                            "label": CLUSTER_LABELS[case.case_id],
                            "category": case.case_id,
                            "category_label": CLUSTER_LABELS[case.case_id],
                            "error": f"{type(exc).__name__}:{exc}",
                        }
                    )
                    points = 0
                else:
                    points = 1
                clusters.append(
                    {
                        "id": case.case_id,
                        "label": CLUSTER_LABELS[case.case_id],
                        "case_ids": [case.case_id],
                        "points": points,
                        "max_points": 1,
                        "passed": points == 1,
                    }
                )
    except BaseException as exc:
        return _empty_payload(
            "runner_error",
            f"{type(exc).__name__}:{exc}",
            patch_format_ok=True,
            patch_applies=True,
        )
    finally:
        sys.modules.pop("_compact_session_repair_candidate", None)

    score = sum(int(cluster["points"]) for cluster in clusters)
    return {
        "status": "passed" if score == MAX_SCORE else "semantic_failed",
        "score": score,
        "max_score": MAX_SCORE,
        "failure_details": failures,
        "facets": {},
        "clusters": clusters,
        "raw_score": score,
        "raw_max_score": MAX_SCORE,
        "patch_format_ok": True,
        "patch_applies": True,
    }
