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
# cache. A failed migration stops here, before any service is replaced.
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
