#!/bin/bash

sign_app_bundle() {
  local app_dir="$1"
  local identity="$2"
  local bundle_id="$3"
  local sparkle_framework="$app_dir/Contents/Frameworks/Sparkle.framework"
  local sparkle_version="$sparkle_framework/Versions/Current"
  local timestamp_option="--timestamp"

  if [[ "$identity" == "-" ]]; then
    timestamp_option="--timestamp=none"
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
          --options runtime \
          "$timestamp_option" \
          --preserve-metadata=identifier,entitlements,flags,runtime \
          "$nested_code"
      fi
    done

    codesign --force --sign "$identity" \
      --options runtime \
      "$timestamp_option" \
      --preserve-metadata=identifier,entitlements,flags,runtime \
      "$sparkle_framework"
  fi

  codesign --force --sign "$identity" \
    --identifier "$bundle_id" \
    --options runtime \
    "$timestamp_option" \
    "$app_dir"
  codesign --verify --deep --strict --verbose=2 "$app_dir"
}
