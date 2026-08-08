from __future__ import annotations

import json
import unittest

from scanner.usage_behavior import (
    message_category_from_response_item,
    mcp_tool_step,
    summarize_turn_behavior,
    tool_step_from_response_item,
)


def _fingerprint(value: str) -> str:
    return f"hash:{value.rsplit('/', 1)[-1]}"


def _function_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(arguments),
    }


class UsageBehaviorTest(unittest.TestCase):
    def test_standard_calls_count_same_file_edit_after_shell_as_retry(self) -> None:
        patch = "*** Begin Patch\n*** Update File: /private/project/app.py\n@@\n*** End Patch"
        steps = [
            tool_step_from_response_item(
                _function_call("apply_patch", {"patch": patch}),
                _fingerprint,
            ),
            tool_step_from_response_item(
                _function_call("exec_command", {"cmd": "python -m unittest"}),
                _fingerprint,
            ),
            tool_step_from_response_item(
                _function_call("apply_patch", {"patch": patch}),
                _fingerprint,
            ),
        ]

        summary = summarize_turn_behavior(
            {
                "behavior_observed": True,
                "message_category_hint": "debugging",
                "tool_steps": [step for step in steps if step is not None],
            }
        )

        self.assertEqual(summary["task_category"], "debugging")
        self.assertTrue(summary["has_edits"])
        self.assertEqual(summary["retry_count"], 1)
        self.assertFalse(summary["one_shot"])

    def test_custom_exec_calls_are_derived_without_retaining_source(self) -> None:
        edit_source = """
const patch = "*** Begin Patch\\n*** Update File: /private/project/app.py\\n*** End Patch";
await tools.apply_patch(patch);
"""
        verify_source = 'await tools.exec_command({cmd: "npm test"});'
        steps = [
            tool_step_from_response_item(
                {"type": "custom_tool_call", "name": "exec", "input": source},
                _fingerprint,
            )
            for source in (edit_source, verify_source, edit_source)
        ]

        summary = summarize_turn_behavior(
            {
                "behavior_observed": True,
                "message_category_hint": "feature",
                "tool_steps": [step for step in steps if step is not None],
            }
        )

        self.assertEqual(summary["task_category"], "feature")
        self.assertEqual(summary["retry_count"], 1)
        self.assertNotIn("source", json.dumps(steps))
        self.assertNotIn("/private/project", json.dumps(steps))

    def test_unknown_edit_target_is_not_treated_as_same_file_retry(self) -> None:
        edit = tool_step_from_response_item(
            _function_call("apply_patch", {}),
            _fingerprint,
        )
        shell = tool_step_from_response_item(
            _function_call("exec_command", {"cmd": "pytest"}),
            _fingerprint,
        )

        summary = summarize_turn_behavior(
            {
                "behavior_observed": True,
                "tool_steps": [edit, shell, edit],
            }
        )

        self.assertTrue(summary["has_edits"])
        self.assertIsNone(summary["retry_count"])
        self.assertIsNone(summary["one_shot"])

    def test_non_edit_test_command_is_classified_as_testing(self) -> None:
        message_hint = message_category_from_response_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "请运行测试并检查结果"}],
            }
        )
        shell = tool_step_from_response_item(
            _function_call("exec_command", {"cmd": "python -m pytest"}),
            _fingerprint,
        )

        summary = summarize_turn_behavior(
            {
                "behavior_observed": True,
                "message_category_hint": message_hint,
                "tool_steps": [shell],
            }
        )

        self.assertEqual(summary["task_category"], "testing")
        self.assertFalse(summary["has_edits"])
        self.assertIsNone(summary["one_shot"])

    def test_unknown_mcp_tool_is_not_assumed_to_be_search(self) -> None:
        function_step = tool_step_from_response_item(
            _function_call("mcp__github__create_issue", {}),
            _fingerprint,
        )

        self.assertEqual(function_step, {"kinds": ["external"], "file_keys": []})
        self.assertEqual(mcp_tool_step(), {"kinds": ["external"], "file_keys": []})


if __name__ == "__main__":
    unittest.main()
