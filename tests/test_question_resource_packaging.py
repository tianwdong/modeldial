from __future__ import annotations

import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest

from scanner.question_bank import QuestionBank

ROOT = Path(__file__).resolve().parents[1]
copy_question_resources = runpy.run_path(
    str(ROOT / "build-support" / "copy-question-resources.py")
)["copy_question_resources"]


class QuestionResourcePackagingTests(unittest.TestCase):
    def test_preserves_authored_assets_and_excludes_nested_generated_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "source", root / "bundle"
            authored = ["catalog.json", "prompt.md", "answer.json", "frontend/harness/package.json",
                        "frontend/harness/package-lock.json", "frontend/harness/src/main.js"]
            generated = ["node_modules/root.js", "frontend/harness/node_modules/@rollup/binary.node",
                         "frontend/__pycache__/grader.pyc", "frontend/grader.pyc", ".DS_Store"]
            for name in authored + generated:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            copy_question_resources(source, destination)
            for name in authored:
                self.assertEqual((source / name).read_bytes(), (destination / name).read_bytes())
            for name in generated:
                self.assertTrue((source / name).exists())
                self.assertFalse((destination / name).exists())
            self.assertEqual(sorted(str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()), sorted(authored))

    def test_keeps_authored_symlinks_without_following_dependency_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "prompt.md").write_text("unchanged")
            (source / "prompt-alias.md").symlink_to("prompt.md")
            (source / "node_modules").symlink_to(root / "not-installed", target_is_directory=True)
            copy_question_resources(source, root / "bundle")
            self.assertTrue((root / "bundle/prompt-alias.md").is_symlink())
            self.assertFalse((root / "bundle/node_modules").is_symlink())

    def test_current_catalog_and_all_registered_question_bytes_are_unchanged(self):
        import json

        source = ROOT / "questions"
        catalog = json.loads((source / "catalog.json").read_text())
        paths = ["catalog.json", *[item[key] for item in catalog["questions"] for key in ("prompt_path", "answer_path")]]
        before = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in paths}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "questions"
            copy_question_resources(source, destination)
            self.assertEqual(QuestionBank(source).load(), QuestionBank(destination).load())
            self.assertEqual(before, {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in paths})
            self.assertFalse(any(p.name == "node_modules" for p in destination.rglob("*")))
        self.assertEqual(before, {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in paths})

    def test_refuses_to_overwrite_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            (root / "destination").mkdir()
            marker = root / "destination/keep.txt"
            marker.write_text("user file")
            with self.assertRaises(FileExistsError):
                copy_question_resources(root / "source", root / "destination")
            self.assertEqual(marker.read_text(), "user file")
