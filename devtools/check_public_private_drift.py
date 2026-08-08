#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


PUBLIC_REQUIRED_PATHS = (
    "Sources",
    "Resources",
    "scanner",
    "scripts",
    "questions",
    "tests",
    "devtools",
    "build.sh",
    "build-dev.sh",
)
PUBLIC_FORBIDDEN_PATHS = (
    "codex_candy_eval.py",
)
PRIVATE_FORBIDDEN_PATHS = PUBLIC_REQUIRED_PATHS
PRIVATE_REQUIRED_PATHS = (
    "private_runtime/scanner/cloud_reference_runner.py",
    "private_runtime/scanner/reference_snapshot_publish.py",
    "private_runtime/scanner/reference_snapshot_regrade.py",
    "private_runtime/public_core.py",
    "private_runtime/prepare_container_context.py",
    "private_runtime/verify_public_core.py",
    "private_runtime/run_private_tests.py",
    "private_runtime/run_private_module.py",
    "private_tests/test_cloud_reference_runner.py",
    "private_tests/test_reference_snapshot_publish.py",
    "modeldial-public-core.lock.json",
    "Dockerfile.cloudflare",
    "cloudflare/wrangler.jsonc",
)
LOCKED_PUBLIC_PATHS = (
    Path("scanner"),
    Path("questions"),
    Path("devtools/__init__.py"),
    Path("devtools/pricing"),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the public App source and private service boundary."
    )
    parser.add_argument("--public-repo", required=True, type=Path)
    parser.add_argument("--private-mirror", required=True, type=Path)
    return parser.parse_args()


def _git_output(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _git_public_core_sha256(root: Path, commit: str) -> str:
    digest = hashlib.sha256()
    pathspecs = [path.as_posix() for path in LOCKED_PUBLIC_PATHS]
    output = _git_output(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        commit,
        "--",
        *pathspecs,
    )
    names = sorted(name.decode("utf-8") for name in output.split(b"\0") if name)
    for path in LOCKED_PUBLIC_PATHS:
        expected = path.as_posix()
        if expected not in names and not any(name.startswith(f"{expected}/") for name in names):
            raise ValueError(f"locked public path is absent from {commit}: {expected}")
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_git_output(root, "show", f"{commit}:{name}"))
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_lock(lock: object) -> tuple[str, str]:
    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        raise ValueError("public core lock schema is invalid")
    if lock.get("repository") != "modeldial":
        raise ValueError("public core lock repository is invalid")
    commit = lock.get("git_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ValueError("public core lock git_commit is invalid")
    try:
        int(commit, 16)
    except ValueError as exc:
        raise ValueError("public core lock git_commit is invalid") from exc
    if lock.get("source_revision") != f"git:{commit}":
        raise ValueError("public core lock source_revision does not match git_commit")
    tree_sha256 = lock.get("tree_sha256")
    if not isinstance(tree_sha256, str) or len(tree_sha256) != 64:
        raise ValueError("public core lock tree_sha256 is invalid")
    try:
        int(tree_sha256, 16)
    except ValueError as exc:
        raise ValueError("public core lock tree_sha256 is invalid") from exc
    return commit, tree_sha256


def _require_lock_commit_matches_head(public_root: Path, commit: str) -> None:
    head = _git_output(public_root, "rev-parse", "HEAD").decode("ascii").strip()
    if commit != head:
        raise ValueError(
            "public core lock git_commit does not match public repository HEAD: "
            f"expected {head}, got {commit}"
        )


def main() -> int:
    args = _arguments()
    public_root = args.public_repo.resolve()
    private_root = args.private_mirror.resolve()
    problems: list[str] = []

    for relative in PUBLIC_REQUIRED_PATHS:
        if not (public_root / relative).exists():
            problems.append(f"public source is missing: {relative}")
    for relative in PUBLIC_FORBIDDEN_PATHS:
        path = public_root / relative
        if path.exists() or path.is_symlink():
            problems.append(f"public source is forbidden: {relative}")
    for relative in PRIVATE_FORBIDDEN_PATHS:
        path = private_root / relative
        if path.exists() or path.is_symlink():
            problems.append(f"public source still exists in private repo: {relative}")
    for relative in PRIVATE_REQUIRED_PATHS:
        if not (private_root / relative).is_file():
            problems.append(f"private integration source is missing: {relative}")

    lock_path = private_root / "modeldial-public-core.lock.json"
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            commit, expected = _validate_lock(lock)
            _require_lock_commit_matches_head(public_root, commit)
            _git_output(public_root, "cat-file", "-e", f"{commit}^{{commit}}")
            actual = _git_public_core_sha256(public_root, commit)
            if expected != actual:
                problems.append(
                    f"public core lock mismatch: expected {expected}, got {actual}"
                )
            dirty = _git_output(
                public_root,
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *(path.as_posix() for path in LOCKED_PUBLIC_PATHS),
            ).decode("utf-8").strip()
            if dirty:
                problems.append(
                    "locked public core has uncommitted changes; commit it before updating the lock"
                )
        except (ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            problems.append(f"public core lock is invalid: {exc}")

    config_path = private_root / "cloudflare" / "wrangler.jsonc"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        containers = config.get("containers")
        context = (
            containers[0].get("image_build_context")
            if isinstance(containers, list) and containers
            else None
        )
        if context != "./.container-context":
            problems.append("Cloudflare Container build context is not the minimal staged context")
        build = config.get("build")
        command = build.get("command") if isinstance(build, dict) else None
        if command != "python3 ../private_runtime/prepare_container_context.py":
            problems.append("Cloudflare Container build context preparation is not configured")

    dockerfile_path = private_root / "Dockerfile.cloudflare"
    if dockerfile_path.is_file():
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        allowed_copy_lines = {
            "COPY --from=codex-installer /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex",
            "COPY --chown=modeldial:modeldial public/scanner /app/scanner",
            "COPY --chown=modeldial:modeldial public/questions /app/questions",
            "COPY --chown=modeldial:modeldial public/devtools/__init__.py /app/devtools/__init__.py",
            "COPY --chown=modeldial:modeldial public/devtools/pricing /app/devtools/pricing",
            "COPY --chown=modeldial:modeldial private_runtime/public_core.py /app/private_runtime/public_core.py",
            "COPY --chown=modeldial:modeldial private_runtime/verify_public_core.py /app/private_runtime/verify_public_core.py",
            "COPY --chown=modeldial:modeldial modeldial-public-core.lock.json /app/modeldial-public-core.lock.json",
            "COPY --chown=modeldial:modeldial private_runtime/scanner/cloud_reference_runner.py /app/scanner/cloud_reference_runner.py",
            "COPY --chown=modeldial:modeldial private_runtime/scanner/reference_snapshot_publish.py /app/scanner/reference_snapshot_publish.py",
            "COPY --chown=modeldial:modeldial private_runtime/scanner/reference_snapshot_regrade.py /app/scanner/reference_snapshot_regrade.py",
            "COPY --chown=modeldial:modeldial cloudflare/job-spec.json /app/cloudflare/job-spec.json",
        }
        actual_copy_lines = {
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip().upper().startswith(("COPY ", "ADD "))
        }
        missing = allowed_copy_lines - actual_copy_lines
        unexpected = actual_copy_lines - allowed_copy_lines
        for line in sorted(missing):
            problems.append(f"Container public-core assembly is missing: {line}")
        for line in sorted(unexpected):
            problems.append(f"Container includes an unexpected build-context source: {line}")

    if problems:
        print("Public/private source boundary failure:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("Public App source and private service overlay boundary are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
