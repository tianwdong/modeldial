from __future__ import annotations


STARTER_SOURCE = '''from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Iterable, Mapping
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


_MEMBERS = ("metadata.json", "events.jsonl")


class BundleValidationError(ValueError):
    def __init__(self, bundle_path: str | Path, errors: Iterable[str]) -> None:
        self.bundle_path = str(bundle_path)
        self.errors = tuple(errors)
        super().__init__("invalid bundle")


def _exact_int(value: object) -> bool:
    return isinstance(value, int)


def _schema_errors(metadata: object, events: list[object]) -> list[str]:
    errors: list[str] = []
    if not isinstance(metadata, MappingABC):
        errors.append("metadata must be an object")
    else:
        if metadata.get("format") != "session-bundle":
            errors.append("metadata format must be session-bundle")
        version = metadata.get("format_version")
        if not _exact_int(version) or version < 1:
            errors.append("format_version must be an integer >= 1")
        if "event_count" in metadata:
            count = metadata["event_count"]
            if not _exact_int(count) or count != len(events):
                errors.append("event_count must equal the number of events")

    sequences: list[int] = []
    for index, event in enumerate(events, start=1):
        prefix = f"event {index}"
        if not isinstance(event, MappingABC):
            errors.append(f"{prefix} must be an object")
            continue
        if event.get("type") != "cell":
            errors.append(f"{prefix} type must be cell")
        seq = event.get("seq")
        if _exact_int(seq):
            sequences.append(seq)
        else:
            errors.append(f"{prefix} seq must be an integer")
        if not isinstance(event.get("code"), str):
            errors.append(f"{prefix} code must be a string")
        success = event.get("success")
        if type(success) is not bool:
            errors.append(f"{prefix} success must be a boolean")
        execution_count = event.get("execution_count")
        if execution_count is not None and not _exact_int(execution_count):
            errors.append(f"{prefix} execution_count must be an integer or null")
        if not isinstance(event.get("stdout"), str):
            errors.append(f"{prefix} stdout must be a string")
        if not isinstance(event.get("stderr"), str):
            errors.append(f"{prefix} stderr must be a string")
        result = event.get("execute_result")
        if not isinstance(result, MappingABC):
            errors.append(f"{prefix} execute_result must be an object")
        elif result and not isinstance(result.get("text/plain"), str):
            errors.append(f"{prefix} execute_result text/plain must be a string")
        if success is False:
            error = event.get("error")
            if not isinstance(error, MappingABC):
                errors.append(f"{prefix} error must be an object")
            else:
                if not isinstance(error.get("ename"), str):
                    errors.append(f"{prefix} error ename must be a string")
                if not isinstance(error.get("evalue"), str):
                    errors.append(f"{prefix} error evalue must be a string")
                traceback = error.get("traceback")
                if not (
                    isinstance(traceback, list)
                    and traceback
                    and all(isinstance(line, str) for line in traceback)
                ):
                    errors.append(f"{prefix} error traceback must be a non-empty string list")
    if sorted(sequences) != list(range(1, len(events) + 1)):
        errors.append("event sequence is not contiguous")
    return errors


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant: {value}")


def _loads(text: str) -> object:
    return json.loads(text, parse_constant=_reject_constant)


def _read_bundle(path: Path) -> tuple[object, list[object], list[str]]:
    errors: list[str] = []
    metadata: object = None
    events: list[object] = []
    try:
        with ZipFile(path, "r") as archive:
            names = archive.namelist()
            for member in _MEMBERS:
                count = names.count(member)
                if count != 1:
                    errors.append(f"bundle must contain exactly one {member}; found {count}")
            unexpected = [name for name in names if name not in _MEMBERS]
            if unexpected:
                errors.append("unexpected members: " + ", ".join(unexpected))
            if names.count("metadata.json") >= 1:
                try:
                    metadata = _loads(archive.read("metadata.json").decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    errors.append(f"metadata.json is invalid: {exc}")
            if names.count("events.jsonl") >= 1:
                try:
                    text = archive.read("events.jsonl").decode("utf-8")
                except UnicodeDecodeError as exc:
                    errors.append(f"events.jsonl is not UTF-8: {exc}")
                else:
                    for line_number, line in enumerate(text.splitlines(), start=1):
                        if not line.strip():
                            errors.append(f"events.jsonl line {line_number} is blank")
                            continue
                        try:
                            events.append(_loads(line))
                        except ValueError as exc:
                            errors.append(f"events.jsonl line {line_number} is invalid: {exc}")
    except (FileNotFoundError, BadZipFile, OSError) as exc:
        errors.append(f"bundle cannot be read: {exc}")
    errors.extend(_schema_errors(metadata, events))
    return metadata, events, errors


def validate_bundle(path: str | Path, *, strict: bool = True) -> list[str]:
    bundle_path = Path(path)
    _, _, errors = _read_bundle(bundle_path)
    if strict and errors:
        raise BundleValidationError(bundle_path, errors)
    return errors


def load_bundle(path: str | Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    bundle_path = Path(path)
    metadata, events, errors = _read_bundle(bundle_path)
    if errors:
        raise BundleValidationError(bundle_path, errors)
    return metadata, events


def save_bundle(
    path: str | Path,
    metadata: Mapping[str, object],
    events: Iterable[Mapping[str, object]],
    *,
    overwrite: bool = False,
) -> Path:
    bundle_path = Path(path)
    if bundle_path.exists() and not overwrite:
        raise FileExistsError(bundle_path)
    raw_events = list(events)
    metadata_to_write = dict(metadata)
    events_to_write = [dict(event) if isinstance(event, MappingABC) else event for event in raw_events]
    errors = _schema_errors(metadata_to_write, events_to_write)
    if errors:
        raise BundleValidationError(bundle_path, errors)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{bundle_path.name}.", suffix=".tmp", dir=bundle_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata_to_write, sort_keys=True))
        archive.writestr(
            "events.jsonl",
            "".join(json.dumps(event, sort_keys=True) + "\\n" for event in events_to_write),
        )
    os.replace(temporary_path, bundle_path)
    return bundle_path


def replay_bundle(
    shell: object,
    path: str | Path,
    *,
    stop_on_error: bool = True,
    store_history: bool = True,
) -> list[dict[str, object]]:
    _, events = load_bundle(path)
    original_execution_count = shell.execution_count
    outcomes: list[dict[str, object]] = []
    for event in events:
        shell.run_cell(event["code"], store_history=True)
        success = bool(event["success"])
        outcomes.append({"seq": event["seq"], "success": success})
        if stop_on_error and not success:
            break
    if not store_history:
        shell.execution_count = original_execution_count
    return outcomes
'''
