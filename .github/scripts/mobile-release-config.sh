#!/usr/bin/env bash
# Decides what a mobile job of release-clients.yml can do with the configured secrets and
# variables (docs/mobile.md, "Release pipeline"). Usage: mobile-release-config.sh android|ios
#
# Reads the values from the environment, only ever tests whether they are empty (never prints
# one), and writes the decision to $GITHUB_OUTPUT:
#   android: build=true|false, play=true|false
#   ios:     signed=true|false, upload=true|false
#
# - A production URL variable that is set but not https:// fails: a mistake, not a missing value.
# - Part of a signing set (some of the four Android or the four Apple secrets) fails: a typo or a
#   forgotten secret would otherwise silently produce no signed build.
# - A run that must upload (a release tag, or the "upload" input) fails when anything it needs is
#   missing, naming every missing secret and variable.
# - A build-only manual run without the credentials passes with a notice (Android skips, iOS runs
#   an unsigned compile check), so the workflow can be tried before the owner has set them.
set -euo pipefail

platform="${1:-}"
upload="${UPLOAD:-false}"
output="${GITHUB_OUTPUT:-/dev/null}"
doc="docs/mobile.md, Release pipeline (repository secrets and the owner checklist)"

case "$platform" in
  android)
    label="Android"
    signing=(ANUM_ANDROID_KEYSTORE_BASE64 ANUM_ANDROID_KEYSTORE_PASSWORD ANUM_ANDROID_KEY_ALIAS ANUM_ANDROID_KEY_PASSWORD)
    ;;
  ios)
    label="iOS"
    signing=(APPLE_TEAM_ID APP_STORE_CONNECT_API_KEY_ID APP_STORE_CONNECT_API_ISSUER_ID APP_STORE_CONNECT_API_KEY_P8)
    ;;
  *)
    echo "::error::Usage: mobile-release-config.sh android|ios"
    exit 2
    ;;
esac

for name in ANUM_PRODUCTION_API_URL ANUM_PRODUCTION_OIDC_ISSUER ANUM_PRODUCTION_WEB_APP_URL; do
  value="${!name:-}"
  if [ -n "$value" ] && [[ "$value" != https://* ]]; then
    echo "::error title=${label} release misconfigured::The ${name} variable must start with https:// (got ${value})."
    exit 1
  fi
done

missing_signing=()
for name in "${signing[@]}"; do
  [ -n "${!name:-}" ] || missing_signing+=("$name")
done
if [ "${#missing_signing[@]}" -gt 0 ] && [ "${#missing_signing[@]}" -lt "${#signing[@]}" ]; then
  echo "::error title=${label} signing incomplete::Some ${label} signing secrets are set but these are missing or empty: ${missing_signing[*]}. Set all of them or none. See ${doc}."
  exit 1
fi

missing=()
for name in "${missing_signing[@]}"; do
  missing+=("${name} (secret)")
done
[ -n "${ANUM_PRODUCTION_API_URL:-}" ] || missing+=("ANUM_PRODUCTION_API_URL (variable, https://)")
[ -n "${ANUM_PRODUCTION_OIDC_ISSUER:-}" ] || missing+=("ANUM_PRODUCTION_OIDC_ISSUER (variable, https://)")
if [ "$platform" = "android" ] && [ "$upload" = "true" ] && [ -z "${PLAY_SERVICE_ACCOUNT_JSON:-}" ]; then
  missing+=("PLAY_SERVICE_ACCOUNT_JSON (secret)")
fi

if [ "${#missing[@]}" -gt 0 ] && [ "$upload" = "true" ]; then
  echo "::error title=${label} release not configured::This run uploads to the stores (a release tag or the upload input), but these are missing or empty: ${missing[*]}. Set them as described in ${doc}, or run the workflow by hand without upload for a build-only check."
  exit 1
fi

if [ "$platform" = "android" ]; then
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "::notice title=Android release skipped::Not configured: ${missing[*]}. See ${doc}."
    echo "build=false" >> "$output"
  else
    echo "build=true" >> "$output"
  fi
  echo "play=${upload}" >> "$output"
else
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "::notice title=iOS signing skipped::Building without code signing as a compile check. Not configured: ${missing[*]}. See ${doc}."
    echo "signed=false" >> "$output"
  else
    echo "signed=true" >> "$output"
  fi
  echo "upload=${upload}" >> "$output"
fi
