-- One-time PostgreSQL bootstrap for an ANUM deployment (docs/deployment.md#database-roles).
--
-- Run as the cluster admin (superuser, or the managed service's admin role with
-- CREATEROLE) against the ANUM database, after `CREATE DATABASE anum`:
--
--   psql "$ADMIN_URL" -v ON_ERROR_STOP=1 \
--     -v migrator_password="$MIGRATOR_PASSWORD" \
--     -v app_password="$APP_PASSWORD" \
--     -v relay_password="$RELAY_PASSWORD" \
--     -f infra/helm/bootstrap-database.sql
--
-- Passwords come from the secret store and are passed as psql variables, never
-- written here. Safe to re-run: existing roles keep their passwords (rotate with
-- ALTER ROLE ... PASSWORD), grants are idempotent.
--
-- Logins it creates (NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE):
--   anum_migrator  owns the database and every table; the chart's migration Job
--                  (secrets.migration). Never used by the API.
--   anum_app       the API, worker and voice retention job (secrets.app
--                  ANUM_DATABASE_URL). Subject to RLS; member of anum_maintenance.
--   anum_relay     the outbox relay (secrets.app ANUM_OUTBOX_DATABASE_URL); member of
--                  anum_outbox_relay only.
-- NOLOGIN roles the migrations grant column-level access to (pre-created so the
-- migration login needs no CREATEROLE): anum_outbox_relay, anum_maintenance.
-- The backup login (BYPASSRLS + pg_read_all_data) is created separately, only where
-- the backup CronJob runs (docs/runbooks.md#backups).

\set ON_ERROR_STOP on

-- pgvector is not a trusted extension: create it as the admin; migration 0001's
-- `create extension if not exists vector` is then a no-op.
create extension if not exists vector;

select format('create role anum_outbox_relay nologin')
where not exists (select 1 from pg_roles where rolname = 'anum_outbox_relay') \gexec
select format('create role anum_maintenance nologin')
where not exists (select 1 from pg_roles where rolname = 'anum_maintenance') \gexec

select format('create role %I login password %L nosuperuser nobypassrls nocreatedb nocreaterole', 'anum_migrator', :'migrator_password')
where not exists (select 1 from pg_roles where rolname = 'anum_migrator') \gexec
select format('create role %I login password %L nosuperuser nobypassrls nocreatedb nocreaterole', 'anum_app', :'app_password')
where not exists (select 1 from pg_roles where rolname = 'anum_app') \gexec
select format('create role %I login password %L nosuperuser nobypassrls nocreatedb nocreaterole', 'anum_relay', :'relay_password')
where not exists (select 1 from pg_roles where rolname = 'anum_relay') \gexec

grant anum_outbox_relay to anum_relay;
grant anum_maintenance to anum_app;

-- The migration login owns the database, so it owns the public schema
-- (pg_database_owner) and can grant schema usage to the relay and maintenance roles.
-- A non-superuser admin must be a member of anum_migrator to hand over ownership.
select format('alter database %I owner to anum_migrator', current_database()) \gexec

-- Tables and sequences the migration login creates are usable by the application
-- login, and only through RLS (every tenant table has FORCE ROW LEVEL SECURITY).
grant usage on schema public to anum_app;
alter default privileges for role anum_migrator in schema public
  grant select, insert, update, delete on tables to anum_app;
alter default privileges for role anum_migrator in schema public
  grant usage, select on sequences to anum_app;
-- Objects that already exist (re-running on an existing database).
grant select, insert, update, delete on all tables in schema public to anum_app;
grant usage, select on all sequences in schema public to anum_app;
