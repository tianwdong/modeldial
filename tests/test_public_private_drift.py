from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from devtools.check_public_private_drift import _require_lock_commit_matches_head


class PublicPrivateDriftTest(unittest.TestCase):
    def _git(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_lock_commit_must_be_current_head_even_when_old_commit_is_reachable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._git(root, "init", "-q", "-b", "main")
            self._git(root, "config", "user.name", "ModelDial Test")
            self._git(root, "config", "user.email", "test@modeldial.invalid")

            tracked = root / "tracked.txt"
            tracked.write_text("first\n", encoding="utf-8")
            self._git(root, "add", "tracked.txt")
            self._git(root, "commit", "-q", "-m", "first")
            old_commit = self._git(root, "rev-parse", "HEAD")

            tracked.write_text("second\n", encoding="utf-8")
            self._git(root, "commit", "-q", "-am", "second")
            current_head = self._git(root, "rev-parse", "HEAD")

            self._git(root, "cat-file", "-e", f"{old_commit}^{{commit}}")
            with self.assertRaisesRegex(
                ValueError,
                "git_commit does not match public repository HEAD",
            ):
                _require_lock_commit_matches_head(root, old_commit)

            _require_lock_commit_matches_head(root, current_head)


if __name__ == "__main__":
    unittest.main()
