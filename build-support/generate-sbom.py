#!/usr/bin/env python3
"""Generate an SPDX 2.3 SBOM from a built ModelDial app bundle.

The generator deliberately uses only the Python standard library.  It reads
the bundle that will be published, hashes every regular file, and combines
those observed files with the locked build inputs and checked-in legal
inventory.  It never fetches package metadata or invents a license claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "build-support" / "pyinstaller-requirements.txt"
LEGAL_ROOT = ROOT / "Resources" / "Legal"


PYPI_METADATA: dict[str, dict[str, str]] = {
    "altgraph": {
        "license": "MIT",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": "https://pypi.org/project/altgraph/0.17.5/",
    },
    "certifi": {
        "license": "MPL-2.0",
        "license_file": "certifi-LICENSE.txt",
        "source": "https://pypi.org/project/certifi/2026.7.22/",
    },
    "macholib": {
        "license": "MIT",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": "https://pypi.org/project/macholib/1.16.4/",
    },
    "packaging": {
        "license": "Apache-2.0 OR BSD-2-Clause",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": "https://pypi.org/project/packaging/26.2/",
    },
    "pip": {
        "license": "MIT",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": "https://pypi.org/project/pip/26.0/",
    },
    "pyinstaller": {
        "license": "GPL-2.0-or-later WITH Bootloader-exception",
        "license_file": "PYINSTALLER-LICENSE.txt",
        "source": "https://pypi.org/project/pyinstaller/6.21.0/",
    },
    "pyinstaller-hooks-contrib": {
        "license": "GPL-2.0-or-later AND Apache-2.0",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": (
            "https://pypi.org/project/pyinstaller-hooks-contrib/2026.6/"
        ),
    },
    "setuptools": {
        "license": "MIT",
        "license_file": "PYPI-BUILD-DEPENDENCIES-LICENSES.txt",
        "source": "https://pypi.org/project/setuptools/83.0.0/",
    },
}


RUNTIME_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Python",
        "version": "3.14.3",
        "license": "PSF-2.0",
        "license_file": "PYTHON-LICENSE.txt",
        "source": "https://www.python.org/downloads/release/python-3143/",
        "paths": ("Contents/Resources/Backend/Runtime/_internal/Python",),
    },
    {
        "name": "OpenSSL",
        "version": "3.0.18",
        "license": "Apache-2.0",
        "license_file": "OPENSSL-LICENSE.txt",
        "source": "https://www.openssl.org/source/",
        "paths": (
            "Contents/Resources/Backend/Runtime/_internal/libcrypto.3.dylib",
            "Contents/Resources/Backend/Runtime/_internal/libssl.3.dylib",
        ),
    },
    {
        "name": "XZ / liblzma",
        "version": "5.2.3",
        "license": "0BSD",
        "license_file": "XZ-LICENSE.txt",
        "source": "https://tukaani.org/xz/",
        "paths": (
            "Contents/Resources/Backend/Runtime/_internal/python3.14/"
            "lib-dynload/_lzma.cpython-314-darwin.so",
        ),
    },
    {
        "name": "Zstandard",
        "version": "1.5.7",
        "license": "BSD-3-Clause",
        "license_file": "ZSTD-LICENSE.txt",
        "source": "https://github.com/facebook/zstd/releases/tag/v1.5.7",
        "paths": (
            "Contents/Resources/Backend/Runtime/_internal/libzstd.1.dylib",
            "Contents/Resources/Backend/Runtime/_internal/python3.14/"
            "lib-dynload/_zstd.cpython-314-darwin.so",
        ),
    },
    {
        "name": "libmpdec",
        "version": "4.0.0",
        "license": "BSD-2-Clause",
        "license_file": "MPDECIMAL-LICENSE.txt",
        "source": "https://www.bytereef.org/mpdecimal/",
        "paths": (
            "Contents/Resources/Backend/Runtime/_internal/python3.14/"
            "lib-dynload/_decimal.cpython-314-darwin.so",
        ),
    },
    {
        "name": "certifi",
        "version": "2026.7.22",
        "license": "MPL-2.0",
        "license_file": "certifi-LICENSE.txt",
        "source": "https://github.com/certifi/python-certifi/tree/2026.07.22",
        "paths": (
            "Contents/Resources/Backend/Runtime/_internal/certifi/cacert.pem",
        ),
    },
    {
        "name": "Sparkle",
        "version": "2.9.4",
        "license": "MIT",
        "license_file": "Sparkle-LICENSE.txt",
        "source": "https://github.com/sparkle-project/Sparkle/tree/2.9.4",
        "paths": (
            "Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle",
        ),
    },
    {
        "name": "LiteLLM pricing data",
        "version": "b45b4b73004261b47369d7d12c97d58b137a732e",
        "license": "MIT",
        "license_file": "THIRD_PARTY_NOTICES.txt",
        "source": (
            "https://github.com/BerriAI/litellm/blob/"
            "b45b4b73004261b47369d7d12c97d58b137a732e/"
            "model_prices_and_context_window.json"
        ),
        "paths": (
            "Contents/Resources/Backend/scanner/pricing_snapshot.json",
        ),
    },
    {
        "name": "@lobehub/icons-static-svg",
        "version": "1.94.0",
        "license": "MIT",
        "license_file": "THIRD_PARTY_NOTICES.txt",
        "source": "https://www.npmjs.com/package/@lobehub/icons-static-svg/v/1.94.0",
        "paths": (
            "Contents/Resources/ProviderLogos/anthropic-lobe.svg",
            "Contents/Resources/ProviderLogos/deepseek-lobe.svg",
            "Contents/Resources/ProviderLogos/google-lobe.svg",
            "Contents/Resources/ProviderLogos/minimax-lobe.svg",
            "Contents/Resources/ProviderLogos/moonshot-lobe.svg",
            "Contents/Resources/ProviderLogos/openai-lobe.svg",
            "Contents/Resources/ProviderLogos/openrouter-lobe.svg",
            "Contents/Resources/ProviderLogos/vercel-lobe.svg",
            "Contents/Resources/ProviderLogos/xai-lobe.svg",
            "Contents/Resources/ProviderLogos/zhipu-lobe.svg",
        ),
    },
)


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


def spdx_id(prefix: str, value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", value).strip("-") or "item"
    return f"SPDXRef-{prefix}-{safe}"


def parse_requirements(path: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            line = line[:-1].strip()
        if line.startswith("--hash="):
            if current is None:
                raise ValueError(f"hash without package in {path}: {raw}")
            algorithm, separator, value = line[7:].partition(":")
            if separator != ":" or algorithm != "sha256" or not re.fullmatch(
                r"[0-9a-f]{64}", value
            ):
                raise ValueError(f"invalid artifact hash in {path}: {raw}")
            current["hash"] = value
            continue
        name, separator, version = line.partition("==")
        if not separator or not name or not version:
            raise ValueError(f"unrecognised requirement in {path}: {raw}")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if current is not None:
            result.append(current)
        current = {
            "name": name,
            "normalized": normalized,
            "version": version,
        }
    if current is not None:
        result.append(current)
    if any("hash" not in item for item in result):
        missing = [item["name"] for item in result if "hash" not in item]
        raise ValueError(f"requirements without sha256 artifact hashes: {missing}")
    return result


def regular_files(bundle: Path) -> list[Path]:
    return sorted(
        path
        for path in bundle.rglob("*")
        if path.is_file()
    )


def read_info(bundle: Path) -> dict[str, Any]:
    info_path = bundle / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise ValueError(f"bundle is missing Contents/Info.plist: {bundle}")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    if not isinstance(info, dict):
        raise ValueError("Contents/Info.plist must contain a dictionary")
    return info


def make_file_entry(bundle: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(bundle).as_posix()
    return {
        "SPDXID": spdx_id("File", relative),
        "fileName": relative,
        "checksums": [{"algorithm": "SHA256", "checksumValue": sha256(path)}],
        "licenseConcluded": "NOASSERTION",
        "licenseInfoInFiles": ["NOASSERTION"],
    }


def package_verification_code(bundle: Path, files: list[dict[str, Any]]) -> str:
    checksums = "".join(
        sorted(sha1(bundle / entry["fileName"]) for entry in files)
    )
    return hashlib.sha1(checksums.encode("ascii")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-label", default=None)
    parser.add_argument("--inventory-output", type=Path)
    parser.add_argument("--requirements", type=Path, default=REQUIREMENTS)
    args = parser.parse_args(argv)

    bundle = args.bundle.resolve()
    if not bundle.is_dir() or bundle.suffix != ".app":
        raise SystemExit(f"bundle must be an existing .app directory: {bundle}")
    info = read_info(bundle)
    version = str(info.get("CFBundleShortVersionString", ""))
    build = str(info.get("CFBundleVersion", ""))
    identifier = str(info.get("CFBundleIdentifier", "com.modeldial.app"))
    if not version or not build:
        raise SystemExit("bundle Info.plist must contain version and build")
    release_label = args.release_label or f"v{version}"
    requirements = parse_requirements(args.requirements.resolve())

    legal_files: dict[str, dict[str, str]] = {}
    packages: list[dict[str, Any]] = []
    for item in RUNTIME_COMPONENTS:
        legal_path = LEGAL_ROOT / str(item["license_file"])
        if not legal_path.is_file() or legal_path.stat().st_size == 0:
            raise SystemExit(f"missing checked-in license evidence: {legal_path}")
        legal_files[str(item["license_file"])] = {
            "path": str(legal_path.relative_to(ROOT)),
            "sha256": sha256(legal_path),
        }
        package_id = spdx_id("Package", str(item["name"]))
        packages.append(
            {
                "SPDXID": package_id,
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": item["source"],
                "licenseConcluded": item["license"],
                "licenseDeclared": item["license"],
                "copyrightText": "NOASSERTION",
                "licenseComments": (
                    f"Complete evidence: Resources/Legal/{item['license_file']}"
                ),
                "filesAnalyzed": False,
            }
        )
    for item in requirements:
        metadata = PYPI_METADATA.get(item["normalized"])
        if metadata is None:
            raise SystemExit(f"no checked-in license metadata for {item['name']}")
        legal_path = LEGAL_ROOT / metadata["license_file"]
        if not legal_path.is_file() or legal_path.stat().st_size == 0:
            raise SystemExit(f"missing checked-in license evidence: {legal_path}")
        legal_files[metadata["license_file"]] = {
            "path": str(legal_path.relative_to(ROOT)),
            "sha256": sha256(legal_path),
        }
        packages.append(
            {
                "SPDXID": spdx_id("Package", f"build-{item['name']}"),
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": metadata["source"],
                "licenseConcluded": metadata["license"],
                "licenseDeclared": metadata["license"],
                "copyrightText": "NOASSERTION",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": item["hash"]}
                ],
                "licenseComments": (
                    f"Build-time wheel hash pinned in {args.requirements}"
                ),
                "filesAnalyzed": False,
            }
        )

    files = [make_file_entry(bundle, path) for path in regular_files(bundle)]
    file_by_name = {entry["fileName"]: entry for entry in files}
    for item in RUNTIME_COMPONENTS:
        missing = [path for path in item["paths"] if path not in file_by_name]
        if missing:
            raise SystemExit(
                f"bundle is missing files for {item['name']}: {', '.join(missing)}"
            )

    app_package_id = spdx_id("Package", "ModelDial")
    verification_code = package_verification_code(bundle, files)
    packages.insert(
        0,
        {
            "SPDXID": app_package_id,
            "name": "ModelDial",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "Copyright 2026 Dong Tianwen",
            "filesAnalyzed": True,
            "packageVerificationCode": {
                "packageVerificationCodeValue": verification_code
            },
            "licenseComments": (
                f"CFBundleVersion={build}; release label={release_label}"
            ),
        },
    )
    relationships = [
        {
            "spdxElementId": app_package_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": entry["SPDXID"],
        }
        for entry in files
    ]
    for package in packages[1:]:
        relationships.append(
            {
                "spdxElementId": app_package_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package["SPDXID"],
            }
        )
    inventory: list[dict[str, Any]] = []
    for item in RUNTIME_COMPONENTS:
        inventory.append(
            {
                "name": item["name"],
                "version": item["version"],
                "license": item["license"],
                "license_file": item["license_file"],
                "runtime_files": [
                    {
                        "path": path,
                        "sha256": file_by_name[path]["checksums"][0][
                            "checksumValue"
                        ],
                    }
                    for path in item["paths"]
                ],
            }
        )

    manifest = "\n".join(
        f"{entry['fileName']} {entry['checksums'][0]['checksumValue']}"
        for entry in files
    ).encode("utf-8")
    namespace_hash = hashlib.sha256(manifest).hexdigest()
    source_date_epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    created = datetime.fromtimestamp(source_date_epoch, timezone.utc).isoformat()
    document: dict[str, Any] = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"modeldial-{release_label.lstrip('v')}-sbom",
        "documentNamespace": (
            f"https://modeldial.com/spdx/{identifier}/{release_label}/"
            f"{namespace_hash}"
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: ModelDial generate-sbom.py"],
        },
        "documentDescribes": [app_package_id],
        "packages": packages,
        "files": files,
        "relationships": relationships,
        "annotations": [
            {
                "annotationType": "OTHER",
                "annotator": "Tool: ModelDial generate-sbom.py",
                "annotationDate": created,
                "comment": (
                    f"CFBundleShortVersionString={version}; "
                    f"CFBundleVersion={build}; release={release_label}"
                ),
            }
        ],
        "comment": (
            "Generated from the supplied .app bundle. Build dependencies are "
            "listed with verified wheel SHA-256 values; runtime file checksums "
            "are observed from the bundle."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.inventory_output:
        args.inventory_output.parent.mkdir(parents=True, exist_ok=True)
        args.inventory_output.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "bundle": str(bundle),
                    "release_label": release_label,
                    "app_version": version,
                    "app_build": build,
                    "legal_files": legal_files,
                    "components": inventory,
                    "build_requirements": requirements,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"generate-sbom: {error}", file=sys.stderr)
        raise SystemExit(1)
