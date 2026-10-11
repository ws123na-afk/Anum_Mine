#!/usr/bin/env bash
# One-time (and safely repeatable) setup of ANUM on a fresh Ubuntu 24.04 VM, built for
# Oracle Cloud's Always Free Ampere A1 (arm64). Step-by-step guide: docs/deploy-oracle.md.
#
# Run as the VM's sudo user (ubuntu on Oracle images), from a clone of the repository:
#   git clone <repository URL> ~/anum && bash ~/anum/infra/oracle/install.sh
# Or copy only this script to the VM and let it clone:
#   ANUM_REPO_URL=<repository URL> ANUM_DIR=/opt/anum bash install.sh
#
# It installs Docker Engine and the compose plugin from Docker's apt repository, turns
# on unattended security upgrades, opens TCP 80/443 in the host firewall the way Oracle's
# images expect, adds swap, generates the secrets into infra/oracle/.env (mode 600; the
# values are never printed), asks for the few values only the owner has, builds and
# refuses duplicate or invalid host names and a world-open Keycloak admin allow-list,
# builds and starts the stack (update.sh: migrations before the API is replaced),
# installs the systemd timers (retention, backup, optional DuckDNS and auto-update) and
# ends with infra/oracle/smoke.sh, printing its summary (non-zero exit when a check
# fails). Every step checks first, so a second run changes nothing already in place.
set -euo pipefail

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  OWNER="${SUDO_USER:-root}"
else
  command -v sudo >/dev/null || die "run as a user with sudo rights"
  SUDO="sudo"
  OWNER="$(id -un)"
  # Oracle's image grants ubuntu NOPASSWD sudo, but `sudo -v` still asks for a password
  # (sudoers' default verifypw=all also sees the %sudo rule), and ubuntu has none.
  sudo -n true 2>/dev/null || sudo -v
fi
run_as_owner() {
  # Under `sudo bash install.sh` SUDO is empty: switch to the owner with runuser.
  if [ "$(id -un)" = "$OWNER" ]; then "$@"
  elif [ -z "$SUDO" ]; then runuser -u "$OWNER" -- "$@"
  else $SUDO -u "$OWNER" -- "$@"; fi
}

# --- Checks -------------------------------------------------------------------------
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "this script supports Ubuntu (24.04); found ${PRETTY_NAME:-unknown}"
[ "${VERSION_ID:-}" = "24.04" ] || warn "tested on Ubuntu 24.04; this is ${VERSION_ID:-unknown}"
case "$(uname -m)" in
  aarch64 | arm64) ;;
  x86_64) warn "x86_64 host: fine, but the guide assumes the Ampere A1 (arm64) shape" ;;
  *) die "unsupported architecture $(uname -m)" ;;
esac

# --- Repository ---------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if REPO_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  log "Using the repository at $REPO_DIR (update it later with infra/oracle/update.sh)"
else
  : "${ANUM_REPO_URL:?not inside a clone: set ANUM_REPO_URL (and optionally ANUM_DIR)}"
  REPO_DIR="${ANUM_DIR:-/opt/anum}"
fi

# --- Packages -------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
log "Installing base packages"
# iptables-persistent must not ask whether to save the current rules (they may include
# Docker's chains); the rules file is edited below instead.
echo "iptables-persistent iptables-persistent/autosave_v4 boolean false" | $SUDO debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | $SUDO debconf-set-selections
$SUDO apt-get update -q
$SUDO apt-get install -y -q ca-certificates curl git gnupg python3 openssl \
  unattended-upgrades apt-listchanges iptables iptables-persistent netfilter-persistent

if [ ! -d "$REPO_DIR/.git" ]; then
  log "Cloning $ANUM_REPO_URL into $REPO_DIR"
  $SUDO install -d -o "$OWNER" -g "$(id -gn "$OWNER")" "$REPO_DIR"
  run_as_owner git clone "$ANUM_REPO_URL" "$REPO_DIR"
fi
ORACLE_DIR="$REPO_DIR/infra/oracle"
ENV_FILE="$ORACLE_DIR/.env"
[ -f "$ORACLE_DIR/compose.yaml" ] || die "$ORACLE_DIR/compose.yaml not found"

# --- Docker Engine from Docker's apt repository ---------------------------------------
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  log "Installing Docker Engine and the compose plugin"
  for package in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
    if dpkg -s "$package" >/dev/null 2>&1; then $SUDO apt-get remove -y -q "$package"; fi
  done
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -q
  $SUDO apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
if [ ! -f /etc/docker/daemon.json ]; then
  log "Configuring Docker: log rotation, live restore"
  # live-restore keeps containers running while unattended-upgrades restarts dockerd.
  $SUDO install -d -m 0755 /etc/docker
  printf '%s\n' '{' \
    '  "log-driver": "json-file",' \
    '  "log-opts": { "max-size": "10m", "max-file": "5" },' \
    '  "live-restore": true' \
    '}' | $SUDO tee /etc/docker/daemon.json >/dev/null
  $SUDO systemctl restart docker
fi
$SUDO systemctl enable --now docker >/dev/null
if [ "$OWNER" != "root" ] && ! id -nG "$OWNER" | tr ' ' '\n' | grep -qx docker; then
  $SUDO usermod -aG docker "$OWNER"
  log "Added $OWNER to the docker group (takes effect at the next SSH login)"
fi

# --- Unattended upgrades ----------------------------------------------------------------
log "Enabling unattended upgrades (security updates daily, reboot at 04:30 UTC when needed)"
printf '%s\n' 'APT::Periodic::Update-Package-Lists "1";' 'APT::Periodic::Unattended-Upgrade "1";' \
  'APT::Periodic::AutocleanInterval "7";' | $SUDO tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
printf '%s\n' \
  '// ANUM (infra/oracle/install.sh): also update Docker Engine, and reboot when a' \
  '// kernel or libc update needs it. Containers come back by themselves (restart: unless-stopped).' \
  'Unattended-Upgrade::Origins-Pattern { "origin=Docker"; };' \
  'Unattended-Upgrade::Automatic-Reboot "true";' \
  'Unattended-Upgrade::Automatic-Reboot-Time "04:30";' \
  'Unattended-Upgrade::Remove-Unused-Dependencies "true";' \
  | $SUDO tee /etc/apt/apt.conf.d/52anum-unattended-upgrades >/dev/null
$SUDO systemctl enable --now unattended-upgrades >/dev/null

# --- Host firewall ----------------------------------------------------------------------
# Oracle's Ubuntu images ship /etc/iptables/rules.v4: INPUT allows SSH and ends with
# `-j REJECT --reject-with icmp-host-prohibited`. Do not use ufw on these images, and
# never run `netfilter-persistent save` while Docker runs (it would store Docker's own
# chains). Docker publishes Caddy's ports through its own DNAT/FORWARD rules; the INPUT
# rules below also cover traffic that reaches docker-proxy on the host. The matching
# Oracle VCN security-list ingress rules are in docs/deploy-oracle.md.
log "Opening TCP 80 and 443 in the host firewall"
RULES_FILE=/etc/iptables/rules.v4
for port in 80 443; do
  rule=(-p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT)
  if ! $SUDO iptables -C INPUT "${rule[@]}" 2>/dev/null; then
    position="$($SUDO iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')"
    if [ -n "$position" ]; then
      $SUDO iptables -I INPUT "$position" "${rule[@]}"
    else
      $SUDO iptables -A INPUT "${rule[@]}"
    fi
  fi
  if $SUDO test -f "$RULES_FILE"; then
    line="-A INPUT -p tcp -m state --state NEW -m tcp --dport $port -j ACCEPT"
    if ! $SUDO grep -qxF -- "$line" "$RULES_FILE"; then
      if $SUDO grep -q '^-A INPUT -j REJECT' "$RULES_FILE"; then
        $SUDO sed -i "0,/^-A INPUT -j REJECT/s//$line\n&/" "$RULES_FILE"
      else
        warn "$RULES_FILE has no INPUT REJECT rule; leaving it unchanged (port $port is open live)"
      fi
    fi
  else
    warn "$RULES_FILE not found (not an Oracle image?); port $port was opened for this boot only"
  fi
done

# --- Swap ---------------------------------------------------------------------------
if [ -z "$(swapon --show --noheadings)" ]; then
  log "Adding a 4 GB swap file (headroom for image builds)"
  $SUDO fallocate -l 4G /swapfile
  $SUDO chmod 600 /swapfile
  $SUDO mkswap /swapfile >/dev/null
  $SUDO swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness=10' | $SUDO tee /etc/sysctl.d/90-anum-swap.conf >/dev/null
  $SUDO sysctl -q -p /etc/sysctl.d/90-anum-swap.conf
fi

# --- Secrets and owner values ---------------------------------------------------------
log "Preparing $ENV_FILE"
set +e
run_as_owner python3 "$ORACLE_DIR/configure_env.py" "$ENV_FILE" --example "$ORACLE_DIR/.env.example"
status=$?
set -e
if [ "$status" -eq 3 ]; then
  die "fill in the values listed above in $ENV_FILE, then run this script again"
elif [ "$status" -ne 0 ]; then
  die "configure_env.py failed ($status)"
fi
# Refuse before building anything: three valid, different host names (Caddy serves one
# site per name; the web bundle and Keycloak's issuer are built from them) and an admin
# allow-list of addresses or CIDR ranges that does not open the console to everyone.
log "Checking the host names and the Keycloak admin allow-list"
set +e
run_as_owner python3 "$ORACLE_DIR/configure_env.py" "$ENV_FILE" --check --strict
status=$?
set -e
if [ "$status" -ne 0 ]; then
  die "fix ANUM_WEB_HOST, ANUM_API_HOST, ANUM_AUTH_HOST or ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS in $ENV_FILE as described above (nano $ENV_FILE), then run this script again"
fi
env_value() {
  # One value from .env without sourcing it (values are never echoed by this script).
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"
}

BACKUP_DIR="$(env_value ANUM_BACKUP_DIR)"
BACKUP_DIR="${BACKUP_DIR:-/var/lib/anum/backups}"
$SUDO install -d -m 0700 -o 10001 -g 10001 "$BACKUP_DIR"

# --- Build and start --------------------------------------------------------------------
if [ -n "$(env_value DUCKDNS_TOKEN)" ]; then
  log "Pointing the DuckDNS names at this VM"
  $SUDO bash "$ORACLE_DIR/duckdns.sh" || warn "DuckDNS update failed; check DUCKDNS_TOKEN and DUCKDNS_DOMAINS"
fi
log "Building the images on this VM and starting the stack (the first build takes a while)"
$SUDO bash "$ORACLE_DIR/update.sh" --no-pull

# --- systemd timers ---------------------------------------------------------------------
log "Installing systemd timers"
DOCKER_COMPOSE="/usr/bin/docker compose --project-directory $ORACLE_DIR -f $ORACLE_DIR/compose.yaml"
write_unit() { printf '%s\n' "$2" | $SUDO tee "/etc/systemd/system/$1" >/dev/null; }

write_unit anum-retention.service "[Unit]
Description=ANUM voice transcript and retrieval index retention
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=$DOCKER_COMPOSE run --rm retention"
write_unit anum-retention.timer "[Unit]
Description=Daily ANUM retention

[Timer]
OnCalendar=*-*-* 03:17:00 UTC
Persistent=true

[Install]
WantedBy=timers.target"

write_unit anum-backup.service "[Unit]
Description=ANUM nightly backup (PostgreSQL dumps, encrypted off-site copy)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/bin/bash $ORACLE_DIR/backup.sh backup"
write_unit anum-backup.timer "[Unit]
Description=Nightly ANUM backup

[Timer]
OnCalendar=*-*-* 02:41:00 UTC
Persistent=true

[Install]
WantedBy=timers.target"

write_unit anum-update.service "[Unit]
Description=ANUM update (git pull, build, migrate, restart)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $ORACLE_DIR/update.sh"
write_unit anum-update.timer "[Unit]
Description=Daily ANUM update

[Timer]
OnCalendar=*-*-* 04:05:00 UTC
Persistent=true

[Install]
WantedBy=timers.target"

write_unit anum-duckdns.service "[Unit]
Description=Keep the DuckDNS names on this VM's public IP
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $ORACLE_DIR/duckdns.sh"
write_unit anum-duckdns.timer "[Unit]
Description=DuckDNS update every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target"

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now anum-retention.timer anum-backup.timer >/dev/null
if [ "$(env_value ANUM_AUTO_UPDATE)" = "true" ]; then
  $SUDO systemctl enable --now anum-update.timer >/dev/null
else
  $SUDO systemctl disable --now anum-update.timer >/dev/null 2>&1 || true
fi
if [ -n "$(env_value DUCKDNS_TOKEN)" ]; then
  $SUDO systemctl enable --now anum-duckdns.timer >/dev/null
else
  $SUDO systemctl disable --now anum-duckdns.timer >/dev/null 2>&1 || true
fi

WEB_HOST="$(env_value ANUM_WEB_HOST)"
API_HOST="$(env_value ANUM_API_HOST)"
AUTH_HOST="$(env_value ANUM_AUTH_HOST)"
if [ -n "$(env_value ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS | tr -d ' ,')" ]; then
  ADMIN_HOW="https://$AUTH_HOST/admin/ (from ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS only)"
else
  ADMIN_HOW="through an SSH tunnel only: ssh -L 8080:172.30.10.20:8080 $OWNER@<VM_IP>, then http://localhost:8080/admin/"
fi

# --- Smoke test -------------------------------------------------------------------------
# As the owner, without sudo, against Caddy on this VM (127.0.0.1 with the real names, so
# it does not depend on Oracle routing the VM's own public IP back to it). A fresh
# install waits up to 5 minutes for the Let's Encrypt certificates.
log "Smoke test (infra/oracle/smoke.sh)"
set +e
run_as_owner bash "$ORACLE_DIR/smoke.sh" --wait 300
smoke_status=$?
set -e

cat <<EOF

ANUM is running.
  Web:      https://$WEB_HOST
  API:      https://$API_HOST/health
  Keycloak: $ADMIN_HOW

Secrets are in $ENV_FILE (mode 600, never committed). Copy them into your password
manager now; read them with:  sudo cat $ENV_FILE
Keycloak's one-time admin is KC_BOOTSTRAP_ADMIN_USERNAME / KC_BOOTSTRAP_ADMIN_PASSWORD in
that file: replace it on day one (docs/deploy-oracle.md#keycloak).

Status:   cd $ORACLE_DIR && docker compose ps
Timers:   systemctl list-timers 'anum-*'
Smoke:    bash $ORACLE_DIR/smoke.sh   (from your PC: bash smoke.sh $WEB_HOST $API_HOST $AUTH_HOST)
EOF
if [ "$smoke_status" -ne 0 ]; then
  warn "the smoke test failed (summary above); fix those items and run: bash $ORACLE_DIR/smoke.sh"
  exit 1
fi
