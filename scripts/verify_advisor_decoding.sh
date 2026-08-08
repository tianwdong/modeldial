#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${TMPDIR:-/tmp}/modeldial-advisor-decoding-tests"
MODULE_CACHE="${TMPDIR:-/tmp}/modeldial-swift-module-cache"
mkdir -p "$MODULE_CACHE"

swiftc \
  -module-cache-path "$MODULE_CACHE" \
  "$ROOT_DIR/Sources/Model/LocalEncryptedSecretStore.swift" \
  "$ROOT_DIR/Sources/Model/SelectionModels.swift" \
  "$ROOT_DIR/tests/swift/AdvisorDecodingTests.swift" \
  -o "$BIN"
"$BIN"
