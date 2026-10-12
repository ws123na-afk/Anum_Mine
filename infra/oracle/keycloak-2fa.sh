#!/usr/bin/env bash
# Two-factor sign-in and the ANUM realm roles for the `anum` realm
# (docs/deploy-oracle.md#two-factor-sign-in, docs/deploy-oracle.md#organization-administrators).
# Keycloak imports the realm only on its first start, so this script is how a running
# server gets later realm changes. Run it on the VM as the user who ran install.sh
# (docker group, no sudo), from anywhere:
#
#   keycloak-2fa.sh                      apply: bind the anum-browser-2fa flow, OTP and
#                                        passkey policies, required actions, and create
#                                        missing realm roles such as org_admin (idempotent)
#   keycloak-2fa.sh status               show the browser flow, the policies and org_admin
#   keycloak-2fa.sh reset-user <name>    remove a user's authenticator app and recovery
#                                        codes and lift a sign-in lockout (they set up a
#                                        new app at the next sign-in)
#   keycloak-2fa.sh ensure-roles         create missing realm roles only (org_admin ...)
#   keycloak-2fa.sh grant-org-admin <name>
#                                        give that user the org_admin realm role: they
#                                        may change tenant-wide policy for their tenant_id
#   keycloak-2fa.sh google | apple       add or update "Continue with Google / Apple"; asks
#                                        for the client ID and secret (Apple: Services ID,
#                                        Team ID, Key ID and the .p8 key file's path)
#   keycloak-2fa.sh remove-provider <google|apple>
#   keycloak-2fa.sh sign-up | sign-up-off
#                                        Keycloak's own sign-up page on or off (needs
#                                        ANUM_PRIVACY_POLICY_URL in .env and realm email)
#   keycloak-2fa.sh pending-users        accounts without an organization (tenant_id)
#   keycloak-2fa.sh assign-tenant <name> <tenant_id> [<workspace_id>]
#                                        put a pending account in an organization; a
#                                        workspace owner then invites it in ANUM
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
REALM_ROLES="$ORACLE_DIR/../keycloak/realm_roles.py"
SOCIAL_LOGIN="$ORACLE_DIR/../keycloak/social_login.py"
ENV_FILE="$ORACLE_DIR/.env"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"; }
usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

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
  apply|status|ensure-roles|google|apple|sign-up|sign-up-off|pending-users) [ "${#ARGS[@]}" -le 1 ] || usage ;;
  reset-user|grant-org-admin) [ "${#ARGS[@]}" -eq 2 ] || usage; TARGET_USERNAME="${ARGS[1]}" ;;
  remove-provider) [ "${#ARGS[@]}" -eq 2 ] || usage; PROVIDER="${ARGS[1]}" ;;
  assign-tenant)
    if ! { [ "${#ARGS[@]}" -ge 3 ] && [ "${#ARGS[@]}" -le 4 ]; }; then usage; fi
    TARGET_USERNAME="${ARGS[1]}"; TARGET_TENANT="${ARGS[2]}"; TARGET_WORKSPACE="${ARGS[3]:-}" ;;
  *) usage ;;
esac

[ -r "$ENV_FILE" ] || die "cannot read $ENV_FILE; run this as the user who ran install.sh"
[ -f "$TWO_FACTOR" ] || die "$TWO_FACTOR is missing; update the checkout (update.sh)"
[ -f "$REALM_ROLES" ] || die "$REALM_ROLES is missing; update the checkout (update.sh)"
[ -f "$SOCIAL_LOGIN" ] || die "$SOCIAL_LOGIN is missing; update the checkout (update.sh)"

# Provider values are asked for before anyone signs in to Keycloak, without echo for the
# secrets, and reach social_login.py on stdin (docs/deploy-oracle.md#google-and-apple-sign-in).
case "$ACTION" in
  google)
    [ -t 0 ] || die "run this in an SSH session: it asks for the Google client ID and secret"
    read -r -p "Google client ID (ends in .apps.googleusercontent.com): " google_client_id
    read -r -s -p "Google client secret: " google_secret
    echo
    if ! { [ -n "$google_client_id" ] && [ -n "$google_secret" ]; }; then die "both the client ID and the secret are needed"; fi
    ;;
  apple)
    [ -t 0 ] || die "run this in an SSH session: it asks for the Apple identifiers and key file"
    read -r -p "Apple Services ID (for example com.example.signin): " apple_services_id
    read -r -p "Apple Team ID (10 characters): " apple_team_id
    read -r -p "Apple Key ID (10 characters): " apple_key_id
    read -r -p "Path to the private key file on this VM (AuthKey_<KEYID>.p8): " apple_key_file
    [ -r "$apple_key_file" ] || die "cannot read $apple_key_file; copy the .p8 file to the VM first (scp), and delete it after"
    ;;
  sign-up)
    PRIVACY_URL="$(env_value ANUM_PRIVACY_POLICY_URL)"
    [ -n "$PRIVACY_URL" ] || die "set ANUM_PRIVACY_POLICY_URL in $ENV_FILE (the published privacy policy, https) first"
    ;;
esac
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
    python3 "$REALM_ROLES" ensure-roles --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    python3 "$SOCIAL_LOGIN" apply --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    echo
    python3 "$TWO_FACTOR" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    python3 "$REALM_ROLES" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    python3 "$SOCIAL_LOGIN" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    ;;
  status)
    python3 "$TWO_FACTOR" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    python3 "$REALM_ROLES" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    python3 "$SOCIAL_LOGIN" status --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    ;;
  google)
    printf '%s\n%s\n' "$google_client_id" "$google_secret" \
      | python3 "$SOCIAL_LOGIN" set-provider --provider google --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    unset google_secret
    echo "Next: set ANUM_SIGN_IN_PROVIDERS in $ENV_FILE (status prints the value), then: docker compose up -d api"
    ;;
  apple)
    { printf '%s\n%s\n%s\n' "$apple_services_id" "$apple_team_id" "$apple_key_id"; cat "$apple_key_file"; } \
      | python3 "$SOCIAL_LOGIN" set-provider --provider apple --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    echo "Delete the key file from the VM now: rm $apple_key_file (Apple keeps no copy; keep yours offline)."
    echo "Next: set ANUM_SIGN_IN_PROVIDERS in $ENV_FILE (status prints the value), then: docker compose up -d api"
    ;;
  remove-provider) python3 "$SOCIAL_LOGIN" remove-provider --provider "$PROVIDER" --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
  sign-up)
    python3 "$SOCIAL_LOGIN" enable-sign-up --privacy-url "$PRIVACY_URL" --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    echo "Next: ANUM_SELF_SIGN_UP=true in $ENV_FILE, then: docker compose up -d api"
    ;;
  sign-up-off)
    python3 "$SOCIAL_LOGIN" disable-sign-up --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    echo "Next: ANUM_SELF_SIGN_UP=false in $ENV_FILE, then: docker compose up -d api"
    ;;
  pending-users) python3 "$SOCIAL_LOGIN" pending-users --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
  assign-tenant)
    python3 "$SOCIAL_LOGIN" assign-tenant --username "$TARGET_USERNAME" --tenant "$TARGET_TENANT" \
      ${TARGET_WORKSPACE:+--workspace "$TARGET_WORKSPACE"} --config "$KCADM_CONFIG" -- "${KCADM[@]}"
    ;;
  reset-user) python3 "$TWO_FACTOR" reset-user --username "$TARGET_USERNAME" --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
  ensure-roles) python3 "$REALM_ROLES" ensure-roles --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
  grant-org-admin) python3 "$REALM_ROLES" grant-org-admin --username "$TARGET_USERNAME" --config "$KCADM_CONFIG" -- "${KCADM[@]}" ;;
esac
