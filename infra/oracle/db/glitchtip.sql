-- GlitchTip's own login and database (the optional `monitoring` profile,
-- docs/monitoring-oracle.md). Run by infra/oracle/db-init.sh as the superuser, connected
-- to the `postgres` database, only when GLITCHTIP_DB_PASSWORD is set in .env.
--
-- glitchtip  Owns only the `glitchtip` database (GlitchTip's migrations create its
--            tables and partitions there). Not a member of any ANUM role, no BYPASSRLS,
--            and no CONNECT on `anum` or `keycloak` (both revoke it from PUBLIC).
--            Not in the nightly backup: error events are diagnostics, kept 30 days.

\set ON_ERROR_STOP on

select format('create role glitchtip login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'glitchtip_password')
where not exists (select 1 from pg_roles where rolname = 'glitchtip') \gexec
select format('alter role glitchtip with login password %L nosuperuser nobypassrls nocreatedb nocreaterole', :'glitchtip_password') \gexec

select 'create database glitchtip owner glitchtip'
where not exists (select 1 from pg_database where datname = 'glitchtip') \gexec
revoke connect, temporary on database glitchtip from public;
grant connect, temporary on database glitchtip to glitchtip;
