#!/usr/bin/env bash
# Writes the owner's Firebase app config into a mobile release build (docs/mobile.md, "Owner
# steps: Firebase project"), so phone push works in CI builds. Usage:
#   mobile-firebase-config.sh android|ios
#
# android: ANUM_ANDROID_GOOGLE_SERVICES_JSON (the whole google-services.json) is written to
#          apps/mobile/android/app/google-services.json after checking that it is JSON and that
#          one of its clients is the package com.anum.app.
# ios:     ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST (the whole GoogleService-Info.plist) is written to
#          apps/mobile/ios/Runner/GoogleService-Info.plist.
#
# Both secrets are optional: when empty the build has no phone push and the run says so with a
# notice. The value is never printed; a value that is not a usable config fails the job. Adds a
# line to $GITHUB_STEP_SUMMARY either way. MOBILE_DIR (default apps/mobile) is for the tests.
set -euo pipefail

platform="${1:-}"
mobile="${MOBILE_DIR:-apps/mobile}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
package="com.anum.app"
doc="docs/mobile.md, Owner steps: Firebase project"

case "$platform" in
  android)
    label="Android"
    secret="ANUM_ANDROID_GOOGLE_SERVICES_JSON"
    value="${ANUM_ANDROID_GOOGLE_SERVICES_JSON:-}"
    target="$mobile/android/app/google-services.json"
    ;;
  ios)
    label="iOS"
    secret="ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST"
    value="${ANUM_IOS_GOOGLE_SERVICE_INFO_PLIST:-}"
    target="$mobile/ios/Runner/GoogleService-Info.plist"
    ;;
  *)
    echo "::error::Usage: mobile-firebase-config.sh android|ios"
    exit 2
    ;;
esac

if [ -z "${value//[[:space:]]/}" ]; then
  echo "::notice title=$label build without phone push::The $secret secret is not set, so this build has no Firebase config and push stays off ($doc)."
  echo "- $label push config: not included (no \`$secret\` secret), phone push is off in this build" >> "$summary"
  exit 0
fi

if [ "$platform" = "android" ]; then
  # The value reaches Python through the environment, never the command line or the log.
  if ! problem="$(FIREBASE_CONFIG="$value" EXPECTED_PACKAGE="$package" python3 -I -c '
import json, os, sys
try:
    data = json.loads(os.environ["FIREBASE_CONFIG"])
except ValueError:
    print("is not valid JSON")
    sys.exit(1)
clients = data.get("client") if isinstance(data, dict) else None
names = set()
for client in clients if isinstance(clients, list) else []:
    try:
        names.add(client["client_info"]["android_client_info"]["package_name"])
    except (KeyError, TypeError):
        pass
if os.environ["EXPECTED_PACKAGE"] not in names:
    print("has no client with package_name " + os.environ["EXPECTED_PACKAGE"])
    sys.exit(1)
')"; then
    echo "::error title=Android push config invalid::The $secret secret $problem. Paste the whole google-services.json of the Firebase Android app $package ($doc)."
    exit 1
  fi
fi

umask 077
mkdir -p "$(dirname "$target")"
printf '%s\n' "$value" > "$target"
echo "- $label push config: included (from the \`$secret\` secret)" >> "$summary"
