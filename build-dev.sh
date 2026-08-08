#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source "./build-support/sign-app-bundle.sh"

APP_NAME="modeldial"
BUNDLE_ID="com.modeldial.app"
CODESIGN_IDENTITY="${MODELDIAL_CODESIGN_IDENTITY:--}"
REFERENCE_SNAPSHOT_URL="${MODELDIAL_REFERENCE_SNAPSHOT_URL:-}"
SOURCE_COMMIT="${MODELDIAL_SOURCE_COMMIT:-}"
UPDATE_FEED_URL="${MODELDIAL_UPDATE_FEED_URL:-}"
UPDATE_PUBLIC_ED_KEY="${MODELDIAL_UPDATE_PUBLIC_ED_KEY:-}"
if [[ -n "$SOURCE_COMMIT" && ! "$SOURCE_COMMIT" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "MODELDIAL_SOURCE_COMMIT must be a 40- or 64-character lowercase Git commit." >&2
  exit 1
fi
if [[ -n "$UPDATE_FEED_URL" && -z "$UPDATE_PUBLIC_ED_KEY" ]] \
  || [[ -z "$UPDATE_FEED_URL" && -n "$UPDATE_PUBLIC_ED_KEY" ]]; then
  echo "MODELDIAL_UPDATE_FEED_URL and MODELDIAL_UPDATE_PUBLIC_ED_KEY must be provided together." >&2
  exit 1
fi
BUILD_DIR="./build"
XCODE_DERIVED_DIR="$BUILD_DIR/xcode-dev-derived"
XCODE_PRODUCT_APP="$XCODE_DERIVED_DIR/Build/Products/Debug/$APP_NAME.app"
CANDIDATE_APP_DIR="$BUILD_DIR/$APP_NAME-candidate.app"
LIVE_APP_DIR="$BUILD_DIR/$APP_NAME.app"
BASE_APP_DIR="$CANDIDATE_APP_DIR"
DEV_APP_DIR="$BUILD_DIR/$APP_NAME-dev.app"
STAGING_DIR="$BUILD_DIR/.modeldial-dev-build-$$"
APP_DIR="$STAGING_DIR/$APP_NAME.app"
RES_DIR="$APP_DIR/Contents/Resources"

if [[ ! -x "$BASE_APP_DIR/Contents/Resources/Backend/Runtime/modeldial-backend" ]]; then
  BASE_APP_DIR="$LIVE_APP_DIR"
fi
if [[ ! -x "$BASE_APP_DIR/Contents/Resources/Backend/Runtime/modeldial-backend" ]]; then
  echo "No complete candidate or live base app found; run ./build.sh once first." >&2
  exit 1
fi

if [[ "$CODESIGN_IDENTITY" == "-" ]]; then
  RESOLVED_CODESIGN_IDENTITY="-"
else
  IDENTITY_LIST="$(security find-identity -v -p codesigning)"
  RESOLVED_CODESIGN_IDENTITY="$(
    printf '%s\n' "$IDENTITY_LIST" | awk -v requested="$CODESIGN_IDENTITY" '
      !resolved && ($2 == requested || index($0, "\"" requested "\"") > 0) {
        resolved = $2
      }
      END {
        if (resolved) print resolved
      }
    '
  )"
  if [[ -z "$RESOLVED_CODESIGN_IDENTITY" ]]; then
    echo "No matching modeldial code-signing identity found: $CODESIGN_IDENTITY" >&2
    exit 1
  fi
fi

mkdir -p "$BUILD_DIR"
rm -rf "$STAGING_DIR"
trap 'rm -rf "$STAGING_DIR"' EXIT
mkdir -p "$STAGING_DIR"
xcodebuild -quiet \
  -project "ModelDial.xcodeproj" \
  -scheme "ModelDial" \
  -configuration "Debug" \
  -destination "generic/platform=macOS" \
  -derivedDataPath "$XCODE_DERIVED_DIR" \
  CODE_SIGNING_ALLOWED=NO \
  MODELDIAL_REFERENCE_SNAPSHOT_URL="$REFERENCE_SNAPSHOT_URL" \
  MODELDIAL_SOURCE_COMMIT="$SOURCE_COMMIT" \
  MODELDIAL_SU_FEED_URL="$UPDATE_FEED_URL" \
  MODELDIAL_SU_PUBLIC_ED_KEY="$UPDATE_PUBLIC_ED_KEY" \
  build
if [[ ! -x "$XCODE_PRODUCT_APP/Contents/MacOS/$APP_NAME" ]]; then
  echo "Xcode did not produce a complete app at $XCODE_PRODUCT_APP" >&2
  exit 1
fi
ditto "$XCODE_PRODUCT_APP" "$APP_DIR"
ditto \
  "$BASE_APP_DIR/Contents/Resources/Backend" \
  "$RES_DIR/Backend"

xcrun xcstringstool compile \
  "Resources/Localizable.xcstrings" \
  --output-directory "$RES_DIR"

mkdir -p "$RES_DIR/ProviderLogos" "$RES_DIR/Legal"
cp Resources/ProviderLogos/*-lobe.svg "$RES_DIR/ProviderLogos/"
cp Resources/Legal/* "$RES_DIR/Legal/"

sign_app_bundle "$APP_DIR" "$RESOLVED_CODESIGN_IDENTITY" "$BUNDLE_ID"

DEV_EXECUTABLE="$(pwd)/${DEV_APP_DIR#./}/Contents/MacOS/$APP_NAME"
if ps -Ao command= | grep -F -x "$DEV_EXECUTABLE" >/dev/null; then
  SAFE_DEV_APP_DIR="$BUILD_DIR/$APP_NAME-dev-$(date +%Y%m%d-%H%M%S)-$$.app"
  mv "$APP_DIR" "$SAFE_DEV_APP_DIR"
  echo "built $SAFE_DEV_APP_DIR; running $DEV_APP_DIR was left untouched"
else
  rm -rf "$DEV_APP_DIR"
  mv "$APP_DIR" "$DEV_APP_DIR"
  echo "built $DEV_APP_DIR"
fi
echo "This development build reuses the frozen backend from $BASE_APP_DIR."
