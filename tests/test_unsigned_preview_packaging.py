from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


class UnsignedPreviewPackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.source_path = root / "build-support" / "package-unsigned-preview.sh"
        self.source = self.source_path.read_text(encoding="utf-8")

    def test_packaging_script_is_executable_and_shell_valid(self) -> None:
        self.assertTrue(self.source_path.stat().st_mode & 0o111)
        result = subprocess.run(
            ["bash", "-n", str(self.source_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_invocation_requires_a_fresh_formal_build(self) -> None:
        self.assertIn('MODELDIAL_CODESIGN_IDENTITY=- ./build.sh', self.source)
        self.assertIn('candidate_relative="$(sed -nE', self.source)
        self.assertIn("no existing candidate was packaged", self.source)
        self.assertIn("the only candidate path that", self.source)
        self.assertNotIn("candidate_mtime", self.source)

    def test_preview_injects_and_reads_back_official_reference_feed(self) -> None:
        self.assertIn(
            'MODELDIAL_REFERENCE_SNAPSHOT_URL="https://reference.modeldial.com/reference-snapshots"',
            self.source,
        )
        self.assertIn(
            'REFERENCE_SNAPSHOT_URL="https://reference.modeldial.com/reference-snapshots"',
            self.source,
        )
        self.assertIn("MODELDIAL_DISABLE_UPDATES=0", self.source)
        self.assertIn("preview.1|preview.2|preview.3|preview.4|preview.5)", self.source)
        self.assertIn(
            'fail "$PREVIEW_LABEL is already published and cannot be overwritten"',
            self.source,
        )
        self.assertIn(
            'reference_snapshot_url="$(plist_value ModelDialReferenceSnapshotURL)"',
            self.source,
        )
        self.assertIn(
            '[[ "$reference_snapshot_url" == "$REFERENCE_SNAPSHOT_URL" ]]',
            self.source,
        )
        self.assertIn('preview_feed_url="$(plist_value SUFeedURL)"', self.source)
        self.assertIn('[[ "$preview_feed_url" == "$UPDATE_FEED_URL" ]]', self.source)
        self.assertIn('preview_public_key="$(plist_value SUPublicEDKey)"', self.source)
        self.assertIn('[[ "$preview_public_key" == "$UPDATE_PUBLIC_ED_KEY" ]]', self.source)

    def test_update_enabled_preview_requires_the_official_feed_and_valid_key_pair(self) -> None:
        self.assertIn(
            'OFFICIAL_PREVIEW_UPDATE_FEED_URL="https://updates.modeldial.com/macos/preview/appcast.xml"',
            self.source,
        )
        self.assertIn(
            'UPDATE_FEED_URL="${MODELDIAL_PREVIEW_UPDATE_FEED_URL:-}"',
            self.source,
        )
        self.assertIn(
            'UPDATE_PUBLIC_ED_KEY="${MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY:-}"',
            self.source,
        )
        self.assertIn(
            "MODELDIAL_PREVIEW_UPDATE_FEED_URL and MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY must be provided together",
            self.source,
        )
        self.assertIn("base64.b64decode", self.source)
        self.assertIn("validate=True", self.source)
        self.assertIn("len(decoded) == 32", self.source)
        self.assertIn('export MODELDIAL_UPDATE_FEED_URL="$UPDATE_FEED_URL"', self.source)
        self.assertIn(
            'export MODELDIAL_UPDATE_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY"',
            self.source,
        )

    def test_preview_records_and_rechecks_exact_source_commit_with_clean_tree_gate(self) -> None:
        self.assertIn('source_commit="$(git rev-parse --verify HEAD^{commit})"', self.source)
        self.assertIn('source_commit_after_build="$(git rev-parse --verify HEAD^{commit})"', self.source)
        self.assertIn('[[ "$source_commit_after_build" == "$source_commit" ]]', self.source)
        self.assertIn('ModelDialSourceCommit', self.source)
        self.assertEqual(
            self.source.count("git status --porcelain --untracked-files=normal"),
            2,
        )
        self.assertIn('worktree changed during packaging', self.source)

    def test_preview_rejects_developer_or_local_certificate_signatures(self) -> None:
        self.assertIn(
            'MODELDIAL_CODESIGN_IDENTITY}" != "-"',
            self.source,
        )
        self.assertIn("Signature=adhoc", self.source)
        self.assertIn("Authority=|Developer ID|Apple (Development|Distribution)", self.source)
        self.assertIn("not Developer ID signed and not notarized", self.source)

    def test_artifacts_are_versioned_arm64_dmg_zip_and_checksums(self) -> None:
        self.assertIn('PREVIEW_LABEL="${MODELDIAL_PREVIEW_LABEL:-preview.6}"', self.source)
        self.assertIn('artifact_prefix="modeldial-${version}-${PREVIEW_LABEL}"', self.source)
        self.assertIn('dmg_name="${artifact_prefix}-macos-arm64.dmg"', self.source)
        self.assertIn(
            'zip_name="${artifact_prefix}-build-${build_number}-macos-arm64.zip"',
            self.source,
        )
        self.assertIn('sbom_name="${artifact_prefix}-sbom.spdx.json"', self.source)
        self.assertIn('sums_name="SHA256SUMS"', self.source)
        self.assertIn('[[ "$architectures" == "arm64" ]]', self.source)
        self.assertIn("hdiutil create", self.source)
        self.assertIn('hdiutil verify -quiet "$dmg_path"', self.source)
        self.assertIn("ditto -c -k --sequesterRsrc --keepParent", self.source)
        self.assertIn('unzip -t "$zip_path"', self.source)
        self.assertIn('unzip -q "$zip_path" -d "$zip_verify_dir"', self.source)
        self.assertIn('zip_version="$(/usr/bin/plutil -extract CFBundleShortVersionString', self.source)
        self.assertIn('[[ "$zip_architectures" == "arm64" ]]', self.source)
        self.assertIn(
            'shasum -a 256 "$dmg_name" "$zip_name" "$sbom_name"', self.source
        )

    def test_sandboxed_sbom_generation_and_verification_are_fail_closed(self) -> None:
        self.assertIn("build-support/generate-sbom.py", self.source)
        self.assertIn("--release-label \"v${version}-${PREVIEW_LABEL}\"", self.source)
        self.assertIn("--inventory-output \"$inventory_path\"", self.source)
        self.assertIn("build-support/verify-sbom.py", self.source)
        self.assertIn("--sbom \"$sbom_path\"", self.source)
        self.assertIn("SBOM generation failed; no preview artifacts were packaged", self.source)
        self.assertIn("SBOM verification failed; no preview artifacts were packaged", self.source)

    def test_failed_packaging_cleans_only_currently_named_artifacts(self) -> None:
        self.assertIn("packaging_succeeded=0", self.source)
        self.assertIn("if (( packaging_succeeded == 0 )); then", self.source)
        for path_name in (
            "dmg_path",
            "zip_path",
            "sbom_path",
            "sums_path",
            "inventory_path",
        ):
            self.assertIn(f'[[ -z "${{{path_name}:-}}" ]] || rm -f "${path_name}"', self.source)
        self.assertIn("packaging_succeeded=1", self.source)

    def test_dmg_staging_explains_manual_gatekeeper_override(self) -> None:
        self.assertIn('ln -s /Applications "$staging_dir/Applications"', self.source)
        self.assertIn('cat > "$staging_dir/UNSIGNED_PREVIEW.txt" <<EOF', self.source)
        self.assertIn("Drag modeldial.app to Applications", self.source)
        self.assertIn("System Settings > Privacy & Security > Open Anyway", self.source)
        self.assertIn("Do not disable Gatekeeper", self.source)
        self.assertIn("xattr or spctl workarounds", self.source)
        self.assertIn("系统设置 → 隐私与安全性 → 仍要打开", self.source)

    def test_no_remote_release_or_signing_work_is_embedded(self) -> None:
        forbidden = ("git tag", "gh release", "gh upload", "notarytool", "xcrun notarytool")
        for token in forbidden:
            self.assertNotIn(token, self.source)


if __name__ == "__main__":
    unittest.main()
