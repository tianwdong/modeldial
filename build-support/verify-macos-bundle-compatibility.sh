#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 APP_BUNDLE LOCKED_DEPLOYMENT_TARGET" >&2
  exit 2
fi

app_dir="$1"
locked_deployment_target="$2"
info_plist="$app_dir/Contents/Info.plist"
if [[ ! -d "$app_dir" || ! -f "$info_plist" ]]; then
  echo "Invalid macOS app bundle: $app_dir" >&2
  exit 1
fi

deployment_target="$(plutil -extract LSMinimumSystemVersion raw "$info_plist")"
if [[ ! "$deployment_target" =~ ^[0-9]+([.][0-9]+)*$ \
  || ! "$locked_deployment_target" =~ ^[0-9]+([.][0-9]+)*$ ]]; then
  echo "Invalid LSMinimumSystemVersion in $info_plist" >&2
  exit 1
fi
if [[ "$deployment_target" != "$locked_deployment_target" ]]; then
  echo "LSMinimumSystemVersion $deployment_target does not match the locked deployment target $locked_deployment_target" >&2
  exit 1
fi

version_exceeds() {
  awk -v actual="$1" -v maximum="$2" '
    BEGIN {
      actual_count = split(actual, actual_parts, ".")
      maximum_count = split(maximum, maximum_parts, ".")
      count = actual_count > maximum_count ? actual_count : maximum_count
      for (part_index = 1; part_index <= count; part_index++) {
        actual_part = part_index <= actual_count ? actual_parts[part_index] + 0 : 0
        maximum_part = part_index <= maximum_count ? maximum_parts[part_index] + 0 : 0
        if (actual_part > maximum_part) exit 0
        if (actual_part < maximum_part) exit 1
      }
      exit 1
    }
  '
}

macho_count=0
architecture_count=0
while IFS= read -r -d '' file_path; do
  if ! file "$file_path" | grep -q 'Mach-O'; then
    continue
  fi
  macho_count=$((macho_count + 1))

  build_versions="$(
    xcrun vtool -show-build "$file_path" | awk '
      /LC_VERSION_MIN_MACOSX/ {
        legacy = 1
        modern = 0
        next
      }
      legacy && /^[[:space:]]*version / {
        print $2
        legacy = 0
      }
      /platform MACOS/ {
        modern = 1
        legacy = 0
        next
      }
      modern && /^[[:space:]]*minos / {
        print $2
        modern = 0
      }
    '
  )"
  if [[ -z "$build_versions" ]]; then
    echo "Mach-O file has no macOS deployment version: $file_path" >&2
    exit 1
  fi

  while IFS= read -r build_version; do
    architecture_count=$((architecture_count + 1))
    if version_exceeds "$build_version" "$deployment_target"; then
      echo "Mach-O deployment target $build_version exceeds $deployment_target: $file_path" >&2
      exit 1
    else
      comparison_status=$?
      if [[ "$comparison_status" -ne 1 ]]; then
        echo "Unable to compare Mach-O deployment targets: $build_version and $deployment_target" >&2
        exit 1
      fi
    fi
  done <<< "$build_versions"

  while IFS= read -r dependency; do
    case "$dependency" in
      /System/Library/*|/usr/lib/*)
        ;;
      /*)
        echo "Mach-O file has a non-system absolute dependency $dependency: $file_path" >&2
        exit 1
        ;;
    esac
  done < <(
    xcrun otool -L "$file_path" | awk '/^[[:space:]]+\// { print $1 }'
  )
done < <(find "$app_dir" -type f -print0)

if [[ "$macho_count" -eq 0 ]]; then
  echo "No Mach-O files found in $app_dir" >&2
  exit 1
fi

echo "verified $macho_count Mach-O files and $architecture_count architecture records at macOS $deployment_target or earlier"
