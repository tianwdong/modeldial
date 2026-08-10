from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from scanner.codex_account import (
    CodexAccountError,
    CodexAccountOutputLimitError,
    _CodexAppServerSession,
    read_codex_account_snapshot,
)


class _FakeSession:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, object]] = []

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def request(self, method: str, params: object = None) -> object:
        self.requests.append((method, params))
        response = self.responses[method]
        if isinstance(response, Exception):
            raise response
        return response


class CodexAccountTest(unittest.TestCase):
    def test_default_discovery_does_not_initialize_a_missing_codex_home(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-account-home-") as temp_dir:
            missing_codex_home = Path(temp_dir) / ".codex"

            with patch(
                "scanner.codex_account._default_binary_candidates"
            ) as default_candidates:
                with self.assertRaisesRegex(CodexAccountError, "尚未初始化"):
                    read_codex_account_snapshot(
                        codex_home=missing_codex_home,
                        session_factory=lambda _binary, _timeout: self.fail(
                            "missing Codex state must not start app-server"
                        ),
                    )

            default_candidates.assert_not_called()

    def test_app_server_shared_output_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-account-output-") as temp_dir:
            binary = Path(temp_dir) / "noisy-codex"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stderr.write('x' * 1000000)\n"
                "sys.stderr.flush()\n"
                "for _line in sys.stdin:\n"
                "    pass\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            with patch(
                "scanner.codex_account.CODEX_ACCOUNT_OUTPUT_LIMIT_BYTES",
                1024,
            ):
                with self.assertRaises(CodexAccountOutputLimitError):
                    with _CodexAppServerSession(binary, 2) as session:
                        session.request("account/read")

    def test_reads_chatgpt_account_quota_and_usage_without_exposing_email(self) -> None:
        session = _FakeSession(
            {
                "account/read": {
                    "account": {
                        "type": "chatgpt",
                        "email": "private@example.com",
                        "planType": "pro",
                    },
                    "requiresOpenaiAuth": True,
                },
                "account/rateLimits/read": {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 63,
                            "windowDurationMins": 300,
                            "resetsAt": 1784898600,
                        },
                        "secondary": {
                            "usedPercent": 41,
                            "windowDurationMins": 10080,
                            "resetsAt": 1785283200,
                        },
                    }
                },
                "account/usage/read": {
                    "summary": {
                        "lifetimeTokens": 123456,
                        "peakDailyTokens": 12000,
                        "longestRunningTurnSec": 321,
                        "currentStreakDays": 4,
                        "longestStreakDays": 9,
                    },
                    "dailyUsageBuckets": [
                        {"startDate": "2026-07-23", "tokens": 4000}
                    ],
                },
            }
        )

        snapshot = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:00:00Z",
            session_factory=lambda _binary, _timeout: session,
        )

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["account_type"], "chatgpt")
        self.assertEqual(snapshot["login_state"], "authenticated")
        self.assertEqual(snapshot["plan_type"], "pro")
        self.assertEqual(snapshot["quota_status"], "available")
        self.assertEqual(
            [item["label"] for item in snapshot["quota_windows"]],
            ["5h", "weekly"],
        )
        self.assertEqual(
            [item["window_id"] for item in snapshot["quota_windows"]],
            ["codex:300m", "codex:10080m"],
        )
        self.assertEqual(
            [item["source_slot"] for item in snapshot["quota_windows"]],
            ["primary", "secondary"],
        )
        self.assertEqual(snapshot["usage_status"], "available")
        self.assertEqual(snapshot["usage_summary"]["lifetime_tokens"], 123456)
        self.assertEqual(snapshot["daily_usage"][0]["tokens"], 4000)
        self.assertNotIn("email", str(snapshot).lower())
        self.assertNotIn("private@example.com", str(snapshot))
        self.assertEqual(
            session.requests,
            [
                ("account/read", {"refreshToken": False}),
                ("account/rateLimits/read", None),
                ("account/usage/read", None),
            ],
        )

    def test_api_key_account_does_not_invent_subscription_quota(self) -> None:
        session = _FakeSession(
            {
                "account/read": {
                    "account": {"type": "apiKey"},
                    "requiresOpenaiAuth": True,
                }
            }
        )

        snapshot = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:00:00Z",
            session_factory=lambda _binary, _timeout: session,
        )

        self.assertEqual(snapshot["account_type"], "api_key")
        self.assertEqual(snapshot["quota_status"], "not_applicable")
        self.assertEqual(snapshot["usage_status"], "not_applicable")
        self.assertEqual(snapshot["quota_windows"], [])
        self.assertEqual(
            session.requests,
            [("account/read", {"refreshToken": False})],
        )

    def test_optional_quota_failure_keeps_authenticated_account_snapshot(self) -> None:
        session = _FakeSession(
            {
                "account/read": {
                    "account": {"type": "chatgpt", "planType": "plus"},
                    "requiresOpenaiAuth": True,
                },
                "account/rateLimits/read": RuntimeError("unavailable"),
                "account/usage/read": RuntimeError("unavailable"),
            }
        )

        snapshot = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:00:00Z",
            session_factory=lambda _binary, _timeout: session,
        )

        self.assertEqual(snapshot["login_state"], "authenticated")
        self.assertEqual(snapshot["quota_status"], "unavailable")
        self.assertEqual(snapshot["usage_status"], "unavailable")
        self.assertEqual(
            snapshot["unavailable_capabilities"],
            ["rate_limits", "account_usage"],
        )

    def test_multi_bucket_rate_limits_preserve_each_limit_identity(self) -> None:
        session = _FakeSession(
            {
                "account/read": {
                    "account": {"type": "chatgpt", "planType": "pro"},
                    "requiresOpenaiAuth": True,
                },
                "account/rateLimits/read": {
                    "rateLimits": {},
                    "rateLimitsByLimitId": {
                        "codex": {
                            "limitId": "codex",
                            "primary": {
                                "usedPercent": 20,
                                "windowDurationMins": 300,
                                "resetsAt": 1784898600,
                            },
                        },
                        "workspace": {
                            "limitId": "workspace",
                            "secondary": {
                                "usedPercent": 40,
                                "windowDurationMins": 10080,
                                "resetsAt": 1785283200,
                            },
                        },
                    },
                },
                "account/usage/read": {
                    "summary": {},
                    "dailyUsageBuckets": None,
                },
            }
        )

        snapshot = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:00:00Z",
            session_factory=lambda _binary, _timeout: session,
        )

        self.assertEqual(
            [item["window_id"] for item in snapshot["quota_windows"]],
            ["codex:300m", "workspace:10080m"],
        )
        self.assertEqual(
            [item["limit_id"] for item in snapshot["quota_windows"]],
            ["codex", "workspace"],
        )

    def test_window_identity_survives_backend_slot_change(self) -> None:
        shared_responses = {
            "account/read": {
                "account": {"type": "chatgpt", "planType": "pro"},
                "requiresOpenaiAuth": True,
            },
            "account/usage/read": {
                "summary": {},
                "dailyUsageBuckets": None,
            },
        }
        weekly_only = _FakeSession(
            {
                **shared_responses,
                "account/rateLimits/read": {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 40,
                            "windowDurationMins": 10080,
                            "resetsAt": 1785283200,
                        },
                    }
                },
            }
        )
        five_hour_and_weekly = _FakeSession(
            {
                **shared_responses,
                "account/rateLimits/read": {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 20,
                            "windowDurationMins": 300,
                            "resetsAt": 1784898600,
                        },
                        "secondary": {
                            "usedPercent": 42,
                            "windowDurationMins": 10080,
                            "resetsAt": 1785283200,
                        },
                    }
                },
            }
        )

        before = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:00:00Z",
            session_factory=lambda _binary, _timeout: weekly_only,
        )
        after = read_codex_account_snapshot(
            binary_candidates=(Path("/runtime/codex"),),
            captured_at="2026-07-24T08:30:00Z",
            session_factory=lambda _binary, _timeout: five_hour_and_weekly,
        )

        self.assertEqual(before["quota_windows"][0]["window_id"], "codex:10080m")
        self.assertEqual(after["quota_windows"][1]["window_id"], "codex:10080m")
        self.assertEqual(before["quota_windows"][0]["source_slot"], "primary")
        self.assertEqual(after["quota_windows"][1]["source_slot"], "secondary")


if __name__ == "__main__":
    unittest.main()
