from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    run_bounded_process,
)
from .frozen_runtime import python_code_worker_command


TEST_FILE = "test_session_store.py"
MAX_SCORE = 20
MAX_TEST_FILE_CHARACTERS = 32_768
MAX_ELIGIBLE_TEST_METHODS = 6
SINGLE_TEST_TIMEOUT_SECONDS = 2.5
COMBINED_TEST_TIMEOUT_SECONDS = 10.0
BLACK_BOX_OUTPUT_LIMIT_BYTES = 256 * 1024
SANDBOX_EXECUTABLE = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
REFERENCE_PATH = Path(__file__).with_name("black_box_session_store_reference.py")

SUBMITTED_TEST_RUNNER = """\
import sys
import types
import unittest

implementation_source = sys.stdin.buffer.read().decode("utf-8")
implementation = types.ModuleType("session_store")
implementation.__file__ = "<grader-provided>"
implementation.__package__ = ""
sys.modules["session_store"] = implementation
exec(compile(implementation_source, "session_store.py", "exec"), implementation.__dict__)
implementation_source = None
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:] or ["test_session_store"])
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""

STARTER_TEST_SOURCE = '''from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import session_store


def metadata(event_count: object = 1) -> dict[str, object]:
    return {
        "format": "session-bundle",
        "format_version": 1,
        "event_count": event_count,
    }


def event(seq: object = 1, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "type": "cell",
        "seq": seq,
        "code": "value = 1",
        "success": True,
        "execution_count": 1,
        "stdout": "",
        "stderr": "",
        "execute_result": {},
    }
    value.update(changes)
    return value


def failed_event(seq: int = 1) -> dict[str, object]:
    return event(
        seq,
        success=False,
        error={
            "ename": "RuntimeError",
            "evalue": "boom",
            "traceback": ["RuntimeError: boom"],
        },
    )


def write_archive(path: Path, meta: object, event_lines: list[object] | str) -> None:
    text = event_lines if isinstance(event_lines, str) else "".join(
        json.dumps(item) + "\\n" for item in event_lines
    )
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(meta))
        archive.writestr("events.jsonl", text)


class Result:
    def __init__(self, success: bool) -> None:
        self.success = success


class Shell:
    def __init__(self, results: list[bool], fail_at: int | None = None) -> None:
        self.results = iter(results)
        self.fail_at = fail_at
        self.execution_count = 40
        self.calls: list[tuple[str, bool]] = []

    def run_cell(self, code: str, *, store_history: bool):
        self.calls.append((code, store_history))
        self.execution_count += 1
        if self.fail_at == len(self.calls):
            raise RuntimeError("shell failed")
        return Result(next(self.results))


class SessionStoreRegressionTests(unittest.TestCase):
    def test_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "smoke.bundle"
            session_store.save_bundle(path, metadata(), [event()])
            self.assertEqual(session_store.validate_bundle(path), [])


if __name__ == "__main__":
    unittest.main()
'''


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    label: str


CASE_SPECS = (
    CaseSpec("overwrite_precedence", "拒绝覆盖优先级"),
    CaseSpec("metadata_preconsume_snapshot", "元数据预消费快照"),
    CaseSpec("snapshot_ownership", "逐事件快照时机"),
    CaseSpec("recursive_mapping_snapshot", "递归映射归一化"),
    CaseSpec("event_limit_consumption", "事件上限与停止消费"),
    CaseSpec("validation_failure_atomicity", "校验失败原子性"),
    CaseSpec("iteration_failure_atomicity", "迭代失败原子性"),
    CaseSpec("serialization_failure_atomicity", "序列化失败原子性"),
    CaseSpec("member_size_atomicity", "成员超限原子性"),
    CaseSpec("replace_failure_atomicity", "替换失败原子性"),
    CaseSpec("no_clobber_race", "无覆盖提交竞态"),
    CaseSpec("deterministic_encoding", "逻辑输入确定性编码"),
    CaseSpec("zip_layout", "归档成员顺序与时间戳"),
    CaseSpec("temporary_fsync", "临时归档先落盘"),
    CaseSpec("parent_fsync", "提交后目录落盘"),
    CaseSpec("actual_replay_result", "实际结果写入回放输出"),
    CaseSpec("stop_on_actual_failure", "按实际失败停止"),
    CaseSpec("continue_after_actual_failure", "实际失败后继续"),
    CaseSpec("store_history_forwarding", "历史参数透传"),
    CaseSpec("history_state_restoration", "历史状态逐次与异常恢复"),
)

MUTANT_CASE_IDS = {
    "consumes_despite_existing_target": "overwrite_precedence",
    "snapshots_metadata_after_events": "metadata_preconsume_snapshot",
    "late_event_snapshot": "snapshot_ownership",
    "skips_recursive_mapping_normalization": "recursive_mapping_snapshot",
    "overconsumes_event_limit": "event_limit_consumption",
    "deletes_target_on_validation_failure": "validation_failure_atomicity",
    "deletes_target_on_iteration_failure": "iteration_failure_atomicity",
    "deletes_target_on_serialization_failure": "serialization_failure_atomicity",
    "deletes_target_on_member_size_failure": "member_size_atomicity",
    "leaves_temp_file": "replace_failure_atomicity",
    "clobbers_racing_target": "no_clobber_race",
    "preserves_mapping_insertion_order": "deterministic_encoding",
    "uses_wall_clock_zip_timestamps": "zip_layout",
    "skips_temporary_archive_fsync": "temporary_fsync",
    "skips_parent_directory_fsync": "parent_fsync",
    "uses_recorded_replay_result": "actual_replay_result",
    "ignores_stop_on_error": "stop_on_actual_failure",
    "always_stops_on_failure": "continue_after_actual_failure",
    "hardcodes_store_history": "store_history_forwarding",
    "leaks_history_state": "history_state_restoration",
}


def _reference_source() -> str:
    configured_root = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
    if configured_root:
        external_reference = (
            Path(configured_root).expanduser()
            / "scanner"
            / "black_box_session_store_reference.py"
        )
        if external_reference.is_file():
            return external_reference.read_text(encoding="utf-8")
    return REFERENCE_PATH.read_text(encoding="utf-8")


def _is_standard_library_import(root: str) -> bool:
    if root in {"__future__", "session_store"}:
        return True
    if root in sys.builtin_module_names:
        return True
    if root in getattr(sys, "stdlib_module_names", ()):
        return True
    for key in ("stdlib", "platstdlib"):
        base = Path(sysconfig.get_paths().get(key, ""))
        if not base.is_dir():
            continue
        if (base / f"{root}.py").is_file() or (base / root / "__init__.py").is_file():
            return True
        extension_root = base / "lib-dynload"
        if extension_root.is_dir() and next(extension_root.glob(f"{root}.*"), None):
            return True
    return False


def _validate_test_source(source: str) -> None:
    if len(source) > MAX_TEST_FILE_CHARACTERS:
        raise ValueError("test_file_too_large")
    tree = ast.parse(source, filename=TEST_FILE)
    forbidden_imports = {
        "ctypes", "dis", "gc", "importlib", "inspect", "marshal", "multiprocessing",
        "pkgutil", "socket", "subprocess",
    }
    banned_calls = {
        "__import__", "compile", "eval", "exec", "input",
    }
    banned_attributes = {
        "__bases__", "__builtins__", "__code__", "__getattribute__", "__globals__",
        "__mro__", "__subclasses__", "execv", "execve", "execvp",
        "execvpe", "fork", "forkpty", "popen", "posix_spawn", "posix_spawnp", "spawnl",
        "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "system",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in forbidden_imports or not _is_standard_library_import(root):
                    raise ValueError(f"forbidden_test_import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                raise ValueError("forbidden_test_relative_import")
            module = (node.module or "").split(".", 1)[0]
            if module in forbidden_imports or not _is_standard_library_import(module):
                raise ValueError(f"forbidden_test_import:{node.module}")
            for alias in node.names:
                if alias.name in banned_attributes:
                    raise ValueError(f"forbidden_test_import_member:{alias.name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in banned_calls:
                raise ValueError(f"forbidden_test_call:{node.func.id}")
            if (
                node.func.id in {"delattr", "getattr", "setattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in banned_attributes
            ):
                raise ValueError(f"forbidden_test_attribute:{node.args[1].value}")
        elif isinstance(node, ast.Name) and node.id == "__builtins__":
            raise ValueError("forbidden_test_name:__builtins__")
        elif isinstance(node, ast.Attribute) and node.attr in banned_attributes:
            raise ValueError(f"forbidden_test_attribute:{node.attr}")


def _sandbox_literal(path: Path | str) -> str:
    return json.dumps(str(path), ensure_ascii=True)


def _sandbox_profile(
    root: Path,
    *,
    read_roots: tuple[Path, ...] = (),
    allow_process_fork: bool = False,
) -> str:
    runtime_root = Path(sys.base_prefix).resolve()
    scratch = root.resolve() / "scratch"
    executable = Path(sys.executable)
    executable_paths = {executable, executable.resolve()}
    runtime_parents = list(runtime_root.parents)
    if len(runtime_parents) >= 4:
        cellar_executable = runtime_parents[3] / "bin" / executable.name
        if cellar_executable.exists():
            executable_paths.add(cellar_executable)
    python_app = runtime_root / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    if python_app.exists():
        executable_paths.add(python_app)
    ancestor_paths: set[Path] = set()
    resolved_read_roots = {path.resolve() for path in read_roots}
    for path in {root.resolve(), runtime_root, *executable_paths, *resolved_read_roots}:
        ancestor_paths.update(path.parents)
    directory_reads = " ".join(
        f"(literal {_sandbox_literal(path)})"
        for path in sorted(ancestor_paths, key=lambda item: str(item))
    )
    runtime_reads = " ".join(
        f"(subpath {_sandbox_literal(path)})"
        for path in (
            root.resolve(),
            runtime_root,
            *sorted(resolved_read_roots, key=lambda item: str(item)),
            Path("/System"),
            Path("/usr/lib"),
            Path("/usr/share"),
        )
    )
    executable_reads = " ".join(
        f"(literal {_sandbox_literal(path)})"
        for path in sorted(executable_paths, key=lambda item: str(item))
    )
    process_fork = "(allow process-fork)" if allow_process_fork else ""
    return "".join(
        (
            "(version 1)",
            "(deny default)",
            f"(allow file-read-metadata {directory_reads} {runtime_reads} "
            f'{executable_reads} (literal "/dev/null") (literal "/dev/urandom"))',
            f"(allow file-read-data {directory_reads})",
            f"(allow file-read* {runtime_reads} {executable_reads} "
            '(literal "/dev/null") (literal "/dev/urandom"))',
            f"(allow file-write* (subpath {_sandbox_literal(scratch)}))",
            process_fork,
            f"(allow process-exec {executable_reads})",
            "(allow process-info*)",
            "(allow sysctl-read)",
        )
    )


def _sandbox_environment(root: Path) -> dict[str, str]:
    scratch = str(root.resolve() / "scratch")
    return {
        "HOME": scratch, "LANG": "C", "LC_ALL": "C", "PATH": "",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1",
        "TEMP": scratch, "TMP": scratch, "TMPDIR": scratch,
    }


def _run_submitted_tests(
    implementation_source: str,
    test_source: str,
    test_ids: list[str],
) -> tuple[bool, str]:
    if SANDBOX_EXECUTABLE is None:
        return False, "sandbox_unavailable"
    with tempfile.TemporaryDirectory(prefix="q1-proof-suite-") as temp:
        root = Path(temp)
        (root / "scratch").mkdir()
        (root / TEST_FILE).write_text(test_source, encoding="utf-8")
        command = [
            SANDBOX_EXECUTABLE,
            "-p",
            _sandbox_profile(root),
            *python_code_worker_command(SUBMITTED_TEST_RUNNER, *test_ids),
        ]
        try:
            completed = run_bounded_process(
                command,
                cwd=str(root),
                env=_sandbox_environment(root),
                input=implementation_source.encode("utf-8"),
                timeout=(
                    SINGLE_TEST_TIMEOUT_SECONDS
                    if len(test_ids) <= 1
                    else COMBINED_TEST_TIMEOUT_SECONDS
                ),
                output_limit_bytes=BLACK_BOX_OUTPUT_LIMIT_BYTES,
                merge_stderr=True,
                runner=subprocess.run,
            )
            output = completed.stdout or b""
            if isinstance(output, str):
                output = output.encode("utf-8", errors="replace")
            detail = output[-2_000:].decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            return False, "test_timeout"
        except BoundedSubprocessOutputError:
            return False, "sandbox_unavailable:output_limit_exceeded"
        except OSError as exc:
            return False, f"sandbox_unavailable:{type(exc).__name__}"
    return completed.returncode == 0, detail


def _discover_test_ids(source: str) -> list[str]:
    tree = ast.parse(source, filename=TEST_FILE)
    test_ids: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                test_ids.append(f"test_session_store.{node.name}.{item.name}")
    return test_ids


def _mutant_transforms() -> dict[str, tuple[str, str]]:
    return {
        "consumes_despite_existing_target": (
            "    if bundle_path.exists() and not overwrite:\n",
            "    if False and bundle_path.exists() and not overwrite:\n",
        ),
        "snapshots_metadata_after_events": (
            "    metadata_to_write = _snapshot(metadata)\n"
            "    events_to_write: list[object] = []\n"
            "    try:\n",
            "    metadata_to_write = metadata\n"
            "    events_to_write: list[object] = []\n"
            "    try:\n",
        ),
        "late_event_snapshot": (
            "            events_to_write.append(_snapshot(event))\n"
            "    except BaseException:\n"
            "        raise\n"
            "    errors = _schema_errors(metadata_to_write, events_to_write)",
            "            events_to_write.append(event)\n"
            "    except BaseException:\n"
            "        raise\n"
            "    events_to_write = [_snapshot(event) for event in events_to_write]\n"
            "    metadata_to_write = _snapshot(metadata_to_write)\n"
            "    errors = _schema_errors(metadata_to_write, events_to_write)",
        ),
        "skips_recursive_mapping_normalization": (
            "    if isinstance(value, MappingABC):\n"
            "        return {\n"
            "            copy.deepcopy(key): _snapshot(item)\n"
            "            for key, item in value.items()\n"
            "        }",
            "    if isinstance(value, MappingABC):\n"
            "        return copy.deepcopy(value)",
        ),
        "overconsumes_event_limit": (
            "            if len(events_to_write) >= _MAX_EVENTS:\n",
            "            if len(events_to_write) > _MAX_EVENTS:\n",
        ),
        "deletes_target_on_validation_failure": (
            "    if errors:\n"
            "        raise BundleValidationError(bundle_path, errors)\n\n"
            "    try:\n"
            "        metadata_text = json.dumps(metadata_to_write, allow_nan=False, sort_keys=True)",
            "    if errors:\n"
            "        bundle_path.unlink(missing_ok=True)\n"
            "        raise BundleValidationError(bundle_path, errors)\n\n"
            "    try:\n"
            "        metadata_text = json.dumps(metadata_to_write, allow_nan=False, sort_keys=True)",
        ),
        "deletes_target_on_iteration_failure": (
            "    except BaseException:\n"
            "        raise\n"
            "    errors = _schema_errors(metadata_to_write, events_to_write)",
            "    except BaseException:\n"
            "        bundle_path.unlink(missing_ok=True)\n"
            "        raise\n"
            "    errors = _schema_errors(metadata_to_write, events_to_write)",
        ),
        "deletes_target_on_serialization_failure": (
            "    except BaseException:\n"
            "        raise\n"
            "    size_errors: list[str] = []",
            "    except BaseException:\n"
            "        bundle_path.unlink(missing_ok=True)\n"
            "        raise\n"
            "    size_errors: list[str] = []",
        ),
        "deletes_target_on_member_size_failure": (
            "    if size_errors:\n"
            "        raise BundleValidationError(bundle_path, size_errors)\n",
            "    if size_errors:\n"
            "        bundle_path.unlink(missing_ok=True)\n"
            "        raise BundleValidationError(bundle_path, size_errors)\n",
        ),
        "leaves_temp_file": (
            "    except BaseException:\n"
            "        temporary_path.unlink(missing_ok=True)\n"
            "        raise\n",
            "    except BaseException:\n"
            "        raise\n",
        ),
        "clobbers_racing_target": (
            "            os.link(temporary_path, bundle_path)\n"
            "            temporary_path.unlink()\n",
            "            os.replace(temporary_path, bundle_path)\n",
        ),
        "preserves_mapping_insertion_order": (
            "        metadata_text = json.dumps(metadata_to_write, allow_nan=False, sort_keys=True)\n"
            "        events_text = \"\".join(\n"
            "            json.dumps(event, allow_nan=False, sort_keys=True) + \"\\n\"\n",
            "        metadata_text = json.dumps(metadata_to_write, allow_nan=False, sort_keys=False)\n"
            "        events_text = \"\".join(\n"
            "            json.dumps(event, allow_nan=False, sort_keys=False) + \"\\n\"\n",
        ),
        "uses_wall_clock_zip_timestamps": (
            "def _zip_info(name: str) -> ZipInfo:\n"
            "    info = ZipInfo(name, date_time=_ZIP_DATE_TIME)\n"
            "    info.compress_type = ZIP_DEFLATED\n"
            "    info.create_system = 0\n"
            "    info.external_attr = 0\n"
            "    return info",
            "def _zip_info(name: str) -> str:\n"
            "    return name",
        ),
        "skips_temporary_archive_fsync": (
            "            os.fsync(handle.fileno())\n",
            "            pass\n",
        ),
        "skips_parent_directory_fsync": (
            "def _sync_parent(path: Path) -> None:\n"
            "    try:\n"
            "        descriptor = os.open(path.parent, os.O_RDONLY)\n"
            "    except OSError:\n"
            "        return\n"
            "    try:\n"
            "        try:\n"
            "            os.fsync(descriptor)\n"
            "        except OSError:\n"
            "            pass\n"
            "    finally:\n"
            "        os.close(descriptor)",
            "def _sync_parent(path: Path) -> None:\n"
            "    del path\n"
            "    return None",
        ),
        "uses_recorded_replay_result": (
            "            success = bool(result.success)\n",
            "            success = bool(event[\"success\"])\n",
        ),
        "ignores_stop_on_error": (
            "    should_stop = stop_on_error\n",
            "    should_stop = False\n",
        ),
        "always_stops_on_failure": (
            "            if should_stop and not success:\n",
            "            if not success:\n",
        ),
        "hardcodes_store_history": (
            "            result = shell.run_cell(event[\"code\"], store_history=store_history)\n",
            "            result = shell.run_cell(event[\"code\"], store_history=True)\n",
        ),
        "leaks_history_state": (
            "            if not store_history:\n"
            "                shell.execution_count = original_execution_count\n",
            "",
        ),
    }


def _mutant_sources() -> dict[str, str]:
    reference = _reference_source()
    sources: dict[str, str] = {}
    for name, (old, new) in _mutant_transforms().items():
        if reference.count(old) != 1:
            raise AssertionError(f"mutation source drift:{name}:{reference.count(old)}")
        sources[name] = reference.replace(old, new, 1)
    return sources


def _empty_result(status: str, error: str) -> dict[str, object]:
    return {
        "status": status,
        "score": 0,
        "max_score": MAX_SCORE,
        "failure_summary": error,
        "score_details": [
            {
                "id": spec.case_id,
                "label": spec.label,
                "points": 0,
                "max_points": 1,
                "passed": False,
            }
            for spec in CASE_SPECS
        ],
        "survived_mutants": list(MUTANT_CASE_IDS),
    }


def _apply_response_patch(response: str) -> tuple[str, int]:
    from .session_bundle_grader import _apply_patch

    envelopes = re.findall(
        r"(?ms)^\*\*\* Begin Patch\s*$.*?^\*\*\* End Patch\s*$",
        response,
    )
    sources = {TEST_FILE: STARTER_TEST_SOURCE}
    for envelope in envelopes or [response]:
        sources = _apply_patch(sources, envelope)
    return sources[TEST_FILE], len(envelopes) or 1


def grade_response(response: str) -> dict[str, object]:
    try:
        test_source, envelope_count = _apply_response_patch(response)
    except BaseException as exc:
        return _empty_result("patch_apply_failed", f"{type(exc).__name__}:{exc}")

    try:
        _validate_test_source(test_source)
        discovered = _discover_test_ids(test_source)
        if not discovered:
            raise ValueError("no_unittest_test_methods")
    except BaseException as exc:
        return _empty_result("submission_validation_failed", f"{type(exc).__name__}:{exc}")

    test_ids = discovered[:MAX_ELIGIBLE_TEST_METHODS]
    ignored_tests = discovered[MAX_ELIGIBLE_TEST_METHODS:]
    reference = _reference_source()
    valid_tests: list[str] = []
    invalid_tests: list[dict[str, str]] = []
    for test_id in test_ids:
        passed, detail = _run_submitted_tests(reference, test_source, [test_id])
        if not passed:
            if detail.startswith("sandbox_unavailable"):
                unavailable = _empty_result("grader_unavailable", detail)
                unavailable["score"] = None
                return unavailable
            invalid_tests.append({"test_id": test_id, "reference_error": detail})
            continue
        combined_ids = [*valid_tests, test_id]
        if valid_tests:
            combined_passed, combined_detail = _run_submitted_tests(
                reference,
                test_source,
                combined_ids,
            )
            if not combined_passed:
                if combined_detail.startswith("sandbox_unavailable"):
                    unavailable = _empty_result("grader_unavailable", combined_detail)
                    unavailable["score"] = None
                    return unavailable
                invalid_tests.append(
                    {
                        "test_id": test_id,
                        "reference_error": f"combined_reference_error:{combined_detail}",
                    }
                )
                continue
        valid_tests.append(test_id)

    killed_cases: set[str] = set()
    killed_by: dict[str, str] = {}
    if valid_tests:
        for mutant_id, mutant_source in _mutant_sources().items():
            passed, detail = _run_submitted_tests(mutant_source, test_source, valid_tests)
            if detail.startswith("sandbox_unavailable"):
                unavailable = _empty_result("grader_unavailable", detail)
                unavailable["score"] = None
                return unavailable
            if not passed:
                case_id = MUTANT_CASE_IDS[mutant_id]
                killed_cases.add(case_id)
                killed_by[case_id] = mutant_id

    score_details = [
        {
            "id": spec.case_id,
            "label": spec.label,
            "core_scenarios": ["测试验证", "调试修复"],
            "regression_killed": spec.case_id in killed_cases,
            "scoring_mode": "regression_proof",
            "points": int(spec.case_id in killed_cases),
            "max_points": 1,
            "passed": spec.case_id in killed_cases,
        }
        for spec in CASE_SPECS
    ]
    score = sum(int(item["points"]) for item in score_details)
    survived = [
        mutant_id
        for mutant_id, case_id in MUTANT_CASE_IDS.items()
        if case_id not in killed_cases
    ]
    return {
        "status": "passed" if score == MAX_SCORE else "semantic_failed",
        "score": score,
        "max_score": MAX_SCORE,
        "score_details": score_details,
        "killed_mutants": [item for item in MUTANT_CASE_IDS if item not in survived],
        "survived_mutants": survived,
        "failure_summary": ",".join(survived),
        "regression_proof": {
            "valid": True,
            "valid_tests": valid_tests,
            "invalid_tests": invalid_tests,
            "ignored_tests": ignored_tests,
            "killed_by": killed_by,
        },
        "patch_envelopes": envelope_count,
    }


__all__ = ["MAX_SCORE", "STARTER_TEST_SOURCE", "grade_response"]
