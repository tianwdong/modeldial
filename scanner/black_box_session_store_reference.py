from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Iterable, Mapping
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo


_MEMBERS = ("metadata.json", "events.jsonl")
_MAX_MEMBER_BYTES = 262_144
_MAX_EVENTS = 1_000
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


class BundleValidationError(ValueError):
    def __init__(self, bundle_path: str | Path, errors: Iterable[str]) -> None:
        self.bundle_path = Path(bundle_path)
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _exact_int(value: object) -> bool:
    return type(value) is int


def _schema_errors(
    metadata: object,
    events: list[object],
    *,
    event_positions: list[int] | None = None,
    physical_event_count: int | None = None,
) -> list[str]:
    errors: list[str] = []
    if event_positions is None:
        event_positions = list(range(1, len(events) + 1))
    if physical_event_count is None:
        physical_event_count = len(events)
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
            if not _exact_int(count) or count != physical_event_count:
                errors.append("event_count must equal the number of events")

    for index, event in zip(event_positions, events):
        prefix = f"event {index}"
        if not isinstance(event, MappingABC):
            errors.append(f"{prefix} must be an object")
            continue
        if event.get("type") != "cell":
            errors.append(f"{prefix} type must be cell")
        seq = event.get("seq")
        if not _exact_int(seq) or seq != index:
            errors.append(f"{prefix} seq must be {index}")
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
    return errors


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _loads(text: str) -> object:
    return json.loads(
        text,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_pairs,
    )


def _read_bundle(path: Path) -> tuple[object, list[object], list[str]]:
    errors: list[str] = []
    metadata: object = None
    events: list[object] = []
    event_positions: list[int] = []
    physical_event_count = 0
    try:
        with ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            for member in _MEMBERS:
                count = names.count(member)
                if count != 1:
                    errors.append(f"bundle must contain exactly one {member}; found {count}")
            unexpected = [name for name in names if name not in _MEMBERS]
            if unexpected:
                errors.append("unexpected members: " + ", ".join(unexpected))

            metadata_infos = [info for info in infos if info.filename == "metadata.json"]
            metadata_info = metadata_infos[0] if len(metadata_infos) == 1 else None
            if metadata_info is not None and metadata_info.file_size > _MAX_MEMBER_BYTES:
                errors.append("metadata.json exceeds the member size limit")
                metadata_info = None
            if metadata_info is not None:
                try:
                    metadata = _loads(archive.read(metadata_info).decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    errors.append(f"metadata.json is invalid: {exc}")

            event_infos = [info for info in infos if info.filename == "events.jsonl"]
            event_info = event_infos[0] if len(event_infos) == 1 else None
            if event_info is not None and event_info.file_size > _MAX_MEMBER_BYTES:
                errors.append("events.jsonl exceeds the member size limit")
                event_info = None
            if event_info is not None:
                try:
                    text = archive.read(event_info).decode("utf-8")
                except UnicodeDecodeError as exc:
                    errors.append(f"events.jsonl is not UTF-8: {exc}")
                else:
                    lines = text.splitlines()
                    physical_event_count = len(lines)
                    if physical_event_count > _MAX_EVENTS:
                        errors.append("events.jsonl exceeds the event count limit")
                    else:
                        for line_number, line in enumerate(lines, start=1):
                            if not line.strip():
                                errors.append(f"events.jsonl line {line_number} is blank")
                                continue
                            try:
                                events.append(_loads(line))
                                event_positions.append(line_number)
                            except ValueError as exc:
                                errors.append(f"events.jsonl line {line_number} is invalid: {exc}")
    except (FileNotFoundError, BadZipFile, OSError) as exc:
        errors.append(f"bundle cannot be read: {exc}")
    errors.extend(
        _schema_errors(
            metadata,
            events,
            event_positions=event_positions,
            physical_event_count=physical_event_count,
        )
    )
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


def _snapshot(value: object) -> object:
    if isinstance(value, MappingABC):
        return {
            copy.deepcopy(key): _snapshot(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_snapshot(item) for item in value]
    return copy.deepcopy(value)


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=_ZIP_DATE_TIME)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0
    return info


def _sync_parent(path: Path) -> None:
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)


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
    metadata_to_write = _snapshot(metadata)
    events_to_write: list[object] = []
    try:
        for event in events:
            if len(events_to_write) >= _MAX_EVENTS:
                raise BundleValidationError(
                    bundle_path,
                    ["events exceed the event count limit"],
                )
            events_to_write.append(_snapshot(event))
    except BaseException:
        raise
    errors = _schema_errors(metadata_to_write, events_to_write)
    if errors:
        raise BundleValidationError(bundle_path, errors)

    try:
        metadata_text = json.dumps(metadata_to_write, allow_nan=False, sort_keys=True)
        events_text = "".join(
            json.dumps(event, allow_nan=False, sort_keys=True) + "\n"
            for event in events_to_write
        )
    except BaseException:
        raise
    size_errors: list[str] = []
    if len(metadata_text.encode("utf-8")) > _MAX_MEMBER_BYTES:
        size_errors.append("metadata.json exceeds the member size limit")
    if len(events_text.encode("utf-8")) > _MAX_MEMBER_BYTES:
        size_errors.append("events.jsonl exceeds the member size limit")
    if size_errors:
        raise BundleValidationError(bundle_path, size_errors)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{bundle_path.name}.", suffix=".tmp", dir=bundle_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(_zip_info("metadata.json"), metadata_text)
            archive.writestr(_zip_info("events.jsonl"), events_text)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary_path, bundle_path)
        else:
            os.link(temporary_path, bundle_path)
            temporary_path.unlink()
        _sync_parent(bundle_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
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
    should_stop = stop_on_error
    outcomes: list[dict[str, object]] = []
    try:
        for event in events:
            result = shell.run_cell(event["code"], store_history=store_history)
            success = bool(result.success)
            outcomes.append({"seq": event["seq"], "success": success})
            if not store_history:
                shell.execution_count = original_execution_count
            if should_stop and not success:
                break
    finally:
        if not store_history:
            shell.execution_count = original_execution_count
    return outcomes
