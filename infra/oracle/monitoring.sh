#!/usr/bin/env bash
# Optional monitoring for ANUM on one VM (docs/monitoring-oracle.md): Beszel for host
# metrics and GlitchTip for the API's errors, each on its own hostname behind Caddy
# (basic auth in front of each tool's own login). Nothing publishes a port.
#
#   bash infra/oracle/monitoring.sh enable    hostnames, basic-auth password, secrets,
#                                            start Beszel's hub and GlitchTip, create the
#                                            GlitchTip admin, then `agent` and `dsn`
#   bash infra/oracle/monitoring.sh agent     connect the Beszel agent (key and token
#                                            from the hub's "Add system" dialog)
#   bash infra/oracle/monitoring.sh dsn       set the API's GlitchTip DSN (or --clear)
#   bash infra/oracle/monitoring.sh status    profiles, containers, memory, endpoints
#   bash infra/oracle/monitoring.sh disable   stop and remove the containers and the
#                                            Caddy sites; data and secrets are kept
#   bash infra/oracle/monitoring.sh purge     disable, then delete the data volumes,
#                                            GlitchTip's database and the .env values
#
# Run it as the user who owns infra/oracle/.env (the install user, in the docker group):
# no sudo is needed. Only when that user cannot reach the Docker daemon does it use
# sudo for docker. Secrets are read with `read -s`, never passed on a command line and
# never printed; .env stays mode 600 (configure_env.py writes it).
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
CONFIGURE_ENV="$ORACLE_DIR/configure_env.py"
SITE_LINK="$ORACLE_DIR/sites-enabled/monitoring.caddy"
HUB_SERVICES=(beszel glitchtip)
ALL_SERVICES=(beszel beszel-agent glitchtip)
VOLUMES=(beszel-data beszel-agent-data glitchtip-uploads)

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

# --------------------------------------------------------------------------- access

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; run infra/oracle/install.sh first"
command -v python3 >/dev/null || die "python3 is required"
ENV_OWNER="$(stat -c %U "$ENV_FILE")"
if [ "$(id -un)" != "$ENV_OWNER" ] && [ "$(id -u)" -ne 0 ]; then
  die "run this as $ENV_OWNER (the owner of $ENV_FILE)"
fi

# .env is always written as its owner, so a run with sudo never leaves it owned by root.
as_env_owner() {
  if [ "$(id -un)" = "$ENV_OWNER" ]; then "$@"; else runuser -u "$ENV_OWNER" -- "$@"; fi
}

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  [ "$(id -u)" -ne 0 ] || die "cannot reach the Docker daemon (is docker running?)"
  echo "$(id -un) cannot reach the Docker daemon (not in the docker group yet?); using sudo for docker."
  # Oracle's ubuntu user has NOPASSWD sudo but no password: `sudo -v` alone would ask.
  sudo -n true 2>/dev/null || sudo -v
  DOCKER=(sudo docker)
fi
dc() { "${DOCKER[@]}" compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }

# --------------------------------------------------------------------------- .env

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
elif action == "valid":
    problem = m.validate(name, sys.stdin.read().strip())
    if problem:
        print(f"{name} must be {problem}", file=sys.stderr)
        sys.exit(1)
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
# get_env NAME: the value, for this script only (never printed for secrets).
get_env() { env_helper get "$ENV_FILE" "$1"; }
# set_env NAME: the value comes on stdin, never on a command line.
set_env() { env_helper set "$ENV_FILE" "$1"; }
set_env_value() { printf '%s\n' "$2" | set_env "$1"; }
valid_value() { printf '%s\n' "$2" | env_helper valid "$ENV_FILE" "$1"; }

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

# --------------------------------------------------------------------------- prompts

ask_host() {
  local name="$1" label="$2" example="$3" current answer
  current="$(get_env "$name")"
  while true; do
    if [ -n "$current" ]; then
      read -r -p "$label [$current]: " answer
      answer="${answer:-$current}"
    else
      read -r -p "$label (for example $example): " answer
    fi
    answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
    if valid_value "$name" "$answer"; then
      set_env_value "$name" "$answer"
      return 0
    fi
  done
}

# ask_secret LABEL VAR: hidden input, asked twice; the value lands in VAR.
ask_secret() {
  local label="$1" first second
  while true; do
    read -r -s -p "$label: " first; echo
    read -r -s -p "$label (again): " second; echo
    if [ "$first" != "$second" ]; then
      echo "The two entries differ; try again."
    elif [ "${#first}" -lt 12 ]; then
      echo "Use at least 12 characters (a password manager can generate one)."
    else
      printf -v "$2" '%s' "$first"
      return 0
    fi
  done
}

wait_healthy() {
  local service="$1" seconds="$2" container status waited=0
  while [ "$waited" -lt "$seconds" ]; do
    container="$(dc ps -q "$service" 2>/dev/null || true)"
    status="$("${DOCKER[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo missing)"
    if [ "$status" = "healthy" ]; then
      echo "$service is healthy"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  warn "$service is not healthy after $seconds seconds ($status); see: docker compose logs --tail 100 $service"
  return 1
}

# DuckDNS names in either monitoring host are added to DUCKDNS_DOMAINS, so the existing
# anum-duckdns.timer keeps them on the VM's address too.
add_duckdns_names() {
  local token domains host name changed=false
  token="$(get_env DUCKDNS_TOKEN)"
  [ -n "$token" ] || return 0
  domains="$(get_env DUCKDNS_DOMAINS)"
  for host in "$(get_env ANUM_MONITOR_HOST)" "$(get_env ANUM_ERRORS_HOST)"; do
    case "$host" in
      *.duckdns.org) name="${host%.duckdns.org}" ;;
      *) continue ;;
    esac
    [[ "$name" =~ ^[a-z0-9-]+$ ]] || continue
    if [[ ",$domains," != *",$name,"* ]]; then
      domains="${domains:+$domains,}$name"
      changed=true
    fi
  done
  if [ "$changed" = true ]; then
    set_env_value DUCKDNS_DOMAINS "$domains"
    echo "Added the monitoring names to DUCKDNS_DOMAINS; updating DuckDNS now."
    as_env_owner bash "$ORACLE_DIR/duckdns.sh" || warn "DuckDNS update failed; the timer retries every 5 minutes"
  fi
}

check_dns() {
  local host address
  for host in "$(get_env ANUM_MONITOR_HOST)" "$(get_env ANUM_ERRORS_HOST)"; do
    address="$(getent ahostsv4 "$host" 2>/dev/null | awk 'NR==1 {print $1}' || true)"
    if [ -z "$address" ]; then
      warn "$host does not resolve yet. Create its DNS A record (docs/deploy-oracle.md#dns); Caddy keeps retrying the certificate."
    else
      echo "$host -> $address"
    fi
  done
}

# --------------------------------------------------------------------------- caddy

# `caddy validate` and `caddy reload` adapt the Caddyfile in the exec'd process, so they
# need the admin allow-list rewritten exactly as compose.yaml's Caddy entrypoint does
# (commas to spaces; empty becomes 0.0.0.0/32 ::/128). Extra `-e NAME=value` options
# for `docker compose exec` go in CADDY_EXEC_ENV.
CADDY_EXEC_ENV=()
# shellcheck disable=SC2016 # expanded by sh inside the Caddy container
CADDY_WRAP='ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS="$(printf "%s" "${ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS:-}" | tr "," " " | xargs)"
export ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS="${ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS:-0.0.0.0/32 ::/128}"
exec caddy "$@" --config /etc/caddy/Caddyfile --adapter caddyfile'
caddy_config_cmd() { dc exec -T "${CADDY_EXEC_ENV[@]}" caddy sh -c "$CADDY_WRAP" caddy-cli "$@"; }

# Validates the monitoring sites inside the running Caddy with the new values, then
# recreates Caddy with them (its environment changed) and reloads. A failed check
# removes the site link again, so a mistake never takes the ANUM sites down.
enable_caddy_sites() {
  log "Adding the monitoring sites to Caddy"
  mkdir -p "$(dirname "$SITE_LINK")"
  printf '%s\n' "# Written by monitoring.sh enable; removed by disable." \
    "import /etc/caddy/sites-available/monitoring.caddy" >"$SITE_LINK"
  CADDY_EXEC_ENV=(
    -e "ANUM_MONITOR_HOST=$(get_env ANUM_MONITOR_HOST)"
    -e "ANUM_ERRORS_HOST=$(get_env ANUM_ERRORS_HOST)"
    -e "ANUM_MONITORING_BASIC_AUTH_USER=$(get_env ANUM_MONITORING_BASIC_AUTH_USER)"
    -e "ANUM_MONITORING_BASIC_AUTH_HASH=$(get_env ANUM_MONITORING_BASIC_AUTH_HASH)"
  )
  if ! caddy_config_cmd validate >/dev/null; then
    CADDY_EXEC_ENV=()
    rm -f "$SITE_LINK"
    die "Caddy refused the monitoring sites (see above); nothing was changed"
  fi
  CADDY_EXEC_ENV=()
  dc up -d --no-deps caddy
  caddy_config_cmd reload
}

disable_caddy_sites() {
  [ -f "$SITE_LINK" ] || return 0
  log "Removing the monitoring sites from Caddy"
  rm -f "$SITE_LINK"
  caddy_config_cmd reload || warn "Caddy reload failed; run: docker compose restart caddy"
}

# --------------------------------------------------------------------------- commands

configure_basic_auth() {
  local user current_hash password hash answer
  user="$(get_env ANUM_MONITORING_BASIC_AUTH_USER)"
  current_hash="$(get_env ANUM_MONITORING_BASIC_AUTH_HASH)"
  if [ -n "$user" ] && [ -n "$current_hash" ]; then
    read -r -p "Keep the existing basic-auth login '$user'? [Y/n] " answer
    case "${answer:-y}" in [Yy]*) return 0 ;; esac
  fi
  read -r -p "Basic-auth user name for the monitoring sites [${user:-monitor}]: " answer
  user="${answer:-${user:-monitor}}"
  [[ "$user" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "user name: letters, digits, '.', '_' and '-' only"
  ask_secret "Basic-auth password (store it in your password manager)" password
  # Caddy reads the password on stdin when no terminal is attached.
  hash="$(printf '%s\n' "$password" | dc exec -T caddy caddy hash-password)"
  unset password
  [[ "$hash" == \$2* ]] || die "caddy hash-password did not return a bcrypt hash"
  set_env_value ANUM_MONITORING_BASIC_AUTH_USER "$user"
  set_env_value ANUM_MONITORING_BASIC_AUTH_HASH "$hash"
}

generate_secrets() {
  local name
  for name in GLITCHTIP_SECRET_KEY GLITCHTIP_DB_PASSWORD; do
    if [ -z "$(get_env "$name")" ]; then
      env_helper secret "$ENV_FILE" "$name" | set_env "$name"
      echo "Generated $name (copy it from $ENV_FILE into your password manager)"
    fi
  done
}

create_glitchtip_admin() {
  local email password
  read -r -p "GlitchTip admin e-mail (your login; GlitchTip sends no mail) [skip]: " email
  if [ -z "$email" ]; then
    echo "Skipped. Later: bash infra/oracle/monitoring.sh enable (keeps everything else)."
    return 0
  fi
  [[ "$email" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "not an e-mail address"
  ask_secret "GlitchTip admin password" password
  # E-mail and password go to Django on stdin, not on a command line.
  printf '%s\n%s\n' "$email" "$password" | dc exec -T glitchtip python manage.py shell -c '
import sys
from django.contrib.auth import get_user_model
email, password = sys.stdin.read().split("\n")[:2]
User = get_user_model()
user = User.objects.filter(email__iexact=email).first()
if user is None:
    User.objects.create_superuser(email=email, password=password)
    print("GlitchTip admin created")
else:
    user.set_password(password)
    user.is_superuser = True
    user.is_staff = True
    user.save()
    print("GlitchTip admin password updated")
'
  unset password
}

cmd_agent() {
  local key token
  [ -n "$(get_env ANUM_MONITOR_HOST)" ] || die "run: bash infra/oracle/monitoring.sh enable"
  cat <<EOF

Connect the Beszel agent:
  1. Open https://$(get_env ANUM_MONITOR_HOST) (basic-auth login first). On the first
     visit Beszel asks you to create its admin account.
  2. Click "Add System": name $(get_env BESZEL_SYSTEM_NAME), host "beszel-agent", leave the port.
  3. Copy the "Public Key" and the "Token" from that dialog, click "Add system", then
     paste both here.
EOF
  read -r -p "Public key (ssh-ed25519 ...) [skip]: " key
  if [ -z "$key" ]; then
    echo "Skipped. Later: bash infra/oracle/monitoring.sh agent"
    return 0
  fi
  [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]] || die "expected one line starting with ssh-ed25519"
  read -r -s -p "Token (hidden): " token; echo
  [[ "$token" =~ ^[A-Za-z0-9_-]{8,}$ ]] || die "the token has an unexpected format"
  set_env_value BESZEL_AGENT_KEY "$key"
  printf '%s\n' "$token" | set_env BESZEL_AGENT_TOKEN
  unset token
  profile_add monitoring-agent
  log "Starting the Beszel agent"
  dc up -d beszel-agent
  wait_healthy beszel-agent 120 || true
  echo "The system turns green in Beszel within a minute."
}

# Accepts the DSN GlitchTip shows (https://<key>@<ANUM_ERRORS_HOST>/<id>) and stores it
# for the private network: http://<key>@glitchtip:8000/<id> (no hairpin through the
# VM's public address; the API's startup policy allows http only to such a name).
cmd_dsn() {
  local dsn converted
  if [ "${1:-}" = "--clear" ]; then
    set_env_value ANUM_SENTRY_DSN ""
  else
    [ -n "$(get_env ANUM_ERRORS_HOST)" ] || die "run: bash infra/oracle/monitoring.sh enable"
    cat <<EOF

Connect the API to GlitchTip:
  1. Open https://$(get_env ANUM_ERRORS_HOST) (basic-auth login, then the GlitchTip admin).
  2. Create an organization, then a project (platform: Python / FastAPI).
  3. Project settings -> Client Keys (DSN): copy the DSN and paste it here.
EOF
    read -r -s -p "DSN (hidden) [skip]: " dsn; echo
    if [ -z "$dsn" ]; then
      echo "Skipped. Later: bash infra/oracle/monitoring.sh dsn"
      return 0
    fi
    converted="$(printf '%s\n' "$dsn" | python3 -c '
import sys
from urllib.parse import urlsplit
dsn = sys.stdin.read().strip()
try:
    parts = urlsplit(dsn)
    project = parts.path.rstrip("/").rsplit("/", 1)[-1]
    ok = parts.scheme in ("http", "https") and parts.username and parts.hostname and project.isdigit()
except ValueError:
    ok = False
if not ok:
    sys.exit("not a DSN: expected https://<key>@<host>/<project id>")
print(f"http://{parts.username}@glitchtip:8000/{project}")
')" || die "that is not a GlitchTip DSN"
    printf '%s\n' "$converted" | set_env ANUM_SENTRY_DSN
  fi
  log "Recreating the API with the new ANUM_SENTRY_DSN"
  dc up -d --no-deps api
  wait_healthy api 300 || die "the API did not become healthy; check: docker compose logs --tail 100 api"
}

cmd_enable() {
  log "Monitoring hostnames (DNS A records must point at this VM, as for the other three)"
  ask_host ANUM_MONITOR_HOST "Beszel (host metrics) hostname" "anum-monitor-yourname.duckdns.org"
  ask_host ANUM_ERRORS_HOST "GlitchTip (errors) hostname" "anum-errors-yourname.duckdns.org"
  [ "$(get_env ANUM_MONITOR_HOST)" != "$(get_env ANUM_ERRORS_HOST)" ] || die "use two different hostnames"
  for name in ANUM_WEB_HOST ANUM_API_HOST ANUM_AUTH_HOST; do
    if [ "$(get_env "$name")" = "$(get_env ANUM_MONITOR_HOST)" ] || [ "$(get_env "$name")" = "$(get_env ANUM_ERRORS_HOST)" ]; then
      die "a monitoring hostname equals $name; use new names"
    fi
  done
  add_duckdns_names
  check_dns

  log "Basic auth for both monitoring sites"
  # Caddy hashes the password; start it if needed, but do not recreate it yet.
  dc up -d --no-deps --no-recreate caddy >/dev/null
  configure_basic_auth
  generate_secrets

  log "GlitchTip's database login (db-init)"
  dc up -d --wait postgres
  dc run --rm --no-deps db-init

  log "Pulling and starting Beszel's hub and GlitchTip"
  dc --profile monitoring pull "${HUB_SERVICES[@]}"
  dc --profile monitoring up -d --no-deps "${HUB_SERVICES[@]}"
  # From here on, `docker compose up` and update.sh keep them running.
  profile_add monitoring

  enable_caddy_sites
  wait_healthy beszel 120 || true
  echo "GlitchTip runs its migrations on the first start (a few minutes)."
  wait_healthy glitchtip 600 || die "GlitchTip did not start; fix it, then run enable again"

  log "GlitchTip admin account"
  create_glitchtip_admin
  cmd_agent
  cmd_dsn
  cmd_status
}

cmd_disable() {
  log "Turning monitoring off (data volumes, GlitchTip's database and the secrets stay)"
  profile_remove monitoring-agent
  profile_remove monitoring
  disable_caddy_sites
  dc --profile monitoring --profile monitoring-agent rm --stop --force "${ALL_SERVICES[@]}"
  if [[ "$(get_env ANUM_SENTRY_DSN)" == *"@glitchtip:"* ]]; then
    echo "The API reported errors to this GlitchTip; clearing ANUM_SENTRY_DSN."
    set_env_value ANUM_SENTRY_DSN ""
    dc up -d --no-deps api
  fi
  echo "Off. 'enable' turns it back on with the same data (then run 'dsn' again)."
}

cmd_purge() {
  local answer volume
  echo "This deletes Beszel's history, the agent's identity, GlitchTip's events, its"
  echo "database and login, and the monitoring values in $ENV_FILE."
  read -r -p "Type purge to continue: " answer
  [ "$answer" = "purge" ] || die "nothing deleted"
  cmd_disable
  log "Dropping GlitchTip's database and login"
  dc up -d --wait postgres
  dc exec -T -u postgres postgres psql -X -q -v ON_ERROR_STOP=1 -d postgres \
    -c 'drop database if exists glitchtip with (force)' \
    -c 'drop role if exists glitchtip'
  log "Deleting the monitoring volumes"
  for volume in "${VOLUMES[@]}"; do
    if "${DOCKER[@]}" volume rm "anum_$volume" >/dev/null 2>&1; then
      echo "removed anum_$volume"
    fi
  done
  for name in ANUM_MONITORING_BASIC_AUTH_USER ANUM_MONITORING_BASIC_AUTH_HASH GLITCHTIP_SECRET_KEY \
    GLITCHTIP_DB_PASSWORD BESZEL_AGENT_KEY BESZEL_AGENT_TOKEN; do
    set_env_value "$name" ""
  done
  echo "Purged. The hostnames stay in .env (and in DUCKDNS_DOMAINS); remove their DNS records if unused."
}

cmd_status() {
  local host code
  log "Monitoring status"
  echo "COMPOSE_PROFILES=$(get_env COMPOSE_PROFILES)"
  if [ -f "$SITE_LINK" ]; then echo "Caddy sites: enabled"; else echo "Caddy sites: disabled"; fi
  if [ -n "$(get_env ANUM_SENTRY_DSN)" ]; then echo "API error reporting: on"; else echo "API error reporting: off"; fi
  dc --profile monitoring --profile monitoring-agent ps "${ALL_SERVICES[@]}" || true
  local ids
  ids="$(dc --profile monitoring --profile monitoring-agent ps -q "${ALL_SERVICES[@]}" 2>/dev/null || true)"
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086 # one container id per word
    "${DOCKER[@]}" stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $ids || true
  fi
  free -m | awk 'NR<=2'
  if [ -f "$SITE_LINK" ] && command -v curl >/dev/null; then
    for host in "$(get_env ANUM_MONITOR_HOST)" "$(get_env ANUM_ERRORS_HOST)"; do
      [ -n "$host" ] || continue
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$host/" || true)"
      # 401: Caddy answers and asks for the basic-auth login, as it should.
      echo "https://$host/ -> HTTP $code (401 expected without the login)"
    done
  fi
}

case "${1:-}" in
  enable) cmd_enable ;;
  agent) cmd_agent ;;
  dsn) shift; cmd_dsn "${1:-}" ;;
  status) cmd_status ;;
  disable) cmd_disable ;;
  purge) cmd_purge ;;
  *) usage ;;
esac
