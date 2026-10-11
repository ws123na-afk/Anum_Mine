#!/usr/bin/env bash
# Two-factor sign-in for the `anum` realm (docs/deploy-oracle.md#two-factor-sign-in,
# docs/identity.md#two-factor-sign-in). Keycloak imports the realm only on its first
# start, so this script is how a running server gets the change. Run it on the VM as the
# user who ran install.sh (docker group, no sudo), from anywhere:
#
#   keycloak-2fa.sh                      apply: bind the anum-browser-2fa flow, OTP and
#                                        passkey policies, required actions (idempotent)
#   keycloak-2fa.sh status               show the browser flow and the policies
#   keycloak-2fa.sh reset-user <name>    remove a user's authenticator app and recovery
#                                        codes and lift a sign-in lockout (they set up a
#                                        new app at the next sign-in)
#
#   --temporary-admin   sign in with a temporary master-realm service account created by
#                       `kc.sh bootstrap-admin service` inside the container, deleted again
#                       at the end. Use it when your admin has OTP (kcadm cannot answer an
#                       OTP prompt) or when nobody can sign in to the admin console.
#
# Without --temporary-admin it signs in as a master-realm admin: the bootstrap admin from
# .env while KC_BOOTSTRAP_ADMIN_PASSWORD is still set there, otherwise the username and
# password you type (read without echo). Secrets reach the container on stdin, never on a
# command line, and are never printed. kcadm's token file lives in the container's /tmp
# and is deleted on exit.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TWO_FACTOR="$ORACLE_DIR/../keycloak/two_factor.py"
ENV_FILE="$ORACLE_DIR/.env"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"; }
usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

TEMPORARY_ADMIN=false
ARGS=()
for argument in "$@"; do
  case "$argument" in
    --temporary-admin) TEMPORARY_ADMIN=true ;;
    -h|--help) usage ;;
    *) ARGS+=("$argument") ;;
  esac
done
ACTION="${ARGS[0]:-apply}"
case "$ACTION" in
  apply|status) [ "${#ARGS[@]}" -le 1 ] || usage ;;
  reset-user) [ "${#ARGS[@]}" -eq 2 ] || usage; RESET_USERNAME="${ARGS[1]}" ;;
  *) usage ;;
esac

[ -r "$ENV_FILE" ] || die "cannot read $ENV_FILE; run this as the user who ran install.sh"
[ -f "$TWO_FACTOR" ] || die "$TWO_FACTOR is missing; update the checkout (update.sh)"
command -v python3 >/dev/null || die "python3 is missing"
AUTH_HOST="$(env_value ANUM_AUTH_HOST)"
[ -n "$AUTH_HOST" ] || die "ANUM_AUTH_HOST is not set in $ENV_FILE"
dc ps --status running --services 2>/dev/null | grep -qx keycloak \
  || die "Keycloak is not running; start it with: cd $ORACLE_DIR && docker compose up -d keycloak"

SERVER="http://localhost:8080"
KCADM_CONFIG="/tmp/anum-kcadm-2fa-$$.config"
KCADM=(docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml"
  exec -T keycloak /opt/keycloak/bin/kcadm.sh)
TEMP_CLIENT=""

cleanup() {
  if [ -n "$TEMP_CLIENT" ]; then
    local id
    id="$("${KCADM[@]}" get clients -r master -q clientId="$TEMP_CLIENT" --fields id --format csv --noquotes \
      --config "$KCADM_CONFIG" 2>/dev/null | head -n 1)"
    if [ -n "$id" ] && "${KCADM[@]}" delete "clients/$id" -r master --config "$KCADM_CONFIG" >/dev/null 2>&1; then
      echo "Deleted the temporary admin $TEMP_CLIENT."
    else
      printf 'WARNING: delete the temporary admin yourself: admin console, realm master -> Clients -> %s -> Delete\n' "$TEMP_CLIENT" >&2
    fi
  fi
  dc exec -T keycloak rm -f "$KCADM_CONFIG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Reads one line (the secret) from stdin into the variable named by $1 inside the
# container, then runs the rest of the arguments with it exported.
with_secret() {
  local name="$1"; shift
  dc exec -T keycloak bash -c "IFS= read -r $name && export $name && exec \"\$@\"" bash "$@"
}

if [ "$TEMPORARY_ADMIN" = true ]; then
  TEMP_CLIENT="anum-2fa-temporary-$(date -u +%Y%m%d%H%M%S)"
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "Creating the temporary admin $TEMP_CLIENT (kc.sh bootstrap-admin service; takes about a minute)"
  printf '%s\n' "$secret" | with_secret ANUM_TEMPORARY_ADMIN_SECRET \
    /opt/keycloak/bin/kc.sh bootstrap-admin service --optimized --no-prompt \
      --client-id "$TEMP_CLIENT" --client-secret:env ANUM_TEMPORARY_ADMIN_SECRET >/dev/null \
    || { TEMP_CLIENT=""; die "kc.sh bootstrap-admin failed; see docs/deploy-oracle.md#two-factor-sign-in"; }
  printf '%s\n' "$secret" | with_secret KC_CLI_CLIENT_SECRET \
    /opt/keycloak/bin/kcadm.sh config credentials --config "$KCADM_CONFIG" \
      --server "$SERVER" --realm master --client "$TEMP_CLIENT" >/dev/null \
    || die "the temporary admin could not sign in"
  unset secret
else
  username="$(env_value KC_BOOTSTRAP_ADMIN_USERNAME)"
  password="$(env_value KC_BOOTSTRAP_ADMIN_PASSWORD)"
  if [ -z "$password" ]; then
    [ -t 0 ] || die "no bootstrap admin in .env and no terminal to ask; run it in an SSH session"
    read -r -p "Keycloak admin (realm master) username${username:+ [$username]}: " typed
    username="${typed:-$username}"
    [ -n "$username" ] || die "no username"
    read -r -s -p "Password for $username: " password
    echo
  fi
  printf '%s\n' "$password" | with_secret KC_CLI_PASSWORD \
    /opt/keycloak/bin/kcadm.sh config credentials --config "$KCADM_CONFIG" \
      --server "$SERVER" --realm master --user "$username" >/dev/null \
    || die "sign-in as $username failed: wrong password, or the account has OTP (kcadm cannot answer it). Run again with --temporary-admin."
  unset password
fi

case "$ACTION" in
  apply)
    python3 "$TWO_FACTOR" apply --rp-id "$AUTH_HOST" --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    echo
    python3 "$TWO_FACTOR" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    ;;
  status) python3 "$TWO_FACTOR" status --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
  reset-user) python3 "$TWO_FACTOR" reset-user --username "$RESET_USERNAME" --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
esac
