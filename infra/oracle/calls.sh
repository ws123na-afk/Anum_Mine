#!/usr/bin/env bash
# Optional TURN relay for ANUM's in-app calls (docs/in-app-calls.md, docs/deploy-oracle.md#in-app-calls).
# Calls between teammates are peer to peer (WebRTC); coturn relays the media when two
# networks cannot reach each other directly. Without it calls stay off.
#
#   bash infra/oracle/calls.sh enable [--external-ip 203.0.113.10]
#                                       set the relay's addresses and URLs in .env, open
#                                       TCP/UDP 3478 and the UDP relay range in the host
#                                       firewall, start coturn (profile `calls`) and
#                                       restart the API so it mints TURN credentials
#   bash infra/oracle/calls.sh status   profile, container health, firewall rules, URLs
#   bash infra/oracle/calls.sh disable  stop coturn, clear the URLs (calls off), close
#                                       the host firewall ports; the secret is kept
#
# Before `enable`, add the ingress rules this script prints to the VM's Oracle VCN
# security list (Networking > Virtual cloud networks > your VCN > Security Lists).
# Run it as the user who owns infra/oracle/.env (the install user). The firewall needs
# root: it uses sudo for iptables only. Never ufw, never `netfilter-persistent save`
# (it would store Docker's chains): rules go live and into /etc/iptables/rules.v4 before
# the REJECT rule of Oracle's Ubuntu images.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
CONFIGURE_ENV="$ORACLE_DIR/configure_env.py"
RULES_FILE=/etc/iptables/rules.v4

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; run infra/oracle/install.sh first"
command -v python3 >/dev/null || die "python3 is required"
ENV_OWNER="$(stat -c %U "$ENV_FILE")"
if [ "$(id -un)" != "$ENV_OWNER" ] && [ "$(id -u)" -ne 0 ]; then
  die "run this as $ENV_OWNER (the owner of $ENV_FILE)"
fi
as_env_owner() {
  if [ "$(id -un)" = "$ENV_OWNER" ]; then "$@"; else runuser -u "$ENV_OWNER" -- "$@"; fi
}

SUDO=()
if [ "$(id -u)" -ne 0 ]; then
  # Oracle's ubuntu user has NOPASSWD sudo but no password: `sudo -v` alone would ask.
  sudo -n true 2>/dev/null || sudo -v
  SUDO=(sudo)
fi
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=("${SUDO[@]}" docker)
fi
dc() { "${DOCKER[@]}" compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }

# .env access through configure_env.py (mode 600 kept, values never on a command line).
ENV_HELPER='
import importlib.util, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("configure_env", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
action, env_path, name = sys.argv[2], m.Path(sys.argv[3]), sys.argv[4]
if action == "get":
    print(m.read_entries(env_path)[1].get(name, ""))
elif action == "secret":
    print(m.new_secret(name))
elif action == "set":
    value = sys.stdin.read()
    value = value[:-1] if value.endswith("\n") else value
    lines, _ = m.read_entries(env_path)
    entry = f"{name}={m.format_value(value)}"
    output, done = [], False
    for line in lines:
        match = m.LINE.match(line)
        if match and match.group(1) == name:
            if not done:
                output.append(entry)
                done = True
            continue
        output.append(line)
    if not done:
        output.append(entry)
    m.write_private(env_path, "\n".join(output) + "\n")
'
env_helper() { as_env_owner python3 -c "$ENV_HELPER" "$CONFIGURE_ENV" "$@"; }
get_env() { env_helper get "$ENV_FILE" "$1"; }
set_env() { env_helper set "$ENV_FILE" "$1"; }
set_env_value() { printf '%s\n' "$2" | set_env "$1"; }

profile_has() { [[ ",$(get_env COMPOSE_PROFILES)," == *",$1,"* ]]; }
profile_add() {
  local current
  current="$(get_env COMPOSE_PROFILES)"
  profile_has "$1" && return 0
  set_env_value COMPOSE_PROFILES "${current:+$current,}$1"
}
profile_remove() {
  local current kept=() items=() item
  current="$(get_env COMPOSE_PROFILES)"
  IFS=',' read -r -a items <<<"$current"
  for item in "${items[@]}"; do
    [ -n "$item" ] && [ "$item" != "$1" ] && kept+=("$item")
  done
  set_env_value COMPOSE_PROFILES "$(IFS=','; echo "${kept[*]}")"
}

valid_ipv4() {
  python3 -c 'import ipaddress, sys; a = ipaddress.ip_address(sys.argv[1]); sys.exit(0 if a.version == 4 and a.is_global else 1)' "$1" 2>/dev/null
}

port_range() {
  local min max
  min="$(get_env ANUM_TURN_MIN_PORT)"; max="$(get_env ANUM_TURN_MAX_PORT)"
  min="${min:-49160}"; max="${max:-49200}"
  [[ "$min" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ ]] || die "ANUM_TURN_MIN_PORT and ANUM_TURN_MAX_PORT must be numbers"
  [ "$min" -ge 1024 ] && [ "$max" -le 65535 ] && [ "$min" -lt "$max" ] || die "the relay range must be inside 1024-65535 and min < max"
  printf '%s:%s' "$min" "$max"
}

# The three INPUT rules, one per line, without the leading -A INPUT.
firewall_rules() {
  local range
  range="$(port_range)"
  printf '%s\n' \
    "-p tcp -m state --state NEW -m tcp --dport 3478 -j ACCEPT" \
    "-p udp -m udp --dport 3478 -j ACCEPT" \
    "-p udp -m udp --dport $range -j ACCEPT"
}

open_firewall() {
  local rule position line
  while IFS= read -r rule; do
    read -r -a parts <<<"$rule"
    if ! "${SUDO[@]}" iptables -C INPUT "${parts[@]}" 2>/dev/null; then
      position="$("${SUDO[@]}" iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')"
      if [ -n "$position" ]; then
        "${SUDO[@]}" iptables -I INPUT "$position" "${parts[@]}"
      else
        "${SUDO[@]}" iptables -A INPUT "${parts[@]}"
      fi
    fi
    if "${SUDO[@]}" test -f "$RULES_FILE"; then
      line="-A INPUT $rule"
      if ! "${SUDO[@]}" grep -qxF -- "$line" "$RULES_FILE"; then
        if "${SUDO[@]}" grep -q '^-A INPUT -j REJECT' "$RULES_FILE"; then
          "${SUDO[@]}" sed -i "0,/^-A INPUT -j REJECT/s//${line//\//\\/}\n&/" "$RULES_FILE"
        else
          warn "$RULES_FILE has no INPUT REJECT rule; leaving it unchanged ($rule is open live)"
        fi
      fi
    else
      warn "$RULES_FILE not found (not an Oracle image?); $rule was opened for this boot only"
    fi
  done < <(firewall_rules)
}

close_firewall() {
  local rule line
  while IFS= read -r rule; do
    read -r -a parts <<<"$rule"
    while "${SUDO[@]}" iptables -C INPUT "${parts[@]}" 2>/dev/null; do
      "${SUDO[@]}" iptables -D INPUT "${parts[@]}"
    done
    if "${SUDO[@]}" test -f "$RULES_FILE"; then
      line="-A INPUT $rule"
      if "${SUDO[@]}" grep -qxF -- "$line" "$RULES_FILE"; then
        "${SUDO[@]}" sed -i "\\#^${line//\#/\\#}\$#d" "$RULES_FILE"
      fi
    fi
  done < <(firewall_rules)
}

print_security_list() {
  local range
  range="$(port_range)"
  cat <<EOF
Oracle VCN security list, ingress rules (source 0.0.0.0/0, stateful):
  TCP  destination port 3478
  UDP  destination port 3478
  UDP  destination port range ${range/:/-}
EOF
}

wait_healthy() {
  local container status waited=0
  while [ "$waited" -lt 60 ]; do
    container="$(dc ps -q coturn 2>/dev/null || true)"
    status="$("${DOCKER[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      echo "coturn is healthy"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  warn "coturn is not healthy after 60 seconds ($status); see: docker compose logs --tail 100 coturn"
  return 1
}

cmd_enable() {
  local external="" host suggested private secret
  while [ $# -gt 0 ]; do
    case "$1" in
      --external-ip) external="${2:-}"; shift 2 ;;
      *) usage ;;
    esac
  done
  host="$(get_env ANUM_API_HOST)"
  [ -n "$host" ] && [[ "$host" != *replace-me* ]] || die "ANUM_API_HOST is not set in $ENV_FILE"
  log "Ports to open in the Oracle security list first"
  print_security_list
  if [ -z "$external" ]; then
    suggested="$(getent ahostsv4 "$host" 2>/dev/null | awk 'NR == 1 { print $1 }')"
    read -r -p "The VM's public IPv4 address [${suggested:-none}]: " external
    external="${external:-$suggested}"
  fi
  valid_ipv4 "$external" || die "'$external' is not a public IPv4 address"
  private="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }')"
  [ -n "$private" ] || warn "could not find the VM's private address; coturn maps the public one only"
  secret="$(get_env ANUM_TURN_SECRET)"
  if [ "${#secret}" -lt 32 ] || [ "$secret" = "generate-on-server" ]; then
    env_helper secret "$ENV_FILE" ANUM_TURN_SECRET | set_env ANUM_TURN_SECRET
  fi
  set_env_value ANUM_TURN_EXTERNAL_IP "$external"
  set_env_value ANUM_TURN_PRIVATE_IP "$private"
  set_env_value ANUM_CALLS_TURN_URLS "turn:$host:3478?transport=udp,turn:$host:3478?transport=tcp"
  log "Opening TCP/UDP 3478 and UDP $(port_range) in the host firewall"
  open_firewall
  profile_add calls
  log "Starting coturn and restarting the API"
  dc up -d coturn
  dc up -d --no-deps api
  wait_healthy || true
  log "Calls use the relay at $host (TURN credentials expire after ANUM_CALLS_TURN_TTL_SECONDS, default 2 hours)"
}

cmd_disable() {
  log "Stopping coturn and turning calls off"
  profile_remove calls
  set_env_value ANUM_CALLS_TURN_URLS ""
  dc stop coturn >/dev/null 2>&1 || true
  dc rm -f coturn >/dev/null 2>&1 || true
  dc up -d --no-deps api
  log "Closing the relay ports in the host firewall"
  close_firewall
  echo "Remove the three ingress rules from the Oracle security list too:"
  print_security_list
}

cmd_status() {
  local urls
  urls="$(get_env ANUM_CALLS_TURN_URLS)"
  echo "COMPOSE_PROFILES=$(get_env COMPOSE_PROFILES)"
  echo "ANUM_CALLS_TURN_URLS=${urls:-<empty: calls are off>}"
  echo "ANUM_TURN_EXTERNAL_IP=$(get_env ANUM_TURN_EXTERNAL_IP)"
  if profile_has calls; then dc ps coturn; fi
  echo "Host firewall:"
  while IFS= read -r rule; do
    read -r -a parts <<<"$rule"
    if "${SUDO[@]}" iptables -C INPUT "${parts[@]}" 2>/dev/null; then echo "  open    $rule"; else echo "  closed  $rule"; fi
  done < <(firewall_rules)
}

case "${1:-}" in
  enable) shift; cmd_enable "$@" ;;
  disable) cmd_disable ;;
  status) cmd_status ;;
  *) usage ;;
esac
