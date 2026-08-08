from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Mapping


RAW_ANSWER_SCHEMA_VERSION = 1


def capture_raw_answer(
    *,
    run_id: str,
    evaluation_id: str,
    candidate_id: str,
    question_id: str,
    attempt_index: int,
    answer: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    environment = os.environ if environ is None else environ
    configured_root = environment.get("MODELDIAL_RAW_ANSWER_DIR", "").strip()
    if not configured_root:
        return None
    root = Path(configured_root).expanduser().resolve()
    run_root = (root / run_id).resolve()
    if root not in run_root.parents:
        raise ValueError("raw answer run path escapes evidence root")
    record = {
        "schema_version": RAW_ANSWER_SCHEMA_VERSION,
        "run_id": run_id,
        "evaluation_id": evaluation_id,
        "candidate_id": candidate_id,
        "question_id": question_id,
        "attempt_index": attempt_index,
        "answer": answer,
        "answer_sha256": _sha256_text(answer),
    }
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / f"{evaluation_id}.json"
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(record["answer_sha256"])


def load_raw_answer_record(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"raw answer evidence must contain an object: {path}")
    if payload.get("schema_version") != RAW_ANSWER_SCHEMA_VERSION:
        raise ValueError(f"unsupported raw answer evidence schema: {path}")
    answer = payload.get("answer")
    if not isinstance(answer, str):
        raise ValueError(f"raw answer evidence is missing answer text: {path}")
    if payload.get("answer_sha256") != _sha256_text(answer):
        raise ValueError(f"raw answer evidence hash mismatch: {path}")
    return payload


def payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
