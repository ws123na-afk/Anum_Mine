#!/usr/bin/env sh
set -eu

# Only run migrations when the app is actually configured to talk to
# PostgreSQL. The `memory` repository backend keeps all state in-process and
# has no database to migrate, so running Alembic in that mode would fail.
if [ "${ANUM_REPOSITORY_BACKEND:-memory}" = "postgresql" ]; then
  alembic upgrade head
fi

exec uvicorn anum_api.main:app --host 0.0.0.0 --port 8000
