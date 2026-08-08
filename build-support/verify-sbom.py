#!/usr/bin/env python3
"""Fail-closed verification for a ModelDial SPDX bundle SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "build-support" / "pyinstaller-requirements.txt"
REQUIRED_RUNTIME_LICENSES = {
    "PYTHON-LICENSE.txt",
    "OPENSSL-LICENSE.txt",
    "XZ-LICENSE.txt",
    "ZSTD-LICENSE.txt",
    "MPDECIMAL-LICENSE.txt",
    "PYINSTALLER-LICENSE.txt",
    "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
    "THIRD_PARTY_DEPENDENCIES.txt",
    "THIRD_PARTY_NOTICES.txt",
    "Sparkle-LICENSE.txt",
    "certifi-LICENSE.txt",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requirements(path: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    package_names: set[str] = set()
    current: tuple[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            line = line[:-1].strip()
        if line.startswith("--hash="):
            if current is None:
                raise ValueError(f"hash without package: {raw}")
            algorithm, separator, digest = line[7:].partition(":")
            if algorithm != "sha256" or separator != ":" or not re.fullmatch(
                r"[0-9a-f]{64}", digest
            ):
                raise ValueError(f"invalid requirement hash: {raw}")
            result[current[0]] = (current[1], digest)
            continue
        name, separator, version = line.partition("==")
        if separator != "==" or not name or not version:
            raise ValueError(f"invalid requirement: {raw}")
        current = (re.sub(r"[-_.]+", "-", name).lower(), version)
        package_names.add(current[0])
    if not result:
        raise ValueError("requirements file contains no hashed packages")
    if package_names != set(result):
        missing = sorted(package_names - set(result))
        raise ValueError(f"requirements without sha256 hashes: {', '.join(missing)}")
    return result


def fail(message: str) -> None:
    raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--requirements", type=Path, default=REQUIREMENTS)
    args = parser.parse_args(argv)

    bundle = args.bundle.resolve()
    sbom_path = args.sbom.resolve()
    if not bundle.is_dir() or bundle.suffix != ".app":
        fail(f"bundle must be an existing .app directory: {bundle}")
    if not sbom_path.is_file():
        fail(f"SBOM does not exist: {sbom_path}")
    try:
        document: dict[str, Any] = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read SPDX JSON: {error}")

    if document.get("spdxVersion") != "SPDX-2.3":
        fail("SBOM must use SPDX-2.3")
    if document.get("dataLicense") != "CC0-1.0":
        fail("SBOM dataLicense must be CC0-1.0")
    if document.get("SPDXID") != "SPDXRef-DOCUMENT":
        fail("SBOM document SPDXID is invalid")

    packages = document.get("packages")
    files = document.get("files")
    relationships = document.get("relationships")
    if not isinstance(packages, list) or not isinstance(files, list):
        fail("SBOM packages and files arrays are required")
    if not isinstance(relationships, list):
        fail("SBOM relationships array is required")
    package_by_name: dict[str, dict[str, Any]] = {}
    package_ids: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            fail("SBOM package entry is not an object")
        package_id = package.get("SPDXID")
        name = package.get("name")
        license_declared = package.get("licenseDeclared")
        if not isinstance(package_id, str) or package_id in package_ids:
            fail("SBOM package IDs must be unique")
        if not isinstance(name, str) or not name:
            fail("SBOM package name is required")
        if not isinstance(license_declared, str) or license_declared in {
            "NOASSERTION",
            "NONE",
        }:
            fail(f"license claim missing for package {name}")
        package_ids.add(package_id)
        package_by_name[name] = package

    info_path = bundle / "Contents" / "Info.plist"
    if not info_path.is_file():
        fail("bundle is missing Contents/Info.plist")
    info = plistlib.loads(info_path.read_bytes())
    app = package_by_name.get("ModelDial")
    if app is None:
        fail("SBOM is missing ModelDial package")
    if str(app.get("versionInfo")) != str(info.get("CFBundleShortVersionString")):
        fail("SBOM ModelDial version does not match bundle Info.plist")
    if app.get("filesAnalyzed") is not True:
        fail("ModelDial package must analyze bundle files")
    verification = app.get("packageVerificationCode")
    if not isinstance(verification, dict) or not re.fullmatch(
        r"[0-9a-f]{40}", str(verification.get("packageVerificationCodeValue"))
    ):
        fail("ModelDial package verification code is required")

    required_names = {
        "ModelDial",
        "Python",
        "OpenSSL",
        "XZ / liblzma",
        "Zstandard",
        "libmpdec",
        "certifi",
        "Sparkle",
        "LiteLLM pricing data",
        "@lobehub/icons-static-svg",
    }
    requirement_names = requirements(args.requirements.resolve())
    required_names.update(
        {
            name
            for name in (
                package.get("name")
                for package in packages
                if isinstance(package, dict)
            )
            if isinstance(name, str) and name.lower() in requirement_names
        }
    )
    missing_names = sorted(name for name in required_names if name not in package_by_name)
    if missing_names:
        fail(f"SBOM is missing required packages: {', '.join(missing_names)}")

    file_by_name: dict[str, dict[str, Any]] = {}
    file_ids: set[str] = set()
    for file_entry in files:
        if not isinstance(file_entry, dict):
            fail("SBOM file entry is not an object")
        file_id = file_entry.get("SPDXID")
        file_name = file_entry.get("fileName")
        checksums = file_entry.get("checksums")
        if not isinstance(file_id, str) or file_id in file_ids:
            fail("SBOM file IDs must be unique")
        if not isinstance(file_name, str) or not file_name:
            fail("SBOM fileName is required")
        if not isinstance(checksums, list) or len(checksums) != 1:
            fail(f"SHA-256 checksum required for {file_name}")
        checksum = checksums[0]
        if checksum.get("algorithm") != "SHA256" or not re.fullmatch(
            r"[0-9a-f]{64}", str(checksum.get("checksumValue"))
        ):
            fail(f"invalid SHA-256 checksum for {file_name}")
        path = bundle / file_name
        if not path.is_file():
            fail(f"SBOM file is not present in bundle: {file_name}")
        if sha256(path) != checksum["checksumValue"]:
            fail(f"bundle checksum mismatch: {file_name}")
        file_ids.add(file_id)
        file_by_name[file_name] = file_entry

    actual_bundle_files = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    if set(file_by_name) != actual_bundle_files:
        missing = sorted(actual_bundle_files - set(file_by_name))
        extra = sorted(set(file_by_name) - actual_bundle_files)
        details = []
        if missing:
            details.append(f"unlisted bundle files: {', '.join(missing[:5])}")
        if extra:
            details.append(f"SBOM files outside bundle: {', '.join(extra[:5])}")
        fail("SBOM file coverage is incomplete (" + "; ".join(details) + ")")

    legal_dir = bundle / "Contents" / "Resources" / "Legal"
    for legal_name in REQUIRED_RUNTIME_LICENSES:
        legal_path = legal_dir / legal_name
        if not legal_path.is_file() or legal_path.stat().st_size == 0:
            fail(f"bundle is missing legal evidence: {legal_name}")

    app_contains = {
        relation.get("relatedSpdxElement")
        for relation in relationships
        if relation.get("spdxElementId") == app["SPDXID"]
        and relation.get("relationshipType") == "CONTAINS"
    }
    if app_contains != file_ids:
        fail("ModelDial package must contain every SBOM file exactly once")
    expected_verification = hashlib.sha1(
        "".join(
            sorted(sha1(bundle / entry["fileName"]) for entry in files)
        ).encode("ascii")
    ).hexdigest()
    if verification["packageVerificationCodeValue"] != expected_verification:
        fail("ModelDial package verification code mismatch")
    dependencies = {
        relation.get("relatedSpdxElement")
        for relation in relationships
        if relation.get("spdxElementId") == app["SPDXID"]
        and relation.get("relationshipType") == "DEPENDS_ON"
    }
    expected_dependencies = package_ids - {app["SPDXID"]}
    if dependencies != expected_dependencies:
        fail("ModelDial package dependency relationships are incomplete")

    package_by_id = {package["SPDXID"]: package for package in packages}
    for package_id, package in package_by_id.items():
        if package_id == app["SPDXID"]:
            continue
        if package.get("filesAnalyzed") is True:
            fail(f"unmapped package must not claim filesAnalyzed=true: {package['name']}")

    for normalized_name, (version, digest) in requirement_names.items():
        candidates = [
            package
            for package in packages
            if re.sub(r"[-_.]+", "-", str(package.get("name"))).lower()
            == normalized_name
            and str(package.get("versionInfo")) == version
            and isinstance(package.get("checksums"), list)
        ]
        if len(candidates) != 1:
            fail(f"hashed build package missing or duplicated: {normalized_name}")
        checksums = candidates[0].get("checksums")
        if not isinstance(checksums, list) or not any(
            item.get("algorithm") == "SHA256" and item.get("checksumValue") == digest
            for item in checksums
            if isinstance(item, dict)
        ):
            fail(f"build artifact hash mismatch in SBOM: {normalized_name}")

    print(f"verified SPDX SBOM: {sbom_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"verify-sbom: {error}", file=sys.stderr)
        raise SystemExit(1)
