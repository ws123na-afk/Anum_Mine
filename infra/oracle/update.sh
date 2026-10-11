#!/usr/bin/env bash
# Update ANUM on the VM (docs/deploy-oracle.md#updates). Run as root or a docker-group
# user; anum-update.timer runs it daily when ANUM_AUTO_UPDATE=true.
#
#   bash infra/oracle/update.sh            git pull --ff-only, then deploy
#   bash infra/oracle/update.sh --no-pull  deploy the current checkout (install.sh)
#
# Deploy = complete .env with any new variables, build the images on this VM (base
# images re-pulled for security fixes), run db-init and the migrations as one-shots
# (the running API keeps serving: migrations are expand/contract), recreate what
# changed, wait for the API to be healthy, then prune dangling images and old build
# cache. A failed migration stops here, before any service is replaced. At the end it
# prints a notice when no user of the Keycloak realm `anum` holds the org_admin role
# (docs/deploy-oracle.md#organization-administrators); it never grants it.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$ORACLE_DIR/../.." && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
log() { printf '\n==> %s\n' "$*"; }

PULL=true
for argument in "$@"; do
  case "$argument" in
    --no-pull) PULL=false ;;
    *) echo "usage: $0 [--no-pull]" >&2; exit 2 ;;
  esac
done

[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE is missing; run install.sh first" >&2; exit 1; }
REPO_OWNER="$(stat -c %U "$REPO_DIR")"
as_repo_owner() {
  if [ "$(id -un)" = "$REPO_OWNER" ]; then "$@"; else runuser -u "$REPO_OWNER" -- "$@"; fi
}

if [ "$PULL" = true ]; then
  log "Pulling $(as_repo_owner git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
  before="$(as_repo_owner git -C "$REPO_DIR" rev-parse HEAD)"
  as_repo_owner git -C "$REPO_DIR" pull --ff-only
  after="$(as_repo_owner git -C "$REPO_DIR" rev-parse HEAD)"
  echo "now at $(as_repo_owner git -C "$REPO_DIR" log -1 --format='%h %s')"
  [ "$before" != "$after" ] || echo "no new commits; rebuilding for base image updates"
fi

log "Completing $ENV_FILE with new variables, if any"
as_repo_owner python3 "$ORACLE_DIR/configure_env.py" "$ENV_FILE" --example "$ORACLE_DIR/.env.example" --non-interactive
# Duplicate or invalid host names, or a malformed admin allow-list, would break Caddy or
# the issuer: stop before building. A world-open allow-list is only a warning here.
as_repo_owner python3 "$ORACLE_DIR/configure_env.py" "$ENV_FILE" --check

log "Checking the compose configuration"
dc config --quiet

log "Building images"
dc --profile jobs build --pull

log "Starting the database and applying roles and migrations"
dc up -d --wait postgres valkey
dc run --rm --no-deps db-init
# Before any service is recreated: the running API keeps serving on the old code while
# `alembic upgrade head` applies new revisions (expand-only, e.g. 0018_conversations
# adds two tables), and a failed migration stops the update here.
dc run --rm --no-deps migrate
echo "database schema now at: $(dc run --rm --no-deps -T migrate python -m alembic current 2>/dev/null | tail -n 1)"

log "Starting or recreating the services"
dc up -d --remove-orphans

log "Waiting for the API to be healthy"
for attempt in $(seq 1 60); do
  container="$(dc ps -q api)"
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo missing)"
  if [ "$status" = "healthy" ]; then
    echo "api is healthy"
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "ERROR: the API is not healthy after 5 minutes ($status). Logs: docker compose logs api" >&2
    dc ps
    exit 1
  fi
  sleep 5
done
dc ps

log "Pruning dangling images and build cache older than a week"
docker image prune -f >/dev/null
docker builder prune -f --filter until=168h >/dev/null

# Who may change tenant-wide policy. Keycloak imports the realm only on its first start,
# so a realm from before the org_admin role existed lacks it, and then every change to
# policy packs, role templates, approval rules or memory governance answers 403, owners
# included. A read-only look at Keycloak's own tables needs no Keycloak admin: the
# postgres container's peer-authenticated superuser, in a read-only transaction. It
# prints "<realms> <roles> <holders>"; when Keycloak has not imported the realm yet or
# the query fails (a schema change), nothing is printed and so is no notice.
org_admin_counts() {
  dc exec -T -u postgres -e PGOPTIONS='-c default_transaction_read_only=on' postgres \
    psql -X -q -t -A -v ON_ERROR_STOP=1 --dbname=keycloak -c "
      with realm_anum as (select id from realm where name = 'anum'),
           org_admin as (select r.id from keycloak_role r join realm_anum m
                           on m.id in (r.realm_id, r.client_realm_constraint)
                         where r.name = 'org_admin' and not r.client_role)
      select (select count(*) from realm_anum) || ' ' || (select count(*) from org_admin) || ' ' ||
             ((select count(*) from user_role_mapping where role_id in (select id from org_admin))
              + (select count(*) from group_role_mapping where role_id in (select id from org_admin)))" \
    2>/dev/null || true
}
read -r realms roles holders <<<"$(org_admin_counts)" || true
grant_hint="bash $ORACLE_DIR/keycloak-2fa.sh grant-org-admin <your ANUM username> (docs/deploy-oracle.md#organization-administrators)"
if [ "${realms:-}" = 1 ] && [ "${roles:-}" = 0 ]; then
  log "NOTICE: Keycloak realm anum has no org_admin role yet, so nobody can change policy packs, role templates, approval rules or memory governance. Create and grant it: $grant_hint"
elif [ "${realms:-}" = 1 ] && [ "${roles:-}" = 1 ] && [ "${holders:-}" = 0 ]; then
  log "NOTICE: nobody in Keycloak realm anum holds org_admin, so nobody can change policy packs, role templates, approval rules or memory governance. Grant it: $grant_hint"
fi
