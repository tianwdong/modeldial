from __future__ import annotations

import json
import os
from pathlib import Path


def repair_truncated_jsonl_tail(path: Path) -> None:
    if not path.exists():
        return
    with path.open("r+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size == 0:
            return

        record_end = size
        while record_end > 0:
            handle.seek(record_end - 1)
            if handle.read(1) not in {b"\n", b"\r"}:
                break
            record_end -= 1
        if record_end == 0:
            return

        record_start = record_end
        while record_start > 0:
            handle.seek(record_start - 1)
            if handle.read(1) == b"\n":
                break
            record_start -= 1
        handle.seek(record_start)
        tail = handle.read(record_end - record_start)
        try:
            json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            handle.truncate(record_start)
            handle.flush()
            os.fsync(handle.fileno())
            return

        if size == record_end:
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
