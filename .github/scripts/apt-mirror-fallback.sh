#!/usr/bin/env bash
# Hosted runners point apt at azure.archive.ubuntu.com, through the mirror list in
# /etc/apt/apt-mirrors.txt (sources say mirror+file:...). On 11 November 2026 that name stopped
# resolving for hours and every job that installs system packages failed before any test ran.
# When the name does not resolve, list the main Ubuntu archive instead; apt also retries downloads.
#
# The runner image also adds third-party sources ANUM never installs from (packages.microsoft.com).
# On 27 November 2026 that repository answered 403, `apt-get update` failed and so did every
# `playwright install-deps`. When an update fails, those non-Ubuntu sources are set aside.
set -euo pipefail
echo 'Acquire::Retries "5";' | sudo tee /etc/apt/apt.conf.d/80-anum-retries >/dev/null
if ! getent hosts azure.archive.ubuntu.com >/dev/null; then
  echo "::warning::azure.archive.ubuntu.com does not resolve; using archive.ubuntu.com"
  for file in /etc/apt/apt-mirrors.txt /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    if [ -f "$file" ]; then
      sudo sed -i 's#azure\.archive\.ubuntu\.com#archive.ubuntu.com#g' "$file"
    fi
  done
fi
if sudo apt-get update -qq >/dev/null 2>&1; then
  exit 0
fi
for file in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
  if [ -f "$file" ] && ! grep -Eq 'ubuntu\.com|apt-mirrors' "$file"; then
    echo "::warning::apt update failed; setting aside the third-party source $file"
    sudo mv "$file" "$file.anum-disabled"
  fi
done
exit 0
