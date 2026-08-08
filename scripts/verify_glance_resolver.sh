#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${TMPDIR:-/tmp}/modeldial-glance-resolver-tests"
MODULE_CACHE="${TMPDIR:-/tmp}/modeldial-swift-module-cache"
mkdir -p "$MODULE_CACHE"

swiftc \
  -module-cache-path "$MODULE_CACHE" \
  "$ROOT_DIR/Sources/Localization/L10n.swift" \
  "$ROOT_DIR/Sources/Model/GlanceState.swift" \
  "$ROOT_DIR/tests/swift/GlanceStateResolverTests.swift" \
  -o "$BIN"
"$BIN"
