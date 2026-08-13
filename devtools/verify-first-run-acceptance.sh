#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
app_path="${MODELDIAL_ACCEPTANCE_APP:-$repo_root/build/modeldial-candidate.app}"
output_dir="${MODELDIAL_ACCEPTANCE_OUTPUT_DIR:-$repo_root/artifacts/first-run-acceptance}"
skip_build=0

if [[ "${1:-}" == "--skip-build" ]]; then
  skip_build=1
  shift
fi

if [[ "$skip_build" -eq 0 ]]; then
  export MODELDIAL_REFERENCE_SNAPSHOT_URL="${MODELDIAL_REFERENCE_SNAPSHOT_URL:-https://reference.modeldial.com/reference-snapshots}"
  export MODELDIAL_UPDATE_FEED_URL="${MODELDIAL_UPDATE_FEED_URL:-https://updates.modeldial.com/macos/preview/appcast.xml}"
  export MODELDIAL_UPDATE_PUBLIC_ED_KEY="${MODELDIAL_UPDATE_PUBLIC_ED_KEY:-maaLn09C7fDPrHIh3Hxr6NYjGrj1CNQPzKUp7DEKID0=}"
  "$repo_root/build.sh"
fi

exec python3 "$repo_root/devtools/first_run_acceptance.py" \
  --app "$app_path" \
  --output-dir "$output_dir" \
  "$@"
