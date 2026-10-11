#!/bin/sh
# Database roles for ANUM on one VM; the `db-init` one-shot in infra/oracle/compose.yaml
# runs it on every `docker compose up` (docs/deploy-oracle.md#what-gets-created).
#
# As the cluster superuser (PG* variables from compose), idempotently:
#   1. db/cluster.sql      Keycloak's own login and database; the anum_backup login.
#   2. bootstrap-database.sql (infra/helm, the same file as on Render and Kubernetes)
#                          anum_migrator, anum_app, anum_relay and the NOLOGIN roles.
#   3. db/anum.sql         passwords from .env (the source of truth, so rotating one is
#                          "edit .env, docker compose up -d"), and CONNECT only for the
#                          logins that need each database.
#
# Passwords are handed to psql as \set lines on stdin, never on a command line, and are
# never printed.
set -eu

for name in ANUM_MIGRATOR_PASSWORD ANUM_APP_PASSWORD ANUM_RELAY_PASSWORD ANUM_BACKUP_DB_PASSWORD KEYCLOAK_DB_PASSWORD; do
  value="$(printenv "$name" || true)"
  # install.sh generates url-safe base64; anything else would need quoting in psql.
  case "$value" in
    "" | *[!A-Za-z0-9_-]*)
      echo "db-init: $name must be set to url-safe base64 (letters, digits, - and _); run install.sh or configure_env.py" >&2
      exit 2
      ;;
  esac
  if [ "${#value}" -lt 24 ]; then
    echo "db-init: $name is shorter than 24 characters" >&2
    exit 2
  fi
done

run_psql() {
  database="$1"
  shift
  {
    printf '\\set migrator_password %s\n' "$ANUM_MIGRATOR_PASSWORD"
    printf '\\set app_password %s\n' "$ANUM_APP_PASSWORD"
    printf '\\set relay_password %s\n' "$ANUM_RELAY_PASSWORD"
    printf '\\set backup_password %s\n' "$ANUM_BACKUP_DB_PASSWORD"
    printf '\\set keycloak_password %s\n' "$KEYCLOAK_DB_PASSWORD"
    for file in "$@"; do
      cat "$file"
    done
  } | psql -X -q -v ON_ERROR_STOP=1 --dbname="$database"
}

run_psql postgres /init/db/cluster.sql
run_psql anum /init/bootstrap-database.sql /init/db/anum.sql
echo "db-init: roles and databases are in place"
