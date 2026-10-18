#!/usr/bin/env bash
# Full third-party license scan (docs/licenses.md).
#
#   scripts/licenses/regenerate.sh                 scan everything, rewrite THIRD_PARTY_NOTICES.md
#   scripts/licenses/regenerate.sh --check [--sbom-dir DIR]
#                                                  CI mode: fail on policy violations or a stale
#                                                  THIRD_PARTY_NOTICES.md, write per-app SBOMs
#
# Needs: Python 3.11+ with pip and PyYAML 6.0.3, cargo, flutter, and Gradle 8.10.2 on JDK 17
# (GRADLE overrides the gradle executable). Output goes to LICENSE_SCAN_DIR (default: a
# temporary directory). Policy: scripts/licenses/policy.toml; reviewed decisions:
# scripts/licenses/exceptions.toml.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${PYTHON:-python3}"
scan="$root/scripts/licenses/license_scan.py"
out="${LICENSE_SCAN_DIR:-$(mktemp -d)}"
mode=write
sbom_dir=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --sbom-dir) sbom_dir="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

"$python" -c 'import yaml' 2>/dev/null || {
  echo "PyYAML is missing: $python -m pip install 'PyYAML==6.0.3'" >&2
  exit 2
}
for tool in cargo flutter "${GRADLE:-gradle}"; do
  command -v "$tool" > /dev/null || { echo "$tool is not on PATH (see docs/licenses.md)" >&2; exit 2; }
done

mkdir -p "$out"
(cd "$root/apps/mobile" && flutter pub get > /dev/null)
for ecosystem in python npm cargo dart gradle; do
  "$python" "$scan" collect "$ecosystem" --gradle "${GRADLE:-gradle}" --out "$out/$ecosystem.json"
done

inputs=("$out/python.json" "$out/npm.json" "$out/cargo.json" "$out/dart.json" "$out/gradle.json")
args=(report --inputs "${inputs[@]}" --results "$out/results.json")
if [ -n "$sbom_dir" ]; then
  args+=(--sbom-dir "$sbom_dir")
fi
if [ "$mode" = check ]; then
  "$python" "$scan" "${args[@]}" --write-notices "$out/THIRD_PARTY_NOTICES.md" --check-notices
else
  set +e
  "$python" "$scan" "${args[@]}" --write-notices "$root/THIRD_PARTY_NOTICES.md"
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    echo "THIRD_PARTY_NOTICES.md was rewritten, but the policy check failed: see the table above." >&2
  fi
  exit "$status"
fi
