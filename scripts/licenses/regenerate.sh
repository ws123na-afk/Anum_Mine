#!/usr/bin/env bash
# Full third-party license scan (docs/licenses.md).
#
#   scripts/licenses/regenerate.sh                 scan everything, rewrite THIRD_PARTY_NOTICES.md
#   scripts/licenses/regenerate.sh --check [--sbom-dir DIR]
#                                                  CI mode: fail on policy violations or a stale
#                                                  THIRD_PARTY_NOTICES.md, write per-app SBOMs
#   scripts/licenses/regenerate.sh --available [--check] [--skip ECOSYSTEM]...
#                                                  local mode (`pnpm licenses:check`): scan only the
#                                                  ecosystems whose tools are installed (and not
#                                                  skipped); keep the committed notices sections
#                                                  of the others and say which were not scanned
#
# Needs: Python 3.11+ with pip and PyYAML 6.0.x, cargo, flutter, and Gradle 8.10.2 on JDK 17
# (GRADLE overrides the gradle executable). Output goes to LICENSE_SCAN_DIR (default: a
# temporary directory). Policy: scripts/licenses/policy.toml; reviewed decisions:
# scripts/licenses/exceptions.toml. Ecosystems: python npm cargo dart gradle.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${PYTHON:-python3}"
scan="$root/scripts/licenses/license_scan.py"
out="${LICENSE_SCAN_DIR:-$(mktemp -d)}"
gradle="${GRADLE:-gradle}"
mode=write
available=false
sbom_dir=""
skip=" "

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --available) available=true ;;
    --skip)
      case "${2:-}" in
        python|npm|cargo|dart|gradle) skip="$skip$2 " ;;
        *) echo "--skip needs one of: python npm cargo dart gradle" >&2; exit 2 ;;
      esac
      shift ;;
    --sbom-dir) sbom_dir="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

"$python" -c 'import yaml' 2>/dev/null || {
  echo "PyYAML is missing: $python -m pip install 'PyYAML==6.0.3'" >&2
  exit 2
}

tool_for() {
  case "$1" in
    python|npm) echo "$python" ;;
    cargo) echo cargo ;;
    dart) echo flutter ;;
    gradle) echo "$gradle" ;;
  esac
}

ecosystems=()
not_scanned=()
for ecosystem in python npm cargo dart gradle; do
  tool="$(tool_for "$ecosystem")"
  if [[ "$skip" == *" $ecosystem "* ]]; then
    if [ "$available" != true ]; then
      echo "--skip needs --available (CI scans every ecosystem)" >&2
      exit 2
    fi
    not_scanned+=("$ecosystem (skipped)")
  elif command -v "$tool" > /dev/null; then
    ecosystems+=("$ecosystem")
  elif [ "$available" = true ]; then
    not_scanned+=("$ecosystem ($tool not installed)")
  else
    echo "$tool is not on PATH (see docs/licenses.md, or use --available)" >&2
    exit 2
  fi
done
if [ "${#ecosystems[@]}" -eq 0 ]; then
  echo "nothing to scan" >&2
  exit 2
fi

mkdir -p "$out"
inputs=()
for ecosystem in "${ecosystems[@]}"; do
  if [ "$ecosystem" = dart ]; then
    (cd "$root/apps/mobile" && flutter pub get > /dev/null)
  fi
  "$python" "$scan" collect "$ecosystem" --gradle "$gradle" --out "$out/$ecosystem.json"
  inputs+=("$out/$ecosystem.json")
done

args=(report --inputs "${inputs[@]}" --results "$out/results.json")
if [ -n "$sbom_dir" ]; then
  args+=(--sbom-dir "$sbom_dir")
fi
if [ "$available" = true ]; then
  args+=(--keep-unscanned)
fi

report_status=0
if [ "$mode" = check ]; then
  "$python" "$scan" "${args[@]}" --write-notices "$out/THIRD_PARTY_NOTICES.md" --check-notices || report_status=$?
else
  "$python" "$scan" "${args[@]}" --write-notices "$root/THIRD_PARTY_NOTICES.md" || report_status=$?
  if [ "$report_status" -ne 0 ]; then
    echo "THIRD_PARTY_NOTICES.md was rewritten, but the policy check failed: see the table above." >&2
  fi
fi

echo "Scanned: ${ecosystems[*]}. Results: $out/results.json"
if [ "${#not_scanned[@]}" -gt 0 ]; then
  echo "NOT scanned here: ${not_scanned[*]}. Their THIRD_PARTY_NOTICES.md sections are kept as committed; CI's full scan checks them." >&2
fi
exit "$report_status"
