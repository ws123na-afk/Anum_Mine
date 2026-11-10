#!/usr/bin/env bash
# Restart ANUM containers that Docker reports as unhealthy
# (docs/deploy-oracle.md#monitoring-basics). `restart: unless-stopped` only brings back a
# container whose process exited; an API that still runs but no longer answers its health
# check would stay that way until someone noticed.
#
#   heal.sh            what anum-heal.timer runs every 2 minutes: restart every container
#                      of the compose project `anum` whose health status is `unhealthy`
#   heal.sh install    write and enable anum-heal.service and anum-heal.timer (root or
#                      sudo; safe to repeat). install.sh and update.sh call it.
#
# clamd is left alone: loading its signatures can take longer than a health check allows
# (start period 600 s), and restarting it only starts the load again; uploads wait for
# it either way. One-shot jobs have no health check. A container that is `starting`
# (just created by update.sh) is not touched. Each restart is one line in the journal:
# journalctl -u anum-heal --since today
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT=anum
SKIP_SERVICES=" clamd "

case "${1:-run}" in
  run)
    # "<container id> <compose service>" per unhealthy container of this project only:
    # other stacks on the VM (other sites, mail) are never touched.
    docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter health=unhealthy \
      --format '{{.ID}} {{.Label "com.docker.compose.service"}}' | while read -r container service; do
      [ -n "$container" ] || continue
      case "$SKIP_SERVICES" in *" $service "*) continue ;; esac
      echo "restarting unhealthy $service ($container)"
      docker restart "$container" >/dev/null || echo "WARNING: could not restart $service" >&2
    done
    ;;
  install)
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
    write_unit() { printf '%s\n' "$2" | $SUDO tee "/etc/systemd/system/$1" >/dev/null; }
    write_unit anum-heal.service "[Unit]
Description=Restart unhealthy ANUM containers
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/bin/bash $ORACLE_DIR/heal.sh run"
    write_unit anum-heal.timer "[Unit]
Description=Check ANUM container health every 2 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target"
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now anum-heal.timer >/dev/null
    echo "anum-heal.timer is enabled"
    ;;
  *)
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
    ;;
esac
