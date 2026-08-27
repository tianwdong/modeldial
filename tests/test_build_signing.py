from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest


class BuildSigningTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.root = root
        self.source = (root / "build.sh").read_text(encoding="utf-8")
        self.pyinstaller_requirements = (
            root / "build-support" / "pyinstaller-requirements.txt"
        ).read_text(encoding="utf-8")
        self.python_runtime_lock = json.loads(
            (root / "build-support" / "python-runtime.lock.json").read_text(
                encoding="utf-8"
            )
        )
        self.compatibility_script = (
            root / "build-support" / "verify-macos-bundle-compatibility.sh"
        )
        self.compatibility_source = self.compatibility_script.read_text(
            encoding="utf-8"
        )
        self.readme = (root / "README.md").read_text(encoding="utf-8")
        self.readme_en = (root / "README.en.md").read_text(encoding="utf-8")
        self.dev_source = (root / "build-dev.sh").read_text(encoding="utf-8")
        self.signing_source = (
            root / "build-support" / "sign-app-bundle.sh"
        ).read_text(encoding="utf-8")
        self.project_source = (
            root / "ModelDial.xcodeproj" / "project.pbxproj"
        ).read_text(encoding="utf-8")
        self.info_plist_source = (root / "Resources" / "Info.plist").read_text(
            encoding="utf-8"
        )
        self.info_plist = plistlib.loads(
            (root / "Resources" / "Info.plist").read_bytes()
        )
        self.native_bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

    def test_app_bundle_is_explicitly_signed_with_configurable_identity(self) -> None:
        self.assertIn('APP_NAME="modeldial"', self.source)
        self.assertIn('BUNDLE_ID="com.modeldial.app"', self.source)
        self.assertIn("<key>CFBundleDisplayName</key>", self.info_plist_source)
        self.assertIn("<string>modeldial</string>", self.info_plist_source)
        self.assertIn('CODESIGN_IDENTITY="${MODELDIAL_CODESIGN_IDENTITY:--}"', self.source)
        self.assertIn('security find-identity -v -p codesigning', self.source)
        self.assertIn('RESOLVED_CODESIGN_IDENTITY=', self.source)
        self.assertIn(
            'sign_app_bundle "$APP_DIR" "$RESOLVED_CODESIGN_IDENTITY" "$BUNDLE_ID"',
            self.source,
        )
        self.assertIn('codesign --force --sign "$identity"', self.signing_source)
        self.assertIn('--identifier "$bundle_id"', self.signing_source)
        self.assertIn(
            'codesign --verify --deep --strict --verbose=2 "$app_dir"',
            self.signing_source,
        )

    def test_build_strips_host_extended_attributes_before_signing(self) -> None:
        cleanup = '/usr/bin/xattr -cr "$APP_DIR"'
        signing = 'sign_app_bundle "$APP_DIR" "$RESOLVED_CODESIGN_IDENTITY" "$BUNDLE_ID"'

        self.assertIn(cleanup, self.source)
        self.assertLess(self.source.index(cleanup), self.source.index(signing))

    def test_xcode_owns_the_app_binary_and_version(self) -> None:
        self.assertIn('-project "ModelDial.xcodeproj"', self.source)
        self.assertIn('-configuration "Release"', self.source)
        self.assertNotIn("swiftc", self.source)
        self.assertIn("CURRENT_PROJECT_VERSION = 115;", self.project_source)
        self.assertIn("MARKETING_VERSION = 0.1.0;", self.project_source)
        for resource in (
            "AppIcon.icns in Resources",
            "Legal in Resources",
            "Localizable.xcstrings in Resources",
            "ModeldialShareMark.svg in Resources",
            "ModeldialWordmark.svg in Resources",
            "ProviderLogos in Resources",
        ):
            self.assertIn(resource, self.project_source)
        self.assertIn(
            "<string>$(CURRENT_PROJECT_VERSION)</string>", self.info_plist_source
        )
        self.assertIn("<string>$(MARKETING_VERSION)</string>", self.info_plist_source)

    def test_sparkle_is_pinned_and_signed_inside_out(self) -> None:
        self.assertIn(
            'repositoryURL = "https://github.com/sparkle-project/Sparkle";',
            self.project_source,
        )
        self.assertIn("version = 2.9.4;", self.project_source)
        for nested_path in (
            "XPCServices/Downloader.xpc",
            "XPCServices/Installer.xpc",
            "Updater.app",
            "Autoupdate",
        ):
            self.assertIn(nested_path, self.signing_source)
        self.assertLess(
            self.signing_source.index("XPCServices/Downloader.xpc"),
            self.signing_source.rindex('"$sparkle_framework"'),
        )

    def test_duplicate_certificate_names_resolve_to_valid_identity_hash(self) -> None:
        self.assertIn("awk -v requested=\"$CODESIGN_IDENTITY\"", self.source)
        self.assertIn("$2 == requested", self.source)
        self.assertIn('index($0, "\\\"" requested "\\\"")', self.source)

    def test_open_source_build_uses_adhoc_without_requesting_hardened_runtime(self) -> None:
        self.assertIn('if [[ "$CODESIGN_IDENTITY" == "-" ]]', self.source)
        self.assertIn('RESOLVED_CODESIGN_IDENTITY="-"', self.source)
        self.assertIn("PYINSTALLER_CODESIGN_ARGS=()", self.source)
        self.assertIn(
            'if [[ "$RESOLVED_CODESIGN_IDENTITY" != "-" ]]',
            self.source,
        )
        self.assertIn(
            'PYINSTALLER_CODESIGN_ARGS=(--codesign-identity "$RESOLVED_CODESIGN_IDENTITY")',
            self.source,
        )
        self.assertIn(
            '${PYINSTALLER_CODESIGN_ARGS[@]+"${PYINSTALLER_CODESIGN_ARGS[@]}"}',
            self.source,
        )
        self.assertIn("signing_policy=(--timestamp=none)", self.signing_source)
        self.assertIn(
            "signing_policy=(--options runtime --timestamp)",
            self.signing_source,
        )
        self.assertIn("No matching modeldial code-signing identity found", self.source)

    def test_adhoc_app_signature_does_not_enable_hardened_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "Test.app"
            executable = app / "Contents" / "MacOS" / "Test"
            executable.parent.mkdir(parents=True)
            plist_path = app / "Contents" / "Info.plist"
            plist_path.write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleExecutable": "Test",
                        "CFBundleIdentifier": "com.modeldial.tests.adhoc",
                        "CFBundlePackageType": "APPL",
                    }
                )
            )
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            subprocess.run(
                ["xcrun", "clang", str(source), "-o", str(executable)],
                check=True,
                capture_output=True,
                text=True,
            )
            signing = subprocess.run(
                [
                    "bash",
                    "-c",
                    'set -euo pipefail; source "$1"; sign_app_bundle "$2" - com.modeldial.tests.adhoc',
                    "bash",
                    str(self.root / "build-support" / "sign-app-bundle.sh"),
                    str(app),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, signing.returncode, signing.stderr)
            signature = subprocess.run(
                ["codesign", "-dv", "--verbose=4", str(executable)],
                check=True,
                capture_output=True,
                text=True,
            ).stderr
            self.assertIn("Signature=adhoc", signature)
            self.assertNotIn("runtime", signature)

            policy_script = (
                self.root / "build-support" / "verify-adhoc-signing-policy.sh"
            )
            self.assertTrue(policy_script.is_file())
            valid = subprocess.run(
                ["bash", str(policy_script), str(app)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, valid.returncode, valid.stderr)

            subprocess.run(
                [
                    "codesign",
                    "--force",
                    "--sign",
                    "-",
                    "--options",
                    "runtime",
                    "--timestamp=none",
                    str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            invalid = subprocess.run(
                ["bash", str(policy_script), str(app)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, invalid.returncode)
            self.assertIn("hardened runtime", invalid.stderr)

        self.assertIn(
            'verify-adhoc-signing-policy.sh" "$APP_DIR"',
            self.source,
        )

    def test_app_bundle_contains_the_python_backend_and_question_pack(self) -> None:
        self.assertIn('BACKEND_DIR="$RES_DIR/Backend"', self.source)
        self.assertIn('cp -R "scanner" "$BACKEND_DIR/scanner"', self.source)
        self.assertIn(
            'RUNTIME_SCRIPT_NAMES=(\n'
            '  "native_bridge.py"\n'
            '  "modeldial_session_hook.py"\n'
            '  "install_session_observer.py"\n'
            ')',
            self.source,
        )
        self.assertIn('mkdir -p "$BACKEND_DIR/scripts"', self.source)
        self.assertIn(
            'cp "scripts/$script_name" "$BACKEND_DIR/scripts/$script_name"',
            self.source,
        )
        self.assertNotIn('cp -R "scripts" "$BACKEND_DIR/scripts"', self.source)
        for development_script in (
            "verify_advisor_decoding.sh",
            "verify_glance_resolver.sh",
        ):
            self.assertNotIn(f'"{development_script}"', self.source)
        self.assertIn('cp -R "questions" "$BACKEND_DIR/questions"', self.source)
        self.assertIn(
            'find "$BACKEND_DIR" -type f -name \'.DS_Store\' -delete',
            self.source,
        )

    def test_source_build_defaults_to_no_reference_snapshot_feed(self) -> None:
        self.assertEqual(
            self.info_plist["ModelDialReferenceSnapshotURL"],
            "$(MODELDIAL_REFERENCE_SNAPSHOT_URL)",
        )
        self.assertEqual(
            self.project_source.count('MODELDIAL_REFERENCE_SNAPSHOT_URL = "";'),
            2,
        )
        for setting in (
            "MODELDIAL_SOURCE_COMMIT",
            "MODELDIAL_SU_FEED_URL",
            "MODELDIAL_SU_PUBLIC_ED_KEY",
        ):
            self.assertEqual(self.project_source.count(f'{setting} = "";'), 2)
        for build_source in (self.source, self.dev_source):
            self.assertIn(
                'REFERENCE_SNAPSHOT_URL="${MODELDIAL_REFERENCE_SNAPSHOT_URL:-}"',
                build_source,
            )
            self.assertIn(
                'MODELDIAL_REFERENCE_SNAPSHOT_URL="$REFERENCE_SNAPSHOT_URL"',
                build_source,
            )
            self.assertIn(
                'UPDATE_FEED_URL="${MODELDIAL_UPDATE_FEED_URL:-}"',
                build_source,
            )
            self.assertIn(
                'MODELDIAL_SU_FEED_URL="$UPDATE_FEED_URL"',
                build_source,
            )
            self.assertIn(
                'MODELDIAL_SU_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY"',
                build_source,
            )
            self.assertIn(
                'SOURCE_COMMIT="${MODELDIAL_SOURCE_COMMIT:-}"',
                build_source,
            )
            self.assertIn(
                'MODELDIAL_SOURCE_COMMIT="$SOURCE_COMMIT"',
                build_source,
            )
        self.assertIn(
            'forInfoDictionaryKey: "ModelDialReferenceSnapshotURL"',
            self.native_bridge_source,
        )
        self.assertIn(
            'environment["MODELDIAL_REFERENCE_SNAPSHOT_URL"]',
            self.native_bridge_source,
        )
        self.assertIn('<key>ModelDialSourceCommit</key>', self.info_plist_source)
        self.assertIn(
            '<string>$(MODELDIAL_SOURCE_COMMIT)</string>',
            self.info_plist_source,
        )

    def test_preview_build_contract_is_explicit_without_changing_source_default(self) -> None:
        preview_source = (
            self.root / "build-support" / "package-unsigned-preview.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'REFERENCE_SNAPSHOT_URL="https://reference.modeldial.com/reference-snapshots"',
            preview_source,
        )
        self.assertIn("MODELDIAL_DISABLE_UPDATES=0", preview_source)
        self.assertIn(
            'MODELDIAL_UPDATE_FEED_URL="$UPDATE_FEED_URL"',
            preview_source,
        )
        self.assertIn(
            'MODELDIAL_UPDATE_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY"',
            preview_source,
        )
        self.assertIn(
            'MODELDIAL_DISABLE_UPDATES="${MODELDIAL_DISABLE_UPDATES:-0}"',
            self.source,
        )
        self.assertIn('if [[ "$MODELDIAL_DISABLE_UPDATES" == "1" ]]', self.source)
        self.assertIn(
            'MODELDIAL_SU_FEED_URL="$UPDATE_FEED_URL"',
            self.source,
        )
        self.assertIn(
            'MODELDIAL_SU_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY"',
            self.source,
        )

    def test_app_bundle_contains_a_standalone_python_backend_runtime(self) -> None:
        self.assertIn(
            'PYTHON_RUNTIME_LOCK="build-support/python-runtime.lock.json"',
            self.source,
        )
        self.assertIn(
            'PYINSTALLER_REQUIREMENTS="build-support/pyinstaller-requirements.txt"',
            self.source,
        )
        self.assertIn(
            'python_runtime_env "$BUILD_PYTHON" -m venv "$PYINSTALLER_ENV"',
            self.source,
        )
        self.assertNotIn("HOST_PYTHON_VERSION", self.source)
        self.assertIn("pyinstaller_env_matches_lock", self.source)
        self.assertIn('rm -rf "$PYINSTALLER_ENV"', self.source)
        self.assertIn('--requirement "$PYINSTALLER_REQUIREMENTS"', self.source)
        self.assertIn('"$PYINSTALLER_PYTHON" -m pip check', self.source)
        self.assertIn('-m PyInstaller', self.source)
        self.assertIn('--onedir', self.source)
        self.assertIn('"${PYINSTALLER_CODESIGN_ARGS[@]}"', self.source)
        self.assertIn('--target-arch "$PYTHON_TARGET_ARCH"', self.source)
        for runtime_library in (
            "libcrypto.3.dylib",
            "libssl.3.dylib",
            "libzstd.1.dylib",
        ):
            self.assertIn(f'"{runtime_library}"', self.source)
        self.assertIn('PYINSTALLER_RUNTIME_BINARY_ARGS+=(--add-binary', self.source)
        self.assertIn('--hidden-import "compression.zstd"', self.source)
        self.assertIn('--hidden-import "certifi"', self.source)
        self.assertIn('--modeldial-python-code', self.source)
        self.assertIn("compression.zstd.compress(payload)", self.source)
        self.assertIn("hashlib.sha256(payload)", self.source)
        self.assertIn("ssl.OPENSSL_VERSION", self.source)
        self.assertIn("ssl.get_default_verify_paths()", self.source)
        self.assertIn('cert_store_stats()["x509_ca"] > 0', self.source)
        self.assertIn("from scanner.endpoint_client import _default_endpoint_opener", self.source)
        self.assertIn("_default_endpoint_opener().handlers", self.source)
        self.assertIn("any(context is not None", self.source)
        self.assertIn('BACKEND_RUNTIME_DIR="$BACKEND_DIR/Runtime"', self.source)
        self.assertIn('modeldial-backend', self.source)

    def test_build_dependencies_are_artifact_hashed(self) -> None:
        self.assertIn("--require-hashes", self.source)
        expected_packages = {
            "altgraph",
            "certifi",
            "macholib",
            "packaging",
            "pip",
            "pyinstaller",
            "pyinstaller-hooks-contrib",
            "setuptools",
        }
        requirements = self.pyinstaller_requirements.splitlines()
        package_names = set()
        for line in requirements:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("--hash=") or stripped == "\\":
                continue
            package, separator, version = stripped.partition("==")
            self.assertEqual("==", separator, stripped)
            self.assertTrue(version, stripped)
            package_names.add(package.lower())
        self.assertEqual(expected_packages, package_names)
        for package in expected_packages:
            package_line = next(
                line
                for line in requirements
                if line.lower().startswith(f"{package}==")
            )
            index = requirements.index(package_line)
            self.assertRegex(
                "\n".join(requirements[index : index + 2]),
                r"--hash=sha256:[0-9a-f]{64}",
            )

    def test_build_requirements_receipt_is_checked_before_reuse(self) -> None:
        runtime_python = self.root / "build" / "pyinstaller-env" / "bin" / "python3"
        runtime_root = self.root / "build" / "python-runtime-3.14.3"
        if not runtime_python.is_file() or not runtime_root.is_dir():
            self.skipTest("requires the project-local frozen Python build environment")
        marker = 'python_runtime_env "$PYINSTALLER_PYTHON"'
        embedded_source = self.source[self.source.index(marker) :]
        embedded_source = embedded_source.split("<<'PY'\n", 1)[1].split(
            "\nPY\n}", 1
        )[0]
        requirements = self.root / "build-support" / "pyinstaller-requirements.txt"
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "requirements.sha256"
            receipt.write_text(
                hashlib.sha256(requirements.read_bytes()).hexdigest() + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "MODELDIAL_REQUIRED_PYTHON": "3.14.3",
                    "MODELDIAL_REQUIRED_BASE_PREFIX": str(
                        runtime_root / "Python.framework" / "Versions" / "3.14"
                    ),
                    "MODELDIAL_REQUIRED_BASE_EXECUTABLE": str(
                        runtime_root
                        / "Python.framework"
                        / "Versions"
                        / "3.14"
                        / "bin"
                        / "python3.14"
                    ),
                    "DYLD_FRAMEWORK_PATH": str(runtime_root),
                    "DYLD_LIBRARY_PATH": str(
                        runtime_root / "Python.framework" / "Versions" / "3.14" / "lib"
                    ),
                }
            )
            valid = subprocess.run(
                [str(runtime_python), "-", str(requirements), str(receipt)],
                input=embedded_source,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            receipt.write_text("0" * 64 + "\n", encoding="utf-8")
            invalid = subprocess.run(
                [str(runtime_python), "-", str(requirements), str(receipt)],
                input=embedded_source,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            receipt.unlink()
            missing = subprocess.run(
                [str(runtime_python), "-", str(requirements), str(receipt)],
                input=embedded_source,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_python_runtime_source_is_immutable_and_project_local(self) -> None:
        self.assertEqual(
            self.python_runtime_lock,
            {
                "deployment_target": "13.0",
                "framework_version": "3.14",
                "installer_filename": "python-3.14.3-macos11.pkg",
                "installer_signer": (
                    "Developer ID Installer: Python Software Foundation "
                    "(BMM5U3QVKW)"
                ),
                "release_page": (
                    "https://www.python.org/downloads/release/python-3143/"
                ),
                "schema_version": 1,
                "sha256": (
                    "50b709f72cb5ed87d5882901923face9"
                    "81dd657569717761832c36db3bf08238"
                ),
                "target_arch": "arm64",
                "url": (
                    "https://www.python.org/ftp/python/3.14.3/"
                    "python-3.14.3-macos11.pkg"
                ),
                "version": "3.14.3",
            },
        )
        self.assertIn("python_installer_matches_lock", self.source)
        self.assertIn("pkgutil --check-signature", self.source)
        self.assertIn('grep -Fq "Notarization: trusted"', self.source)
        self.assertIn("shasum -a 256", self.source)
        self.assertIn("pkgutil --expand", self.source)
        self.assertIn('ditto -x "$framework_payload"', self.source)
        self.assertIn("DYLD_FRAMEWORK_PATH=", self.source)
        self.assertIn("DYLD_LIBRARY_PATH=", self.source)
        self.assertIn('Path(sys.prefix) / "pyvenv.cfg"', self.source)
        self.assertIn("MODELDIAL_REQUIRED_BASE_EXECUTABLE", self.source)
        self.assertNotIn("installer -pkg", self.source)

    def test_bundle_compatibility_gate_is_executable_and_fail_closed(self) -> None:
        self.assertIn(
            '"./build-support/verify-macos-bundle-compatibility.sh" \\\n'
            '  "$APP_DIR" \\\n'
            '  "$PYTHON_DEPLOYMENT_TARGET"',
            self.source,
        )
        self.assertIn("LC_VERSION_MIN_MACOSX", self.compatibility_source)
        self.assertIn("platform MACOS", self.compatibility_source)
        self.assertIn("non-system absolute dependency", self.compatibility_source)

        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir) / "Compatibility.app"
            executable_dir = app_dir / "Contents" / "MacOS"
            executable_dir.mkdir(parents=True)
            with (app_dir / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump({"LSMinimumSystemVersion": "13.0"}, handle)
            executable = executable_dir / "compatibility"
            source = "int main(void) { return 0; }\n"

            subprocess.run(
                [
                    "xcrun",
                    "clang",
                    "-x",
                    "c",
                    "-",
                    "-mmacosx-version-min=13.0",
                    "-o",
                    str(executable),
                ],
                input=source,
                text=True,
                check=True,
            )
            accepted = subprocess.run(
                [str(self.compatibility_script), str(app_dir), "13.0"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            mismatched_lock = subprocess.run(
                [str(self.compatibility_script), str(app_dir), "12.0"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(mismatched_lock.returncode, 1)
            self.assertIn("does not match the locked deployment target", mismatched_lock.stderr)

            subprocess.run(
                [
                    "xcrun",
                    "clang",
                    "-x",
                    "c",
                    "-",
                    "-mmacosx-version-min=14.0",
                    "-o",
                    str(executable),
                ],
                input=source,
                text=True,
                check=True,
            )
            rejected = subprocess.run(
                [str(self.compatibility_script), str(app_dir), "13.0"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("exceeds 13.0", rejected.stderr)

    def test_python_build_dependencies_are_exactly_locked(self) -> None:
        expected = {
            "altgraph": "0.17.5",
            "certifi": "2026.7.22",
            "macholib": "1.16.4",
            "packaging": "26.2",
            "pip": "26.0",
            "pyinstaller": "6.21.0",
            "pyinstaller-hooks-contrib": "2026.6",
            "setuptools": "83.0.0",
        }
        actual = {}
        for raw_line in self.pyinstaller_requirements.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("--hash=") or line == "\\":
                continue
            line = line.rstrip("\\").strip()
            name, separator, version = line.partition("==")
            self.assertEqual(separator, "==")
            self.assertNotIn(name, actual)
            actual[name] = version
        self.assertEqual(actual, expected)

    def test_readmes_document_the_locked_python_build_runtime(self) -> None:
        for readme in (self.readme, self.readme_en):
            self.assertIn("Python 3.14.3", readme)
            self.assertIn("build-support/pyinstaller-requirements.txt", readme)
            self.assertIn("PyInstaller 6.21.0", readme)

    def test_live_app_bundle_is_never_deleted_during_a_build(self) -> None:
        self.assertIn('STAGING_DIR="$BUILD_DIR/.modeldial-build-$$"', self.source)
        self.assertIn('CANDIDATE_APP_DIR="$BUILD_DIR/$APP_NAME-candidate.app"', self.source)
        self.assertIn('ps -Ao command=', self.source)
        self.assertIn('live $LIVE_APP_DIR was left untouched', self.source)
        self.assertNotIn('rm -rf "$BUILD_DIR"', self.source)
        self.assertNotIn('rm -rf "$LIVE_APP_DIR"', self.source)
        self.assertNotIn('mv "$APP_DIR" "$LIVE_APP_DIR"', self.source)
        self.assertNotIn('LIVE_EXECUTABLE=', self.source)

    def test_running_candidate_bundle_is_also_never_replaced(self) -> None:
        self.assertIn('CANDIDATE_EXECUTABLE=', self.source)
        self.assertIn('is_app_running "$CANDIDATE_EXECUTABLE"', self.source)
        self.assertIn('SAFE_CANDIDATE_APP_DIR=', self.source)
        self.assertIn('running candidate $CANDIDATE_APP_DIR were left untouched', self.source)

    def test_native_dev_build_reuses_frozen_backend_without_promoting_live_app(self) -> None:
        self.assertIn(
            'CANDIDATE_APP_DIR="$BUILD_DIR/$APP_NAME-candidate.app"',
            self.dev_source,
        )
        self.assertIn('LIVE_APP_DIR="$BUILD_DIR/$APP_NAME.app"', self.dev_source)
        self.assertIn('BASE_APP_DIR="$CANDIDATE_APP_DIR"', self.dev_source)
        self.assertIn('BASE_APP_DIR="$LIVE_APP_DIR"', self.dev_source)
        self.assertIn('DEV_APP_DIR="$BUILD_DIR/$APP_NAME-dev.app"', self.dev_source)
        self.assertIn('This development build reuses the frozen backend', self.dev_source)
        self.assertIn('-configuration "Debug"', self.dev_source)
        self.assertIn('SWIFT_OPTIMIZATION_LEVEL = "-Onone";', self.project_source)
        self.assertIn('sign_app_bundle "$APP_DIR"', self.dev_source)
        self.assertNotIn('mv "$APP_DIR" "$LIVE_APP_DIR"', self.dev_source)


if __name__ == "__main__":
    unittest.main()
