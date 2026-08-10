#!/bin/bash
set -euo pipefail

app_dir="${1:-}"
if [[ -z "$app_dir" || ! -d "$app_dir" ]]; then
  echo "ad-hoc signing policy verification requires an app bundle" >&2
  exit 1
fi

macho_count=0
while IFS= read -r -d '' file_path; do
  if ! file "$file_path" | grep -q 'Mach-O'; then
    continue
  fi
  macho_count=$((macho_count + 1))

  signature_details="$(codesign -dv --verbose=4 "$file_path" 2>&1)" || {
    echo "Mach-O file has no readable code signature: $file_path" >&2
    exit 1
  }
  grep -Fq 'Signature=adhoc' <<<"$signature_details" || {
    echo "Mach-O file is not ad-hoc signed: $file_path" >&2
    exit 1
  }
  if grep -Eq '^CodeDirectory .*flags=.*runtime' <<<"$signature_details"; then
    echo "Mach-O file enables hardened runtime in an ad-hoc preview: $file_path" >&2
    exit 1
  fi
  if grep -Eiq 'Authority=|Developer ID|Apple (Development|Distribution)|3rd Party Mac Developer|Mac Developer' <<<"$signature_details"; then
    echo "Mach-O file carries a certificate identity: $file_path" >&2
    exit 1
  fi
  grep -Fq 'TeamIdentifier=not set' <<<"$signature_details" || {
    echo "Mach-O file unexpectedly carries a Team ID: $file_path" >&2
    exit 1
  }
  codesign --verify --strict --verbose=2 "$file_path" >/dev/null 2>&1 || {
    echo "Mach-O file failed strict code-signature verification: $file_path" >&2
    exit 1
  }
done < <(find "$app_dir" -type f -print0)

if [[ "$macho_count" -eq 0 ]]; then
  echo "No Mach-O files found in $app_dir" >&2
  exit 1
fi

codesign --verify --deep --strict --verbose=2 "$app_dir" >/dev/null 2>&1 || {
  echo "App bundle failed deep code-signature verification: $app_dir" >&2
  exit 1
}

echo "verified $macho_count ad-hoc Mach-O files without hardened runtime"
