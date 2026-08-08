from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time
from typing import Iterator
from uuid import uuid4


@contextmanager
def exclusive_system_process_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    open_flags = os.O_CREAT | os.O_RDWR
    if os.name == "nt" and hasattr(os, "O_BINARY"):
        open_flags |= os.O_BINARY
    descriptor = os.open(str(path), open_flags, 0o600)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    yield False
                    return
                raise
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    yield False
                    return
                raise
        acquired = True
        yield True
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_scan_lock_payload(path: Path) -> tuple[int, float | None]:
    try:
        raw_text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return -1, None
    if not raw_text:
        return -1, None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            return int(raw_text), None
        except ValueError:
            return -1, None
    if isinstance(payload, int):
        return payload, None
    if not isinstance(payload, dict):
        return -1, None
    try:
        pid = int(payload.get("pid", -1))
    except (TypeError, ValueError):
        pid = -1
    heartbeat_raw = payload.get("heartbeat_at")
    try:
        heartbeat_at = float(heartbeat_raw) if heartbeat_raw is not None else None
    except (TypeError, ValueError):
        heartbeat_at = None
    return pid, heartbeat_at


def scan_lock_is_active(path: Path, *, stale_seconds: float = 420) -> bool:
    pid, heartbeat_at = read_scan_lock_payload(path)
    if not _process_is_alive(pid):
        return False
    reference_time = heartbeat_at
    if reference_time is None:
        try:
            reference_time = path.stat().st_mtime
        except OSError:
            return False
    return (time.time() - reference_time) <= stale_seconds


def _lock_is_reclaimable(path: Path, stale_seconds: float) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        try:
            return time.time() - path.stat().st_mtime >= stale_seconds
        except OSError:
            return True
    try:
        pid = int(payload.get("pid") or 0)
    except (AttributeError, TypeError, ValueError):
        pid = 0
    return pid > 0 and not _process_is_alive(pid)


@contextmanager
def exclusive_process_lock(
    path: Path,
    *,
    timeout_seconds: float = 5.0,
    stale_seconds: float = 60.0,
) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = path.with_name(f"{path.name}.guard")
    token = uuid4().hex
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        with exclusive_system_process_lock(guard_path) as guard_acquired:
            if guard_acquired:
                acquired = False
                while True:
                    try:
                        descriptor = os.open(
                            path,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                    except FileExistsError:
                        if _lock_is_reclaimable(path, stale_seconds):
                            try:
                                path.unlink()
                            except OSError:
                                break
                            continue
                        break
                    payload = json.dumps(
                        {
                            "pid": os.getpid(),
                            "token": token,
                            "created_at": time.time(),
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    acquired = True
                    break

                if acquired:
                    try:
                        yield True
                    finally:
                        try:
                            payload = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            payload = {}
                        if payload.get("token") == token:
                            try:
                                path.unlink()
                            except OSError:
                                pass
                    return
        if time.monotonic() >= deadline:
            yield False
            return
        time.sleep(0.01)
