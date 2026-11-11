#!/usr/bin/env bash
# Hosted runners point apt at azure.archive.ubuntu.com, through the mirror list in
# /etc/apt/apt-mirrors.txt (sources say mirror+file:...). On 11 November 2026 that name stopped
# resolving for hours and every job that installs system packages failed before any test ran.
# When the name does not resolve, list the main Ubuntu archive instead; apt also retries downloads.
set -euo pipefail
echo 'Acquire::Retries "5";' | sudo tee /etc/apt/apt.conf.d/80-anum-retries >/dev/null
if getent hosts azure.archive.ubuntu.com >/dev/null; then
  exit 0
fi
echo "::warning::azure.archive.ubuntu.com does not resolve; using archive.ubuntu.com"
for file in /etc/apt/apt-mirrors.txt /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
  if [ -f "$file" ]; then
    sudo sed -i 's#azure\.archive\.ubuntu\.com#archive.ubuntu.com#g' "$file"
  fi
done
exit 0
