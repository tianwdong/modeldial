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
MODELDIAL_DISABLE_UPDATES="${MODELDIAL_DISABLE_UPDATES:-0}"
if [[ "$MODELDIAL_DISABLE_UPDATES" != "0" && "$MODELDIAL_DISABLE_UPDATES" != "1" ]]; then
  echo "MODELDIAL_DISABLE_UPDATES must be 0 or 1." >&2
  exit 1
fi
if [[ -n "$SOURCE_COMMIT" && ! "$SOURCE_COMMIT" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "MODELDIAL_SOURCE_COMMIT must be a 40- or 64-character lowercase Git commit." >&2
  exit 1
fi
if [[ "$MODELDIAL_DISABLE_UPDATES" == "1" ]]; then
  UPDATE_FEED_URL=""
  UPDATE_PUBLIC_ED_KEY=""
fi
if [[ -n "$UPDATE_FEED_URL" && -z "$UPDATE_PUBLIC_ED_KEY" ]] \
  || [[ -z "$UPDATE_FEED_URL" && -n "$UPDATE_PUBLIC_ED_KEY" ]]; then
  echo "MODELDIAL_UPDATE_FEED_URL and MODELDIAL_UPDATE_PUBLIC_ED_KEY must be provided together." >&2
  exit 1
fi
BUILD_DIR="./build"
PYTHON_RUNTIME_LOCK="build-support/python-runtime.lock.json"
PYINSTALLER_REQUIREMENTS="build-support/pyinstaller-requirements.txt"
XCODE_DERIVED_DIR="$BUILD_DIR/xcode-derived"
XCODE_PRODUCT_APP="$XCODE_DERIVED_DIR/Build/Products/Release/$APP_NAME.app"
LIVE_APP_DIR="$BUILD_DIR/$APP_NAME.app"
CANDIDATE_APP_DIR="$BUILD_DIR/$APP_NAME-candidate.app"
STAGING_DIR="$BUILD_DIR/.modeldial-build-$$"
APP_DIR="$STAGING_DIR/$APP_NAME.app"
RES_DIR="$APP_DIR/Contents/Resources"
BACKEND_DIR="$RES_DIR/Backend"
BACKEND_RUNTIME_DIR="$BACKEND_DIR/Runtime"
IFS=$'\t' read -r \
  PYTHON_RUNTIME_SCHEMA \
  PYTHON_VERSION \
  PYTHON_FRAMEWORK_VERSION \
  PYTHON_INSTALLER_FILENAME \
  PYTHON_INSTALLER_SIGNER \
  PYTHON_RELEASE_PAGE \
  PYTHON_INSTALLER_SHA256 \
  PYTHON_TARGET_ARCH \
  PYTHON_INSTALLER_URL \
  PYTHON_DEPLOYMENT_TARGET < <(
    /usr/bin/python3 - "$PYTHON_RUNTIME_LOCK" <<'PY'
import json
import sys


with open(sys.argv[1], encoding="utf-8") as handle:
    lock = json.load(handle)
keys = (
    "schema_version",
    "version",
    "framework_version",
    "installer_filename",
    "installer_signer",
    "release_page",
    "sha256",
    "target_arch",
    "url",
    "deployment_target",
)
print("\t".join(str(lock[key]) for key in keys))
PY
  )
if [[ "$PYTHON_RUNTIME_SCHEMA" != "1" \
  || ! "$PYTHON_INSTALLER_SHA256" =~ ^[0-9a-f]{64}$ \
  || "$PYTHON_TARGET_ARCH" != "arm64" \
  || ! "$PYTHON_DEPLOYMENT_TARGET" =~ ^[0-9]+([.][0-9]+)*$ \
  || "$PYTHON_INSTALLER_URL" != "https://www.python.org/ftp/python/$PYTHON_VERSION/$PYTHON_INSTALLER_FILENAME" \
  || "$PYTHON_RELEASE_PAGE" != "https://www.python.org/downloads/release/python-${PYTHON_VERSION//./}/" ]]; then
  echo "Invalid ModelDial Python runtime lock: $PYTHON_RUNTIME_LOCK" >&2
  exit 1
fi
PYTHON_RUNTIME_ROOT="$BUILD_DIR/python-runtime-$PYTHON_VERSION"
PYTHON_FRAMEWORK="$PYTHON_RUNTIME_ROOT/Python.framework"
PYTHON_FRAMEWORK_ROOT="$PYTHON_FRAMEWORK/Versions/$PYTHON_FRAMEWORK_VERSION"
BUILD_PYTHON="$PYTHON_FRAMEWORK_ROOT/bin/python$PYTHON_FRAMEWORK_VERSION"
PYTHON_RUNTIME_LIB_DIR="$PYTHON_FRAMEWORK_ROOT/lib"
PYTHON_RUNTIME_RECEIPT="$PYTHON_RUNTIME_ROOT/source.sha256"
PYTHON_INSTALLER_DIR="$BUILD_DIR/python-runtime-downloads"
PYTHON_INSTALLER_PATH="$PYTHON_INSTALLER_DIR/$PYTHON_INSTALLER_FILENAME"
PYTHON_INSTALLER_TEMP="$PYTHON_INSTALLER_PATH.partial.$$"
PYTHON_RUNTIME_EXTRACT_DIR="$BUILD_DIR/.python-runtime-extract-$$"
PYINSTALLER_ENV="$BUILD_DIR/pyinstaller-env"
PYINSTALLER_PYTHON="$PYINSTALLER_ENV/bin/python3"
PYINSTALLER_REQUIREMENTS_RECEIPT="$PYINSTALLER_ENV/.modeldial-requirements.sha256"
PYINSTALLER_DIST_DIR="$STAGING_DIR/pyinstaller-dist"
PYINSTALLER_WORK_DIR="$STAGING_DIR/pyinstaller-work"
PYINSTALLER_SPEC_DIR="$STAGING_DIR/pyinstaller-spec"
RUNTIME_SCRIPT_NAMES=(
  "native_bridge.py"
  "modeldial_session_hook.py"
  "install_session_observer.py"
)
PYTHON_RUNTIME_DYLIB_NAMES=(
  "libcrypto.3.dylib"
  "libssl.3.dylib"
  "libzstd.1.dylib"
)

python_runtime_env() {
  DYLD_FRAMEWORK_PATH="$(pwd)/${PYTHON_RUNTIME_ROOT#./}" \
  DYLD_LIBRARY_PATH="$(pwd)/${PYTHON_FRAMEWORK_ROOT#./}/lib" \
    "$@"
}

python_installer_matches_lock() {
  [[ -f "$1" ]] || return 1
  [[ "$(shasum -a 256 "$1" | awk '{print $1}')" == "$PYTHON_INSTALLER_SHA256" ]] || return 1
  local signature
  signature="$(pkgutil --check-signature "$1")" || return 1
  grep -Fq "$PYTHON_INSTALLER_SIGNER" <<< "$signature" \
    && grep -Fq "Notarization: trusted" <<< "$signature"
}

ensure_python_installer() {
  mkdir -p "$PYTHON_INSTALLER_DIR"
  if python_installer_matches_lock "$PYTHON_INSTALLER_PATH"; then
    return
  fi
  echo "Downloading the locked Python $PYTHON_VERSION runtime from python.org."
  curl \
    --fail \
    --location \
    --proto '=https' \
    --proto-redir '=https' \
    --retry 3 \
    --tlsv1.2 \
    --output "$PYTHON_INSTALLER_TEMP" \
    "$PYTHON_INSTALLER_URL"
  if ! python_installer_matches_lock "$PYTHON_INSTALLER_TEMP"; then
    echo "Downloaded Python runtime failed hash or installer signature validation." >&2
    exit 1
  fi
  mv "$PYTHON_INSTALLER_TEMP" "$PYTHON_INSTALLER_PATH"
}

python_runtime_matches_lock() {
  [[ -x "$BUILD_PYTHON" && -f "$PYTHON_RUNTIME_RECEIPT" ]] || return 1
  [[ "$(<"$PYTHON_RUNTIME_RECEIPT")" == "$PYTHON_INSTALLER_SHA256" ]] || return 1
  MODELDIAL_REQUIRED_PYTHON="$PYTHON_VERSION" \
  MODELDIAL_REQUIRED_BASE_PREFIX="$(pwd)/${PYTHON_FRAMEWORK_ROOT#./}" \
    python_runtime_env "$BUILD_PYTHON" - <<'PY' >/dev/null 2>&1
import os
import platform
import sys
from pathlib import Path


if platform.python_version() != os.environ["MODELDIAL_REQUIRED_PYTHON"]:
    raise SystemExit(1)
if Path(sys.prefix).resolve() != Path(os.environ["MODELDIAL_REQUIRED_BASE_PREFIX"]).resolve():
    raise SystemExit(1)
PY
}

prepare_python_runtime() {
  if python_runtime_matches_lock; then
    return
  fi
  echo "Preparing the locked project-local Python $PYTHON_VERSION runtime."
  rm -rf "$PYTHON_RUNTIME_ROOT" "$PYTHON_RUNTIME_EXTRACT_DIR"
  pkgutil --expand "$PYTHON_INSTALLER_PATH" "$PYTHON_RUNTIME_EXTRACT_DIR"
  local framework_payload="$PYTHON_RUNTIME_EXTRACT_DIR/Python_Framework.pkg/Payload"
  local relocated_runtime="$PYTHON_RUNTIME_EXTRACT_DIR/relocated"
  if [[ ! -f "$framework_payload" ]]; then
    echo "Python installer does not contain the expected framework payload." >&2
    exit 1
  fi
  mkdir -p "$relocated_runtime/Python.framework"
  ditto -x "$framework_payload" "$relocated_runtime/Python.framework"
  printf '%s\n' "$PYTHON_INSTALLER_SHA256" > "$relocated_runtime/source.sha256"
  mv "$relocated_runtime" "$PYTHON_RUNTIME_ROOT"
  if ! python_runtime_matches_lock; then
    echo "The project-local Python runtime failed relocation validation." >&2
    exit 1
  fi
}

pyinstaller_env_matches_lock() {
  MODELDIAL_REQUIRED_PYTHON="$PYTHON_VERSION" \
  MODELDIAL_REQUIRED_BASE_PREFIX="$(pwd)/${PYTHON_FRAMEWORK_ROOT#./}" \
  MODELDIAL_REQUIRED_BASE_EXECUTABLE="$(pwd)/${BUILD_PYTHON#./}" \
    python_runtime_env "$PYINSTALLER_PYTHON" - \
    "$PYINSTALLER_REQUIREMENTS" "$PYINSTALLER_REQUIREMENTS_RECEIPT" <<'PY'
import hashlib
import os
import platform
import re
import sys
from importlib.metadata import distributions
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


requirements_path = Path(sys.argv[1])
receipt_path = Path(sys.argv[2])
expected_receipt = hashlib.sha256(requirements_path.read_bytes()).hexdigest()
if not receipt_path.is_file() or receipt_path.read_text(encoding="utf-8").strip() != expected_receipt:
    raise SystemExit(1)
if platform.python_version() != os.environ["MODELDIAL_REQUIRED_PYTHON"]:
    raise SystemExit(1)
expected_base_prefix = Path(os.environ["MODELDIAL_REQUIRED_BASE_PREFIX"]).resolve()
expected_base_executable = Path(os.environ["MODELDIAL_REQUIRED_BASE_EXECUTABLE"]).resolve()
if Path(sys.base_prefix).resolve() != expected_base_prefix:
    raise SystemExit(1)
venv_config = {}
for raw_line in (Path(sys.prefix) / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
    key, separator, value = raw_line.partition("=")
    if separator:
        venv_config[key.strip()] = value.strip()
if Path(venv_config.get("home", "/")).resolve() != expected_base_executable.parent:
    raise SystemExit(1)
if Path(venv_config.get("executable", "/")).resolve() != expected_base_executable:
    raise SystemExit(1)
venv_python = Path(sys.prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"
if venv_python.resolve() != expected_base_executable:
    raise SystemExit(1)

expected = {}
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.split("#", 1)[0].strip()
    if not line:
        continue
    if line.startswith("--hash="):
        continue
    line = line.rstrip("\\").strip()
    name, separator, version = line.partition("==")
    if not separator or not name or not version:
        raise SystemExit(1)
    expected[normalize(name)] = version

actual = {}
for distribution in distributions():
    name = distribution.metadata.get("Name")
    if name:
        actual[normalize(name)] = distribution.version

if actual != expected:
    raise SystemExit(1)
PY
}

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

PYINSTALLER_CODESIGN_ARGS=()
if [[ "$RESOLVED_CODESIGN_IDENTITY" != "-" ]]; then
  PYINSTALLER_CODESIGN_ARGS=(--codesign-identity "$RESOLVED_CODESIGN_IDENTITY")
fi

mkdir -p "$BUILD_DIR"
rm -rf "$STAGING_DIR" "$PYTHON_RUNTIME_EXTRACT_DIR"
trap 'rm -rf "$STAGING_DIR" "$PYTHON_RUNTIME_EXTRACT_DIR"; rm -f "$PYTHON_INSTALLER_TEMP"' EXIT
ensure_python_installer
prepare_python_runtime
PYINSTALLER_RUNTIME_BINARY_ARGS=()
for runtime_library in "${PYTHON_RUNTIME_DYLIB_NAMES[@]}"; do
  runtime_library_path="$(pwd)/${PYTHON_RUNTIME_LIB_DIR#./}/$runtime_library"
  if [[ ! -f "$runtime_library_path" ]]; then
    echo "The locked Python runtime is missing $runtime_library_path." >&2
    exit 1
  fi
  PYINSTALLER_RUNTIME_BINARY_ARGS+=(--add-binary "$runtime_library_path:.")
done
if [[ ! -x "$PYINSTALLER_PYTHON" ]] || ! pyinstaller_env_matches_lock; then
  if [[ -e "$PYINSTALLER_ENV" ]]; then
    echo "Rebuilding $PYINSTALLER_ENV because it does not match the locked Python build dependencies."
    rm -rf "$PYINSTALLER_ENV"
  fi
  python_runtime_env "$BUILD_PYTHON" -m venv "$PYINSTALLER_ENV"
  python_runtime_env "$PYINSTALLER_PYTHON" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --require-hashes \
    --requirement "$PYINSTALLER_REQUIREMENTS"
  REQUIREMENTS_DIGEST="$(shasum -a 256 "$PYINSTALLER_REQUIREMENTS" | awk '{print $1}')"
  REQUIREMENTS_RECEIPT_TEMP="$PYINSTALLER_REQUIREMENTS_RECEIPT.partial.$$"
  printf '%s\n' "$REQUIREMENTS_DIGEST" > "$REQUIREMENTS_RECEIPT_TEMP"
  mv -f "$REQUIREMENTS_RECEIPT_TEMP" "$PYINSTALLER_REQUIREMENTS_RECEIPT"
  if ! pyinstaller_env_matches_lock; then
    echo "The rebuilt PyInstaller environment does not match $PYINSTALLER_REQUIREMENTS." >&2
    exit 1
  fi
fi
python_runtime_env "$PYINSTALLER_PYTHON" -m pip check
xcodebuild -quiet \
  -project "ModelDial.xcodeproj" \
  -scheme "ModelDial" \
  -configuration "Release" \
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
mkdir -p "$BACKEND_DIR" "$BACKEND_RUNTIME_DIR"

cp "Resources/AppIcon.icns" "$RES_DIR/AppIcon.icns"
cp "Resources/ModeldialShareMark.svg" "$RES_DIR/ModeldialShareMark.svg"
cp "Resources/ModeldialWordmark.svg" \
  "$RES_DIR/ModeldialWordmark.svg"
mkdir -p "$RES_DIR/ProviderLogos" "$RES_DIR/Legal"
cp Resources/ProviderLogos/*-lobe.svg "$RES_DIR/ProviderLogos/"
cp Resources/Legal/* "$RES_DIR/Legal/"
cp -R "scanner" "$BACKEND_DIR/scanner"
mkdir -p "$BACKEND_DIR/scripts"
for script_name in "${RUNTIME_SCRIPT_NAMES[@]}"; do
  cp "scripts/$script_name" "$BACKEND_DIR/scripts/$script_name"
done
cp -R "questions" "$BACKEND_DIR/questions"
mkdir -p "$BACKEND_DIR/devtools/pricing"
cp "devtools/__init__.py" "$BACKEND_DIR/devtools/__init__.py"
cp "devtools/pricing/__init__.py" "$BACKEND_DIR/devtools/pricing/__init__.py"
cp "devtools/pricing/updater.py" "$BACKEND_DIR/devtools/pricing/updater.py"
cp "devtools/pricing/policy.json" "$BACKEND_DIR/devtools/pricing/policy.json"
python_runtime_env "$PYINSTALLER_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --noupx \
  --name "modeldial-backend" \
  --paths "$(pwd)" \
  --hidden-import "unittest" \
  --hidden-import "devtools.pricing.updater" \
  --hidden-import "ssl" \
  --hidden-import "hashlib" \
  --hidden-import "compression.zstd" \
  --hidden-import "certifi" \
  "${PYINSTALLER_RUNTIME_BINARY_ARGS[@]}" \
  --target-arch "$PYTHON_TARGET_ARCH" \
  --distpath "$PYINSTALLER_DIST_DIR" \
  --workpath "$PYINSTALLER_WORK_DIR" \
  --specpath "$PYINSTALLER_SPEC_DIR" \
  ${PYINSTALLER_CODESIGN_ARGS[@]+"${PYINSTALLER_CODESIGN_ARGS[@]}"} \
  --osx-bundle-identifier "$BUNDLE_ID.backend" \
  "scripts/native_bridge.py"
cp -R "$PYINSTALLER_DIST_DIR/modeldial-backend/." "$BACKEND_RUNTIME_DIR/"
find "$BACKEND_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$BACKEND_DIR" -type f -name '*.pyc' -delete
find "$BACKEND_DIR" -type f -name '.DS_Store' -delete
MODELDIAL_BACKEND_ROOT="$BACKEND_DIR" \
  "$BACKEND_RUNTIME_DIR/modeldial-backend" \
  --modeldial-python-code \
  'import compression.zstd, hashlib, ssl; from pathlib import Path; payload = b"modeldial-runtime-smoke"; assert compression.zstd.decompress(compression.zstd.compress(payload)) == payload; assert len(hashlib.sha256(payload).digest()) == 32; assert ssl.OPENSSL_VERSION.startswith("OpenSSL "); paths = ssl.get_default_verify_paths(); assert paths.cafile and Path(paths.cafile).is_file(); assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0' \
  >/dev/null
MODELDIAL_BACKEND_ROOT="$BACKEND_DIR" \
  "$BACKEND_RUNTIME_DIR/modeldial-backend" \
  snapshot \
  --config-path "$STAGING_DIR/smoke-config.json" \
  --history-path "$STAGING_DIR/smoke-history.jsonl" \
  --active-run-path "$STAGING_DIR/smoke-active-run.json" \
  >/dev/null
xcrun xcstringstool compile \
  "Resources/Localizable.xcstrings" \
  --output-directory "$RES_DIR"

# Source assets and cached dependencies can carry host-specific extended
# attributes. They are not part of the release and must not be preserved in
# the signed bundle or its DMG.
/usr/bin/xattr -cr "$APP_DIR"

"./build-support/verify-macos-bundle-compatibility.sh" \
  "$APP_DIR" \
  "$PYTHON_DEPLOYMENT_TARGET"

sign_app_bundle "$APP_DIR" "$RESOLVED_CODESIGN_IDENTITY" "$BUNDLE_ID"
if [[ "$RESOLVED_CODESIGN_IDENTITY" == "-" ]]; then
  bash "./build-support/verify-adhoc-signing-policy.sh" "$APP_DIR"
fi

CANDIDATE_EXECUTABLE="$(pwd)/${CANDIDATE_APP_DIR#./}/Contents/MacOS/$APP_NAME"

is_app_running() {
  ps -Ao command= | grep -F -x "$1" >/dev/null
}

if is_app_running "$CANDIDATE_EXECUTABLE"; then
  SAFE_CANDIDATE_APP_DIR="$BUILD_DIR/$APP_NAME-candidate-$(date +%Y%m%d-%H%M%S)-$$.app"
  mv "$APP_DIR" "$SAFE_CANDIDATE_APP_DIR"
  echo "built $SAFE_CANDIDATE_APP_DIR; live $LIVE_APP_DIR and running candidate $CANDIDATE_APP_DIR were left untouched"
else
  rm -rf "$CANDIDATE_APP_DIR"
  mv "$APP_DIR" "$CANDIDATE_APP_DIR"
  echo "built $CANDIDATE_APP_DIR; live $LIVE_APP_DIR was left untouched"
fi
