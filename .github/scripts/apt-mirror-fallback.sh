#!/usr/bin/env bash
# Hosted runners point apt at azure.archive.ubuntu.com. When that name does not resolve (it did not
# for over an hour on 11 November 2026, and every job that installs system packages failed before
# any test ran), switch to the main Ubuntu archive. apt also retries each download a few times.
set -euo pipefail
echo 'Acquire::Retries "5";' | sudo tee /etc/apt/apt.conf.d/80-anum-retries >/dev/null
if getent hosts azure.archive.ubuntu.com >/dev/null; then
  exit 0
fi
echo "::warning::azure.archive.ubuntu.com does not resolve; using archive.ubuntu.com"
for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
  [ -f "$file" ] && sudo sed -i 's#azure\.archive\.ubuntu\.com#archive.ubuntu.com#g' "$file"
done
exit 0
