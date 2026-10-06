#!/usr/bin/env bash
# Authenticated journey: start Keycloak, PostgreSQL and NATS from the compose stack,
# migrate, run the API with OIDC + PostgreSQL + NATS as a non-superuser database role,
# and drive services/api/scripts/journey.py against it. Used by the "Authenticated
# journey" CI job and runnable locally from any directory (see .claude/skills/anum-verify).
#
# Environment:
#   PYTHON           interpreter with services/api installed (default: python)
#   JOURNEY_API_LOG  where the API log goes (default: $TMPDIR/anum-journey-api.log)
#   JOURNEY_DOWN=1   stop the services and delete their volumes when done
#
# Every credential here is a DEV-ONLY placeholder from infra/docker/compose.yaml and
# infra/keycloak/anum-realm.json, or the throwaway application role created below.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
API_DIR="$ROOT/services/api"
PYTHON="${PYTHON:-python}"
API_LOG="${JOURNEY_API_LOG:-${TMPDIR:-/tmp}/anum-journey-api.log}"
COMPOSE=(docker compose -f "$ROOT/infra/docker/compose.yaml")

ADMIN_DATABASE_URL="postgresql+psycopg://anum:anum@localhost:5432/anum"
APP_DATABASE_URL="postgresql+psycopg://anum_app:anum_app@localhost:5432/anum"
RELAY_DATABASE_URL="postgresql+psycopg://anum_relay:anum_relay@localhost:5432/anum"
ISSUER="http://localhost:8080/realms/anum"
API_URL="http://127.0.0.1:8000"

log() { printf '[run_journey] %s\n' "$*"; }
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }

wait_for() {
  local name="$1" seconds="$2"; shift 2
  for _ in $(seq 1 "$seconds"); do
    if "$@" >/dev/null 2>&1; then
      log "$name is ready"
      return 0
    fi
    sleep 1
  done
  fail "$name was not ready after ${seconds}s"
}

API_PID=""
cleanup() {
  local status=$?
  if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ] && [ -f "$API_LOG" ]; then
    log "API log (last 80 lines of $API_LOG):"
    tail -n 80 "$API_LOG" || true
  fi
  if [ "${JOURNEY_DOWN:-0}" = "1" ]; then
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

log "Starting PostgreSQL, NATS JetStream and Keycloak"
"${COMPOSE[@]}" up -d postgres nats keycloak

wait_for "PostgreSQL" 90 "${COMPOSE[@]}" exec -T postgres pg_isready -U anum -d anum
wait_for "NATS JetStream" 60 curl -fsS "http://localhost:8222/healthz?js-enabled-only=true"
wait_for "Keycloak realm anum" 240 curl -fsS "$ISSUER/.well-known/openid-configuration"

log "Applying migrations"
(cd "$API_DIR" && ANUM_DATABASE_URL="$ADMIN_DATABASE_URL" "$PYTHON" -m alembic upgrade head)

# The compose user owns the tables and is a superuser, which bypasses row-level
# security. The API must connect as a plain login role so RLS is enforced.
log "Creating the non-superuser application role anum_app"
"${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U anum -d anum >/dev/null <<'SQL'
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anum_app') then
    create role anum_app login password 'anum_app' nosuperuser nobypassrls nocreaterole nocreatedb;
  end if;
end
$$;
grant usage on schema public to anum_app;
grant select, insert, update, delete on all tables in schema public to anum_app;
grant usage, select on all sequences in schema public to anum_app;
SQL

# The outbox relay publishes committed events through its own login that holds only
# the narrow anum_outbox_relay role from migration 0007 (docs/events.md).
log "Creating the outbox relay login anum_relay"
"${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U anum -d anum >/dev/null <<'SQL'
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anum_relay') then
    create role anum_relay login password 'anum_relay' nosuperuser nobypassrls nocreaterole nocreatedb;
  end if;
end
$$;
grant anum_outbox_relay to anum_relay;
SQL

log "Starting the API (oidc, postgresql, nats); log: $API_LOG"
(
  cd "$API_DIR"
  env \
    ANUM_ENVIRONMENT=local \
    ANUM_AUTH_MODE=oidc \
    ANUM_KEYCLOAK_ISSUER="$ISSUER" \
    ANUM_REPOSITORY_BACKEND=postgresql \
    ANUM_DATABASE_URL="$APP_DATABASE_URL" \
    ANUM_EVENT_BUS=nats \
    ANUM_NATS_URL=nats://localhost:4222 \
    ANUM_OUTBOX_DATABASE_URL="$RELAY_DATABASE_URL" \
    ANUM_MODEL_PROVIDER=mock \
    "$PYTHON" -m uvicorn anum_api.main:app --host 127.0.0.1 --port 8000
) >"$API_LOG" 2>&1 &
API_PID=$!

wait_for "API" 60 curl -fsS "$API_URL/health"
# The API creates the ANUM_EVENTS stream once it has connected to NATS.
wait_for "API connection to NATS (stream ANUM_EVENTS)" 60 \
  bash -c 'curl -fsS "http://localhost:8222/jsz?streams=true" | grep -q "\"ANUM_EVENTS\""'

log "Running the journey"
JOURNEY_API_URL="$API_URL" \
JOURNEY_ISSUER="$ISSUER" \
JOURNEY_NATS_URL=nats://localhost:4222 \
JOURNEY_DATABASE_URL="$APP_DATABASE_URL" \
  "$PYTHON" "$API_DIR/scripts/journey.py"
