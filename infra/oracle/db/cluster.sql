-- Cluster-level roles for ANUM on one VM (run by infra/oracle/db-init.sh as the
-- superuser, connected to the `postgres` database; psql variables come from .env).
--
-- keycloak     Keycloak's own login. Owns only the `keycloak` database; it is not a
--              member of any ANUM role and cannot connect to `anum`.
-- anum_backup  The backup login (docs/deployment.md#database-roles): BYPASSRLS and
--              pg_read_all_data, because a role subject to RLS would produce a partial
--              backup. Used only by the backup job; no application service receives it.

\set ON_ERROR_STOP on

select format('create role keycloak login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'keycloak_password')
where not exists (select 1 from pg_roles where rolname = 'keycloak') \gexec
select format('alter role keycloak with login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'keycloak_password') \gexec

select 'create database keycloak owner keycloak'
where not exists (select 1 from pg_database where datname = 'keycloak') \gexec
revoke connect, temporary on database keycloak from public;
grant connect, temporary on database keycloak to keycloak;

select format('create role anum_backup login password %L nosuperuser bypassrls nocreatedb nocreaterole', :'backup_password')
where not exists (select 1 from pg_roles where rolname = 'anum_backup') \gexec
select format('alter role anum_backup with login password %L nosuperuser bypassrls nocreatedb nocreaterole', :'backup_password') \gexec
grant pg_read_all_data to anum_backup;
-- The nightly job also dumps Keycloak's database (infra/oracle/backup.sh).
grant connect on database keycloak to anum_backup;
