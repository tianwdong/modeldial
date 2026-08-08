#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.session_hook_installer import (
    install_claude_hooks,
    install_codex_hooks,
    install_helper,
    uninstall_claude_hooks,
    uninstall_codex_hooks,
    uninstall_helper,
)
from scanner.session_registry import application_support_root


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install or uninstall passive modeldial session observer hooks.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove ModelDial's hooks and installed helper",
    )
    parser.add_argument("--codex-hooks", type=Path, default=Path.home() / ".codex" / "hooks.json")
    parser.add_argument(
        "--claude-settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
    )
    args = parser.parse_args()

    helper_source = ROOT / "scripts" / "modeldial_session_hook.py"
    helper_destination = application_support_root() / "bin" / "ModeldialSessionHook"

    if args.uninstall:
        try:
            codex_changed = uninstall_codex_hooks(
                args.codex_hooks.expanduser(), helper_destination
            )
            claude_changed = uninstall_claude_hooks(
                args.claude_settings.expanduser(),
                helper_destination,
            )
            helper_changed = uninstall_helper(helper_source, helper_destination)
        except (OSError, ValueError) as error:
            print(f"session observer uninstall failed: {error}", file=sys.stderr)
            return 1
        print(
            "session observer uninstalled"
            if helper_changed or codex_changed or claude_changed
            else "session observer already uninstalled"
        )
        return 0

    helper_changed = install_helper(helper_source, helper_destination)
    codex_changed = install_codex_hooks(args.codex_hooks.expanduser(), helper_destination)
    claude_changed = install_claude_hooks(
        args.claude_settings.expanduser(),
        helper_destination,
    )
    print(
        "session observer installed"
        if helper_changed or codex_changed or claude_changed
        else "session observer already installed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
