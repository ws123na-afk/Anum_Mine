-- =====================================================================================
-- Undo infra/tyrepulse/supabase-anum-reader.sql.            docs/tyre-planning.md#connecting
-- =====================================================================================
-- Run in the TyrePulse project's Supabase SQL Editor. Idempotent. Removes the reader
-- policies, the PostgREST pre-request guard (only if it is ANUM's), the anum_integration
-- schema and, if one is still there, the old `anum_reader` login attempt. TyrePulse's own
-- policies, roles and data are not touched. The reader *user* (Authentication > Users)
-- stays: delete or ban it in the dashboard if ANUM should never sign in again.
-- =====================================================================================

begin;

do $rollback$
declare
  t record;
  v_current text;
begin
  for t in
    select n.nspname, c.relname, p.polname
    from pg_policy p
    join pg_class c on c.oid = p.polrelid
    join pg_namespace n on n.oid = c.relnamespace
    where p.polname in ('anum_reader_select_scope', 'anum_reader_no_insert', 'anum_reader_no_update', 'anum_reader_no_delete')
  loop
    execute format('drop policy if exists %I on %I.%I', t.polname, t.nspname, t.relname);
  end loop;

  select split_part(cfg, '=', 2) into v_current
  from pg_db_role_setting s join pg_roles r on r.oid = s.setrole, unnest(s.setconfig) cfg
  where r.rolname = 'authenticator' and s.setdatabase = 0 and cfg like 'pgrst.db_pre_request=%';
  if v_current = 'anum_integration.pre_request' then
    begin
      alter role authenticator reset pgrst.db_pre_request;
      notify pgrst, 'reload config';
    exception when others then
      raise notice 'pre-request guard: could not be removed (%); remove it as described in docs/tyre-planning.md', sqlerrm;
    end;
  end if;

  -- The earlier login attempt, if it still exists (see section 1 of the main file).
  if exists (select 1 from pg_roles where rolname = 'anum_reader') then
    for t in
      select c.oid::regclass::text as obj, c.relkind::text as kind
      from pg_class c, aclexplode(c.relacl) a
      where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
    loop
      execute format('revoke all on %s %s from anum_reader', case when t.kind = 'S' then 'sequence' else 'table' end, t.obj);
    end loop;
    for t in
      select p.oid::regprocedure::text as obj
      from pg_proc p, aclexplode(p.proacl) a
      where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
    loop
      execute format('revoke all on routine %s from anum_reader', t.obj);
    end loop;
    for t in
      select n.nspname as obj
      from pg_namespace n, aclexplode(n.nspacl) a
      where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
    loop
      execute format('revoke all on schema %I from anum_reader', t.obj);
    end loop;
    begin
      drop role anum_reader;
    exception when others then
      alter role anum_reader nologin password null;
      raise notice 'anum_reader: could not be dropped (%); it can no longer log in', sqlerrm;
    end;
  end if;
end
$rollback$;

drop schema if exists anum_integration cascade;

commit;

-- Check (each should return no rows):
--   select policyname, tablename from pg_policies where policyname like 'anum_reader_%';
--   select nspname from pg_namespace where nspname = 'anum_integration';
--   select rolname from pg_roles where rolname = 'anum_reader' and rolcanlogin;
