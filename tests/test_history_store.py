from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.history_store import HistoryStore
from scanner.models import ScanResult


class HistoryStoreTest(unittest.TestCase):
    @staticmethod
    def result(index: int) -> ScanResult:
        return ScanResult(
            run_id="run-history",
            candidate_id="candidate",
            model="gpt-5.5",
            effort="xhigh",
            question_id=f"q{index}",
            started_at="2026-06-30T10:00:00+08:00",
            elapsed_seconds=12.5,
            source_mode="mock",
            answer_ok=True,
            answer_preview=str(index),
            input_tokens=100,
            output_tokens=40,
            reasoning_tokens=516,
            retry_index=0,
            flags=[],
            final_status="pass",
        )

    def test_append_and_load_recent_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            result = ScanResult(
                model="gpt-5.5",
                effort="xhigh",
                started_at="2026-06-30T10:00:00+08:00",
                elapsed_seconds=12.5,
                source_mode="mock",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=40,
                reasoning_tokens=516,
                retry_index=0,
                flags=["reason_tok_516"],
                final_status="warn",
            )

            store.append(result)
            recent = store.load_recent(limit=5)

            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0].final_status, "warn")
            self.assertEqual(recent[0].reasoning_tokens, 516)
            self.assertEqual(recent[0].source_mode, "mock")

    def test_load_recent_with_count_keeps_exact_total(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            for index in range(3):
                store.append(self.result(index))

            recent, total = store.load_recent_with_count(limit=2)

            self.assertEqual(total, 3)
            self.assertEqual([item.question_id for item in recent], ["q1", "q2"])

    def test_loaders_ignore_only_a_truncated_final_jsonl_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            store.append(self.result(1))
            with store.path.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "truncated"')

            recent, total = store.load_recent_with_count(limit=5)

            self.assertEqual(total, 1)
            self.assertEqual(len(recent), 1)
            self.assertEqual(len(store.load_all()), 1)

    def test_append_repairs_a_truncated_tail_before_writing_new_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            store.append(self.result(1))
            with store.path.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "truncated"')

            store.append(self.result(2))
            store.append(self.result(3))

            self.assertEqual(
                [item.question_id for item in store.load_all()],
                ["q1", "q2", "q3"],
            )

    def test_loader_does_not_hide_malformed_middle_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            valid = json.dumps(self.result(1).to_dict(), ensure_ascii=False)
            store.path.write_text(f"{valid}\nnot-json\n{valid}\n", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                store.load_all()

    def test_failed_metadata_save_preserves_previous_metadata_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HistoryStore(Path(temp_dir) / "history.jsonl")
            store.save_run_metadata({"run_id": "run-history", "status": "running"})
            previous = store.metadata_path.read_text(encoding="utf-8")

            with patch("scanner.history_store.json.dump", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    store.save_run_metadata(
                        {"run_id": "run-history", "status": "completed"}
                    )

            self.assertEqual(store.metadata_path.read_text(encoding="utf-8"), previous)
            self.assertEqual(
                list(
                    store.metadata_path.parent.glob(
                        f".{store.metadata_path.name}.*.tmp"
                    )
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
