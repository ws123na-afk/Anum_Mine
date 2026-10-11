-- Runs after infra/helm/bootstrap-database.sql in the `anum` database
-- (infra/oracle/db-init.sh, as the superuser).
--
-- bootstrap-database.sql keeps an existing role's password. On this path .env is the
-- source of truth, so the passwords are set from it on every run, and the attributes
-- the bootstrap promises (no superuser, no BYPASSRLS, no CREATEDB/CREATEROLE) are
-- re-asserted.

\set ON_ERROR_STOP on

select format('alter role anum_migrator with login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'migrator_password') \gexec
select format('alter role anum_app with login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'app_password') \gexec
select format('alter role anum_relay with login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'relay_password') \gexec

-- Only ANUM's logins may connect to ANUM's database (Keycloak's login may not). The
-- owner (anum_migrator) connects as owner.
revoke connect on database anum from public;
grant connect on database anum to anum_app, anum_relay, anum_backup;
