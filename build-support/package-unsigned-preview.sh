#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

fail() {
  echo "unsigned preview packaging failed: $*" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "macOS is required"
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  fail "Apple Silicon arm64 is required"
fi

if [[ "${MODELDIAL_CODESIGN_IDENTITY+x}" == x && "${MODELDIAL_CODESIGN_IDENTITY}" != "-" ]]; then
  fail "unsigned preview refuses MODELDIAL_CODESIGN_IDENTITY=${MODELDIAL_CODESIGN_IDENTITY}; it never packages a Developer ID or local development signature"
fi

BUILD_ROOT="$ROOT_DIR/build"
OUTPUT_DIR="${MODELDIAL_UNSIGNED_PREVIEW_OUTPUT_DIR:-$BUILD_ROOT/unsigned-preview}"
PREVIEW_LABEL="${MODELDIAL_PREVIEW_LABEL:-preview.9}"
MODELDIAL_REFERENCE_SNAPSHOT_URL="https://reference.modeldial.com/reference-snapshots"
REFERENCE_SNAPSHOT_URL="$MODELDIAL_REFERENCE_SNAPSHOT_URL"
OFFICIAL_PREVIEW_UPDATE_FEED_URL="https://updates.modeldial.com/macos/preview/appcast.xml"
UPDATE_FEED_URL="${MODELDIAL_PREVIEW_UPDATE_FEED_URL:-}"
UPDATE_PUBLIC_ED_KEY="${MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY:-}"
[[ "$PREVIEW_LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] \
  || fail "invalid preview label: $PREVIEW_LABEL"
case "$PREVIEW_LABEL" in
  preview.1|preview.2|preview.3|preview.4|preview.5|preview.6|preview.7|preview.8)
    fail "$PREVIEW_LABEL is already published and cannot be overwritten"
    ;;
esac
if [[ -n "$UPDATE_FEED_URL" && -z "$UPDATE_PUBLIC_ED_KEY" ]] \
  || [[ -z "$UPDATE_FEED_URL" && -n "$UPDATE_PUBLIC_ED_KEY" ]]; then
  fail "MODELDIAL_PREVIEW_UPDATE_FEED_URL and MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY must be provided together"
fi
if [[ -n "$UPDATE_FEED_URL" && "$UPDATE_FEED_URL" != "$OFFICIAL_PREVIEW_UPDATE_FEED_URL" ]]; then
  fail "update-enabled previews must use the official preview appcast"
fi
if [[ -n "$UPDATE_PUBLIC_ED_KEY" ]]; then
  MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY" python3 - <<'PY' \
    || fail "MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY must be a base64-encoded 32-byte Ed25519 public key"
import base64
import binascii
import os

try:
    decoded = base64.b64decode(
        os.environ["MODELDIAL_PREVIEW_UPDATE_PUBLIC_ED_KEY"],
        validate=True,
    )
except (binascii.Error, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if len(decoded) == 32 else 1)
PY
fi

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  fail "worktree must be clean before packaging an unsigned preview"
fi
source_commit="$(git rev-parse --verify HEAD^{commit})" \
  || fail "could not resolve the source Git commit"
[[ "$source_commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] \
  || fail "source Git commit is not an exact 40- or 64-character SHA"

mkdir -p "$BUILD_ROOT" "$OUTPUT_DIR"

build_log="$(mktemp "${TMPDIR:-/tmp}/modeldial-unsigned-preview-build.XXXXXX")"
staging_dir="$(mktemp -d "$BUILD_ROOT/.modeldial-unsigned-preview-staging.XXXXXX")"
zip_verify_dir=""
packaging_succeeded=0
cleanup() {
  rm -f "$build_log"
  rm -rf "$staging_dir"
  if [[ -n "$zip_verify_dir" ]]; then
    rm -rf "$zip_verify_dir"
  fi
  if (( packaging_succeeded == 0 )); then
    [[ -z "${dmg_path:-}" ]] || rm -f "$dmg_path"
    [[ -z "${zip_path:-}" ]] || rm -f "$zip_path"
    [[ -z "${sbom_path:-}" ]] || rm -f "$sbom_path"
    [[ -z "${sums_path:-}" ]] || rm -f "$sums_path"
    [[ -z "${inventory_path:-}" ]] || rm -f "$inventory_path"
  fi
}
trap cleanup EXIT

# A fresh full Release-configuration build is mandatory. In particular, do not
# fall back to an existing build/modeldial-candidate.app when this invocation
# fails. The successful build invocation reports the only candidate path that
# this packaging run will accept.
export MODELDIAL_REFERENCE_SNAPSHOT_URL="$REFERENCE_SNAPSHOT_URL"
export MODELDIAL_UPDATE_FEED_URL="$UPDATE_FEED_URL"
export MODELDIAL_UPDATE_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY"
export MODELDIAL_DISABLE_UPDATES=0
export MODELDIAL_SOURCE_COMMIT="$source_commit"
if ! MODELDIAL_CODESIGN_IDENTITY=- ./build.sh 2>&1 | tee "$build_log"; then
  fail "./build.sh did not complete; no existing candidate was packaged"
fi
source_commit_after_build="$(git rev-parse --verify HEAD^{commit})" \
  || fail "could not re-read the source Git commit after building"
[[ "$source_commit_after_build" == "$source_commit" ]] \
  || fail "source Git commit changed during packaging"
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  fail "worktree changed during packaging"
fi

candidate_relative="$(sed -nE 's/^built (.+); .*/\1/p' "$build_log" | tail -n 1)"
[[ -n "$candidate_relative" ]] || fail "./build.sh did not report a candidate path"
case "$candidate_relative" in
  /*) candidate_path="$candidate_relative" ;;
  *) candidate_path="$ROOT_DIR/${candidate_relative#./}" ;;
esac
[[ -d "$candidate_path" ]] || fail "fresh build candidate does not exist: $candidate_path"
candidate_path="$(cd "$candidate_path" && pwd -P)"

build_root_real="$(cd "$BUILD_ROOT" && pwd -P)"
case "$candidate_path/" in
  "$build_root_real"/*) ;;
  *) fail "build output escaped the repository build directory: $candidate_path" ;;
esac
plist_value() {
  /usr/bin/plutil -extract "$1" raw -o - "$candidate_path/Contents/Info.plist"
}

[[ -f "$candidate_path/Contents/Info.plist" ]] || fail "candidate is missing Contents/Info.plist"
app_name="$(plist_value CFBundleName)"
bundle_id="$(plist_value CFBundleIdentifier)"
version="$(plist_value CFBundleShortVersionString)"
build_number="$(plist_value CFBundleVersion)"
executable_name="$(plist_value CFBundleExecutable)"
candidate_source_commit="$(plist_value ModelDialSourceCommit)" \
  || fail "candidate is missing ModelDialSourceCommit"
[[ "$candidate_source_commit" == "$source_commit" ]] \
  || fail "candidate source commit does not match the exact packaging commit"
reference_snapshot_url="$(plist_value ModelDialReferenceSnapshotURL)" \
  || fail "candidate is missing ModelDialReferenceSnapshotURL"
[[ "$reference_snapshot_url" == "$REFERENCE_SNAPSHOT_URL" ]] \
  || fail "candidate reference snapshot URL is not the official HTTPS feed"
preview_feed_url="$(plist_value SUFeedURL)" \
  || fail "candidate is missing SUFeedURL"
[[ "$preview_feed_url" == "$UPDATE_FEED_URL" ]] \
  || fail "candidate update feed does not match the requested preview channel"
preview_public_key="$(plist_value SUPublicEDKey)" \
  || fail "candidate is missing SUPublicEDKey"
[[ "$preview_public_key" == "$UPDATE_PUBLIC_ED_KEY" ]] \
  || fail "candidate update public key does not match the requested preview channel"
[[ "$app_name" == "modeldial" ]] || fail "unexpected app name in candidate: $app_name"
[[ "$bundle_id" == "com.modeldial.app" ]] || fail "unexpected bundle identifier in candidate: $bundle_id"
[[ "$version" =~ ^[0-9]+([.][0-9]+){1,2}$ ]] || fail "invalid marketing version: $version"
[[ "$build_number" =~ ^[0-9]+$ ]] || fail "invalid build number: $build_number"
main_executable="$candidate_path/Contents/MacOS/$executable_name"
[[ -x "$main_executable" ]] || fail "candidate is missing executable: $main_executable"

architectures="$(lipo -archs "$main_executable" 2>/dev/null)" || fail "could not inspect candidate architecture"
[[ "$architectures" == "arm64" ]] || fail "candidate is not a thin arm64 build: $architectures"

# The formal build signs with '-' (ad-hoc) so nested code remains verifiable,
# but this preview must never imply Developer ID signing or notarization.
signature_details="$(codesign -dv --verbose=4 "$candidate_path" 2>&1)" \
  || fail "candidate has no verifiable code signature"
if grep -Eiq 'Authority=|Developer ID|Apple (Development|Distribution)|3rd Party Mac Developer|Mac Developer' <<<"$signature_details"; then
  fail "candidate carries a certificate identity; unsigned preview refuses to present it as Developer ID"
fi
grep -Fq 'Signature=adhoc' <<<"$signature_details" \
  || fail "candidate is not ad-hoc signed; unsigned preview requires the formal build's '-' identity"
codesign --verify --deep --strict --verbose=2 "$candidate_path" >/dev/null \
  || fail "candidate deep signature verification failed"

artifact_prefix="modeldial-${version}-${PREVIEW_LABEL}"
dmg_name="${artifact_prefix}-macos-arm64.dmg"
zip_name="${artifact_prefix}-build-${build_number}-macos-arm64.zip"
sbom_name="${artifact_prefix}-sbom.spdx.json"
sums_name="SHA256SUMS"
dmg_path="$OUTPUT_DIR/$dmg_name"
zip_path="$OUTPUT_DIR/$zip_name"
sbom_path="$OUTPUT_DIR/$sbom_name"
sums_path="$OUTPUT_DIR/$sums_name"
inventory_path="$BUILD_ROOT/.modeldial-unsigned-preview-${version}-${PREVIEW_LABEL}-inventory.json"

rm -f "$dmg_path" "$zip_path" "$sbom_path" "$sums_path" "$inventory_path"
if ! python3 build-support/generate-sbom.py \
  --bundle "$candidate_path" \
  --output "$sbom_path" \
  --release-label "v${version}-${PREVIEW_LABEL}" \
  --inventory-output "$inventory_path"; then
  fail "SBOM generation failed; no preview artifacts were packaged"
fi
if ! python3 build-support/verify-sbom.py \
  --bundle "$candidate_path" \
  --sbom "$sbom_path"; then
  fail "SBOM verification failed; no preview artifacts were packaged"
fi

ditto "$candidate_path" "$staging_dir/modeldial.app"
ln -s /Applications "$staging_dir/Applications"
cat > "$staging_dir/UNSIGNED_PREVIEW.txt" <<EOF
ModelDial ${version} ${PREVIEW_LABEL} unsigned preview

This app is ad-hoc signed only. It is not Developer ID signed and is not notarized.
Drag modeldial.app to Applications. If the first launch is blocked, use System Settings > Privacy & Security > Open Anyway.
Do not disable Gatekeeper and do not use xattr or spctl workarounds.

中文：这是仅 ad-hoc 签名、未公证的预览版。请将 modeldial.app 拖入 Applications。
首次打开被拦截时，到“系统设置 → 隐私与安全性 → 仍要打开”。
不要关闭 Gatekeeper，也不要使用 xattr 或 spctl 绕过安全检查。
EOF
rm -f "$dmg_path" "$zip_path" "$sums_path"
hdiutil create \
  -quiet \
  -volname "ModelDial Unsigned Preview $version" \
  -srcfolder "$staging_dir" \
  -ov \
  -format UDZO \
  "$dmg_path"
hdiutil verify -quiet "$dmg_path" \
  || fail "DMG container verification failed"
ditto -c -k --sequesterRsrc --keepParent \
  "$staging_dir/modeldial.app" \
  "$zip_path"
unzip -t "$zip_path" >/dev/null \
  || fail "ZIP container verification failed"
zip_verify_dir="$(mktemp -d "$BUILD_ROOT/.modeldial-unsigned-preview-zip-verify.XXXXXX")"
unzip -q "$zip_path" -d "$zip_verify_dir" \
  || fail "ZIP extraction verification failed"
zip_app="$zip_verify_dir/modeldial.app"
[[ -d "$zip_app" ]] || fail "ZIP does not contain modeldial.app"
zip_plist="$zip_app/Contents/Info.plist"
[[ -f "$zip_plist" ]] || fail "ZIP app is missing Contents/Info.plist"
zip_version="$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$zip_plist")"
zip_build_number="$(/usr/bin/plutil -extract CFBundleVersion raw -o - "$zip_plist")"
zip_executable_name="$(/usr/bin/plutil -extract CFBundleExecutable raw -o - "$zip_plist")"
zip_source_commit="$(/usr/bin/plutil -extract ModelDialSourceCommit raw -o - "$zip_plist")" \
  || fail "ZIP app is missing ModelDialSourceCommit"
[[ "$zip_source_commit" == "$source_commit" ]] \
  || fail "ZIP app source commit does not match the exact packaging commit"
zip_reference_snapshot_url="$(/usr/bin/plutil -extract ModelDialReferenceSnapshotURL raw -o - "$zip_plist")" \
  || fail "ZIP app is missing ModelDialReferenceSnapshotURL"
[[ "$zip_reference_snapshot_url" == "$REFERENCE_SNAPSHOT_URL" ]] \
  || fail "ZIP app reference snapshot URL does not match the official HTTPS feed"
zip_preview_feed_url="$(/usr/bin/plutil -extract SUFeedURL raw -o - "$zip_plist")" \
  || fail "ZIP app is missing SUFeedURL"
[[ "$zip_preview_feed_url" == "$UPDATE_FEED_URL" ]] \
  || fail "ZIP app update feed does not match the candidate"
zip_preview_public_key="$(/usr/bin/plutil -extract SUPublicEDKey raw -o - "$zip_plist")" \
  || fail "ZIP app is missing SUPublicEDKey"
[[ "$zip_preview_public_key" == "$UPDATE_PUBLIC_ED_KEY" ]] \
  || fail "ZIP app update public key does not match the candidate"
[[ "$zip_version" == "$version" ]] || fail "ZIP app version does not match candidate"
[[ "$zip_build_number" == "$build_number" ]] || fail "ZIP app build does not match candidate"
zip_main_executable="$zip_app/Contents/MacOS/$zip_executable_name"
[[ -x "$zip_main_executable" ]] || fail "ZIP app is missing its executable"
zip_architectures="$(lipo -archs "$zip_main_executable" 2>/dev/null)" \
  || fail "could not inspect ZIP app architecture"
[[ "$zip_architectures" == "arm64" ]] \
  || fail "ZIP app is not a thin arm64 build: $zip_architectures"
(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$dmg_name" "$zip_name" "$sbom_name" > "$sums_name"
)
packaging_succeeded=1

echo "Created unsigned preview artifacts in $OUTPUT_DIR"
echo "  $dmg_name"
echo "  $zip_name"
echo "  $sbom_name"
echo "  $sums_name"
echo "Source commit: $source_commit"
echo "Signing status: ad-hoc only; not Developer ID signed and not notarized."
