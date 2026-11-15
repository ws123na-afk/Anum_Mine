#!/usr/bin/env bash
# Update ANUM on the VM (docs/deploy-oracle.md#updates). Run as root or a docker-group
# user; anum-update.timer runs it daily when ANUM_AUTO_UPDATE=true.
#
#   bash infra/oracle/update.sh            git pull --ff-only, then deploy
#   bash infra/oracle/update.sh --no-pull  deploy the current checkout (install.sh)
#   bash infra/oracle/update.sh --no-smoke skip the smoke test at the end (install.sh
#                                          runs its own, waiting for the certificates)
#
# Deploy = complete .env with any new variables, check that enough memory is free, keep
# the running images as `:previous`, build the images on this VM one at a time (base
# images re-pulled for security fixes), run db-init, take a fresh backup when there are
# migrations to apply (a failed backup stops the update), run the migrations as a
# one-shot (the running API keeps serving: migrations are expand/contract), recreate
# what changed, wait for the API and the web app to be healthy, then prune dangling
# images and old build cache and run smoke.sh. A failed migration stops here, before
# any service is replaced. When the new API or web app never becomes healthy, the
# previous images are started again and the script exits non-zero. At the end it
# prints a notice when no user of the Keycloak realm `anum` holds the org_admin role
# (docs/deploy-oracle.md#organization-administrators); it never grants it.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$ORACLE_DIR/../.." && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
log() { printf '\n==> %s\n' "$*"; }

PULL=true
SMOKE=true
for argument in "$@"; do
  case "$argument" in
    --no-pull) PULL=false ;;
    --no-smoke) SMOKE=false ;;
    *) echo "usage: $0 [--no-pull] [--no-smoke]" >&2; exit 2 ;;
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

# The images a failed update goes back to. Keycloak's is kept for a manual rollback
# only: Keycloak upgrades its own database on start, and an older Keycloak may not run
# on it, so the automatic rollback never swaps it.
ROLLBACK_IMAGES=(anum/api anum/web)
KEPT_IMAGES=(anum/api anum/web anum/keycloak)

# Building on the VM that serves the site: one image at a time, and not at all when
# memory is already short (the neighbours' containers and ANUM's own would be pushed
# into swap or killed). MemAvailable counts page cache the kernel can give back.
MIN_BUILD_MEMORY_MB="${ANUM_UPDATE_MIN_BUILD_MEMORY_MB:-2500}"
[[ "$MIN_BUILD_MEMORY_MB" =~ ^[0-9]+$ ]] || { echo "ERROR: ANUM_UPDATE_MIN_BUILD_MEMORY_MB must be a number of megabytes" >&2; exit 2; }
available_mb="$(awk '$1 == "MemAvailable:" { print int($2 / 1024) }' /proc/meminfo)"
if [ -n "$available_mb" ] && [ "$available_mb" -lt "$MIN_BUILD_MEMORY_MB" ]; then
  echo "ERROR: only $available_mb MB of memory is available; building the images needs about $MIN_BUILD_MEMORY_MB MB free." >&2
  echo "Nothing was changed and the running version keeps serving. See what uses the memory with" >&2
  echo "'free -m' and 'docker stats --no-stream', free some (stop a stack you do not need right now)" >&2
  echo "and run this script again." >&2
  exit 1
fi

log "Keeping the running images as :previous"
for image in "${KEPT_IMAGES[@]}"; do
  if docker image inspect "$image:local" >/dev/null 2>&1; then
    docker tag "$image:local" "$image:previous"
  else
    echo "$image:local does not exist yet (first install): nothing to keep"
  fi
done

log "Building images, one at a time"
# Every service with a build section in the profiles in use (jobs, plus COMPOSE_PROFILES
# from .env such as browser or ocr), once per image: migrate and retention share the
# API's image, restore-tools the backup image.
build_targets() {
  dc --profile jobs config --format json | python3 -c '
import json, sys
seen = set()
for name, service in sorted(json.load(sys.stdin)["services"].items()):
    if not service.get("build"):
        continue
    image = service.get("image") or name
    if image not in seen:
        seen.add(image)
        print(name)
'
}
targets="$(build_targets)"
[ -n "$targets" ] || { echo "ERROR: no service to build was found in compose.yaml" >&2; exit 1; }
for target in $targets; do
  echo "-- $target"
  dc --profile jobs build --pull "$target"
done
if ! grep -Eq '^COMPOSE_PROFILES=(.*,)?browser(,.*)?$' "$ENV_FILE" && grep -Eq '^ANUM_BROWSER_ACTION_MODE=(1|true)$' "$ENV_FILE"; then
  log "ANUM_BROWSER_ACTION_MODE=1 but the browser profile is not in COMPOSE_PROFILES: the action browser stays unavailable"
fi

log "Starting the database and applying roles"
dc up -d --wait postgres valkey
dc run --rm --no-deps db-init

# A fresh backup before any migration: compare the revision the database is at with the
# newest revision in the image just built. An empty database (first install) has no
# revision and nothing to back up.
revisions() { dc run --rm --no-deps -T migrate python -m alembic "$1" 2>/dev/null | awk 'NF { print $1 }' | sort | tr '\n' ' '; }
if ! current_revision="$(revisions current)"; then
  echo "ERROR: could not read the database's current revision (alembic current). Nothing was replaced." >&2
  exit 1
fi
if ! head_revision="$(revisions heads)"; then
  echo "ERROR: could not read the newest revision (alembic heads). Nothing was replaced." >&2
  exit 1
fi
if [ -z "${head_revision// /}" ]; then
  echo "ERROR: the new image lists no migration head. Nothing was replaced." >&2
  exit 1
fi
if [ -z "${current_revision// /}" ]; then
  echo "empty database: no backup needed before the first migration"
elif [ "$current_revision" != "$head_revision" ]; then
  log "Migrations to apply (${current_revision% } -> ${head_revision% }): taking a backup first"
  if ! bash "$ORACLE_DIR/backup.sh" backup; then
    echo "ERROR: the backup before the migrations failed, so the update stopped. Nothing was migrated or" >&2
    echo "replaced and the running version keeps serving. Fix the backup (run it by hand to see why:" >&2
    echo "bash $ORACLE_DIR/backup.sh backup), then run this script again." >&2
    exit 1
  fi
else
  echo "database schema already at ${head_revision% }: no migration, no extra backup"
fi

log "Applying migrations"
# Before any service is recreated: the running API keeps serving on the old code while
# `alembic upgrade head` applies new revisions (expand-only, e.g. 0018_conversations
# adds two tables), and a failed migration stops the update here.
dc run --rm --no-deps migrate
echo "database schema now at: $(dc run --rm --no-deps -T migrate python -m alembic current 2>/dev/null | tail -n 1)"

health_of() {
  local container
  container="$(dc ps -q "$1" 2>/dev/null | head -n 1)"
  [ -n "$container" ] || { echo missing; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo missing
}
# wait_healthy <service> <attempts of 5 s>: 0 once the container reports healthy.
wait_healthy() {
  local service="$1" attempts="$2" attempt status=missing
  for attempt in $(seq 1 "$attempts"); do
    status="$(health_of "$service")"
    if [ "$status" = "healthy" ]; then
      echo "$service is healthy"
      return 0
    fi
    [ "$attempt" -eq "$attempts" ] || sleep 5
  done
  echo "$service is not healthy after $((attempts * 5 / 60)) minutes ($status)" >&2
  return 1
}

# The new version did not come up: start the previous images again. The migrations stay
# applied; they are expand/contract, so the previous code runs on the new schema.
# --no-deps: the old image's `migrate` one-shot must not run against the newer schema.
roll_back() {
  local reason="$1" image
  echo "ERROR: $reason" >&2
  dc ps || true
  dc logs --tail 40 api web 2>&1 | tail -n 80 >&2 || true
  for image in "${ROLLBACK_IMAGES[@]}"; do
    if ! docker image inspect "$image:previous" >/dev/null 2>&1; then
      echo "ERROR: there is no $image:previous image to go back to (first install?). The site may be down." >&2
      echo "Logs: docker compose logs api web" >&2
      exit 1
    fi
  done
  log "Going back to the previous version"
  for image in "${ROLLBACK_IMAGES[@]}"; do
    docker tag "$image:local" "$image:failed" 2>/dev/null || true
    docker tag "$image:previous" "$image:local"
  done
  dc up -d --no-deps --no-build --force-recreate api web || true
  if wait_healthy api 60 && wait_healthy web 24; then
    echo "ERROR: the update failed, and the OLD version is running again. The database keeps the new" >&2
    echo "migrations, which the old version works with. The failed images are kept as anum/api:failed and" >&2
    echo "anum/web:failed. Read the logs above, fix the cause and run this script again." >&2
  else
    echo "ERROR: the update failed and the old version did not become healthy either: THE SITE IS DOWN." >&2
    echo "Look at: docker compose ps, docker compose logs --tail 200 api web" >&2
  fi
  exit 1
}

log "Starting or recreating the services"
dc up -d --remove-orphans || roll_back "docker compose up failed while starting the new version"

log "Waiting for the API and the web app to be healthy"
wait_healthy api 60 || roll_back "the new API is not healthy after 5 minutes"
wait_healthy web 24 || roll_back "the new web app is not healthy after 2 minutes"
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

# Restart containers Docker reports as unhealthy (docs/deploy-oracle.md#monitoring-basics).
# VMs installed before the timer existed get it here; needs root, so a docker-group
# user without it only sees the hint.
if ! systemctl is-enabled --quiet anum-heal.timer 2>/dev/null; then
  if [ "$(id -u)" -eq 0 ]; then
    log "Installing the unhealthy-container restart timer (anum-heal.timer)"
    bash "$ORACLE_DIR/heal.sh" install || echo "WARNING: could not install anum-heal.timer; run: sudo bash $ORACLE_DIR/heal.sh install" >&2
  else
    echo "NOTE: anum-heal.timer is not installed; run once: sudo bash $ORACLE_DIR/heal.sh install"
  fi
fi
# The monthly restore drill (docs/system-health.md#restore-drill), for VMs installed before it existed.
if ! systemctl is-enabled --quiet anum-drill.timer 2>/dev/null; then
  if [ "$(id -u)" -eq 0 ]; then
    log "Installing the monthly restore drill timer (anum-drill.timer)"
    bash "$ORACLE_DIR/backup.sh" drill-timer || echo "WARNING: could not install anum-drill.timer; run: sudo bash $ORACLE_DIR/backup.sh drill-timer" >&2
  else
    echo "NOTE: anum-drill.timer is not installed; run once: sudo bash $ORACLE_DIR/backup.sh drill-timer"
  fi
fi

if [ "$SMOKE" = true ] && [ -f "$ORACLE_DIR/smoke.sh" ]; then
  # Keycloak takes a few minutes after a rebuild; the smoke test reads its OIDC discovery.
  wait_healthy keycloak 72 || echo "WARNING: Keycloak is not healthy yet (docker compose logs --tail 100 keycloak)" >&2
  log "Smoke test (infra/oracle/smoke.sh)"
  if as_repo_owner bash "$ORACLE_DIR/smoke.sh" --wait 60; then
    echo "smoke test: passed"
  else
    echo "ERROR: the new version is running, but the smoke test failed (summary above)." >&2
    echo "Fix those items and run: bash $ORACLE_DIR/smoke.sh" >&2
    exit 1
  fi
fi
