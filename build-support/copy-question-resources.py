#!/usr/bin/env python3
"""Stage question source assets without workstation-installed dependencies."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def copy_question_resources(source: Path, destination: Path) -> None:
    # Keep all authored assets, including manifests and lockfiles. Harness
    # dependencies are installed on the execution host, not shipped in the App.
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc", ".DS_Store"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    copy_question_resources(args.source, args.destination)


if __name__ == "__main__":
    main()
