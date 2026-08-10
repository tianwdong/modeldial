#!/bin/bash

sign_app_bundle() {
  local app_dir="$1"
  local identity="$2"
  local bundle_id="$3"
  local sparkle_framework="$app_dir/Contents/Frameworks/Sparkle.framework"
  local sparkle_version="$sparkle_framework/Versions/Current"
  local signing_policy=(--timestamp)
  local preserved_metadata="identifier,entitlements"

  if [[ "$identity" == "-" ]]; then
    signing_policy=(--timestamp=none)
  else
    signing_policy=(--options runtime --timestamp)
    preserved_metadata="identifier,entitlements,flags,runtime"
  fi

  if [[ -d "$sparkle_framework" ]]; then
    local nested_code
    for nested_code in \
      "$sparkle_version/XPCServices/Downloader.xpc" \
      "$sparkle_version/XPCServices/Installer.xpc" \
      "$sparkle_version/Updater.app" \
      "$sparkle_version/Autoupdate"; do
      if [[ -e "$nested_code" ]]; then
        codesign --force --sign "$identity" \
          "${signing_policy[@]}" \
          --preserve-metadata="$preserved_metadata" \
          "$nested_code"
      fi
    done

    codesign --force --sign "$identity" \
      "${signing_policy[@]}" \
      --preserve-metadata="$preserved_metadata" \
      "$sparkle_framework"
  fi

  codesign --force --sign "$identity" \
    --identifier "$bundle_id" \
    "${signing_policy[@]}" \
    "$app_dir"
  codesign --verify --deep --strict --verbose=2 "$app_dir"
}
