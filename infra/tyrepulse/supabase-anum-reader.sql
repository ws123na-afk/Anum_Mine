-- =====================================================================================
-- ANUM read-only access to TyrePulse (Supabase).            docs/tyre-planning.md#connecting
-- =====================================================================================
--
-- Run this whole file once in the TyrePulse project's Supabase SQL Editor (it runs as
-- `postgres`). It is idempotent: running it again changes nothing that is already right,
-- and it should be run again after TyrePulse migrations add tables. Then register the
-- reader user and check the result (the last section shows the two calls).
-- To undo everything: infra/tyrepulse/supabase-anum-reader-rollback.sql.
--
-- WHY THIS AND NOT A DATABASE LOGIN
--   TyrePulse's row level security decides the organisation from the signed-in user:
--   app_current_org() = profiles.org_id of auth.uid(), and auth.uid() comes from the JWT
--   PostgREST verifies. A plain Postgres login has no JWT. Giving it one permanently
--   (`alter role ... set request.jwt.claims`) is refused on Supabase (42501), and it would
--   be unsafe anyway: any login may `set_config('request.jwt.claims', ...)` in its own
--   session and so call TyrePulse's SECURITY DEFINER functions as ANY user. So ANUM signs
--   in as a dedicated TyrePulse user through Supabase Auth and reads PostgREST with that
--   user's JWT. Supabase signs the token; nobody can pick another `sub`.
--
-- WHAT THIS FILE ADDS (nothing is disabled, nothing gets BYPASSRLS, no blanket EXECUTE)
--   1. Removes the earlier `anum_reader` login attempt (its grants, settings and the role).
--   2. Schema `anum_integration` (not exposed through the API):
--        readers        - the reader users and the ONE organisation each may read
--        reader_tables  - the tables a reader may read (TyrePulse's five tyre tables)
--      Both are under RLS; `authenticated` can see only its own `readers` row and the
--      table list. Helper functions are SECURITY INVOKER.
--   3. Row level security on every RLS table in `public` (and storage.objects), for the
--      reader only (everyone else is unaffected):
--        RESTRICTIVE insert / update / delete: never for a reader;
--        RESTRICTIVE select: allowed tables only, and only rows of the reader's own
--        organisation (`organisation_id` = readers.organisation_id; NULL rows excluded).
--      Restrictive policies are AND-ed with TyrePulse's own, so they can only narrow.
--   4. A PostgREST pre-request guard: a reader's request must be GET or HEAD on an allowed
--      table. That blocks /rpc (functions could write, and SECURITY DEFINER functions skip
--      RLS), GraphQL, views and writes, and GET requests run in a READ ONLY transaction.
--   5. register_reader(email) and verify_reader(email).
--
-- =====================================================================================

begin;

-- -------------------------------------------------------------------------------------
-- 1. Remove the earlier `anum_reader` login (docs used to ask for it; it is not used).
--    Undoes whatever part of that attempt exists: grants on tables, sequences, functions
--    and schemas, role settings (including a partial request.jwt.claims), then the role.
--    Refuses to touch it if it owns anything.
-- -------------------------------------------------------------------------------------
do $cleanup$
declare
  r record;
begin
  if not exists (select 1 from pg_roles where rolname = 'anum_reader') then
    raise notice 'anum_reader: not present, nothing to clean up';
    return;
  end if;
  if exists (
    select 1 from pg_shdepend d
    where d.refclassid = 'pg_authid'::regclass
      and d.refobjid = (select oid from pg_roles where rolname = 'anum_reader')
      and d.deptype = 'o'
  ) then
    raise exception 'anum_reader owns database objects: move them to another owner before running this file';
  end if;

  for r in
    select c.oid::regclass as obj, c.relkind
    from pg_class c, aclexplode(c.relacl) a
    where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
  loop
    if r.relkind = 'S' then
      execute format('revoke all on sequence %s from anum_reader', r.obj);
    else
      execute format('revoke all on table %s from anum_reader', r.obj);
    end if;
  end loop;
  for r in
    select p.oid::regprocedure as obj
    from pg_proc p, aclexplode(p.proacl) a
    where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
  loop
    execute format('revoke all on routine %s from anum_reader', r.obj);
  end loop;
  for r in
    select n.nspname
    from pg_namespace n, aclexplode(n.nspacl) a
    where a.grantee = (select oid from pg_roles where rolname = 'anum_reader')
  loop
    execute format('revoke all on schema %I from anum_reader', r.nspname);
  end loop;

  begin
    alter role anum_reader reset all;
  exception when others then
    raise notice 'anum_reader: could not reset its settings (%); dropping the role removes them', sqlerrm;
  end;
  begin
    drop role anum_reader;
    raise notice 'anum_reader: grants revoked and role dropped';
  exception when others then
    alter role anum_reader nologin password null;
    raise notice 'anum_reader: could not be dropped (%); it can no longer log in', sqlerrm;
  end;
end
$cleanup$;

-- -------------------------------------------------------------------------------------
-- 2. The reader registry.
-- -------------------------------------------------------------------------------------
create schema if not exists anum_integration;
comment on schema anum_integration is
  'ANUM read-only integration: reader users, their organisation and allowed tables. Not exposed through the API.';
revoke all on schema anum_integration from public;
grant usage on schema anum_integration to anon, authenticated, service_role;

create table if not exists anum_integration.readers (
  user_id         uuid primary key references auth.users (id) on delete cascade,
  organisation_id uuid not null,
  label           text not null default 'ANUM',
  created_at      timestamptz not null default now()
);
comment on table anum_integration.readers is
  'TyrePulse users ANUM signs in as. Each may only read its organisation''s rows of anum_integration.reader_tables and may never write.';

create table if not exists anum_integration.reader_tables (
  table_name text primary key check (table_name ~ '^[a-z_][a-z0-9_]*$')
);
comment on table anum_integration.reader_tables is
  'Tables in public a reader may read (GET only). Add a row and re-run the file to allow another.';
insert into anum_integration.reader_tables (table_name)
values ('vehicle_fleet'), ('tyre_records'), ('tyre_service_events'), ('tpms_readings'), ('suppliers')
on conflict do nothing;

-- RLS on (not forced: the owner, who runs this file, manages the rows).
alter table anum_integration.readers enable row level security;
alter table anum_integration.reader_tables enable row level security;
revoke all on anum_integration.readers, anum_integration.reader_tables from public, anon, authenticated;
grant select on anum_integration.readers, anum_integration.reader_tables to authenticated;

drop policy if exists readers_self on anum_integration.readers;
create policy readers_self on anum_integration.readers
  for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists reader_tables_read on anum_integration.reader_tables;
create policy reader_tables_read on anum_integration.reader_tables
  for select to authenticated using (true);

-- SECURITY INVOKER helpers: they read the caller's own row through the policy above.
create or replace function anum_integration.is_reader()
returns boolean language sql stable security invoker parallel safe
set search_path = ''
as $$
  select exists (select 1 from anum_integration.readers r where r.user_id = (select auth.uid()));
$$;

create or replace function anum_integration.reader_org()
returns uuid language sql stable security invoker parallel safe
set search_path = ''
as $$
  select r.organisation_id from anum_integration.readers r where r.user_id = (select auth.uid());
$$;

revoke all on function anum_integration.is_reader(), anum_integration.reader_org() from public;
grant execute on function anum_integration.is_reader(), anum_integration.reader_org() to authenticated, service_role;

-- -------------------------------------------------------------------------------------
-- 3. Reader policies on every RLS table in public, and on storage.objects.
--    Only the reader is affected: for everyone else `not is_reader()` is true.
-- -------------------------------------------------------------------------------------
create or replace function anum_integration.apply_reader_policies()
returns table (table_name text, outcome text)
language plpgsql security invoker
set search_path = ''
as $$
declare
  t record;
  v_select text;
  v_deny constant text := 'not (select anum_integration.is_reader())';
begin
  for t in
    select n.nspname as schema_name, c.relname,
           exists (select 1 from anum_integration.reader_tables a
                   where n.nspname = 'public' and a.table_name = c.relname) as allowed,
           exists (select 1 from pg_attribute at
                   where at.attrelid = c.oid and at.attname = 'organisation_id'
                     and not at.attisdropped and at.atttypid = 'uuid'::regtype) as has_org
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r', 'p') and c.relrowsecurity
      and (n.nspname = 'public' or (n.nspname = 'storage' and c.relname = 'objects'))
    order by n.nspname, c.relname
  loop
    if t.allowed and t.has_org then
      v_select := v_deny || ' or organisation_id = (select anum_integration.reader_org())';
    else
      v_select := v_deny;
    end if;
    begin
      execute format('drop policy if exists anum_reader_select_scope on %I.%I', t.schema_name, t.relname);
      execute format('drop policy if exists anum_reader_no_insert on %I.%I', t.schema_name, t.relname);
      execute format('drop policy if exists anum_reader_no_update on %I.%I', t.schema_name, t.relname);
      execute format('drop policy if exists anum_reader_no_delete on %I.%I', t.schema_name, t.relname);
      execute format('create policy anum_reader_select_scope on %I.%I as restrictive for select to authenticated using (%s)',
                     t.schema_name, t.relname, v_select);
      execute format('create policy anum_reader_no_insert on %I.%I as restrictive for insert to authenticated with check (%s)',
                     t.schema_name, t.relname, v_deny);
      execute format('create policy anum_reader_no_update on %I.%I as restrictive for update to authenticated using (%s) with check (%s)',
                     t.schema_name, t.relname, v_deny, v_deny);
      execute format('create policy anum_reader_no_delete on %I.%I as restrictive for delete to authenticated using (%s)',
                     t.schema_name, t.relname, v_deny);
      table_name := t.schema_name || '.' || t.relname;
      outcome := case when t.allowed and t.has_org then 'read: own organisation only; writes denied'
                      when t.allowed then 'NOT READABLE: allowed but has no uuid organisation_id; writes denied'
                      else 'no reads; writes denied' end;
      return next;
    exception when insufficient_privilege then
      table_name := t.schema_name || '.' || t.relname;
      outcome := 'SKIPPED: not owner (' || sqlerrm || ')';
      return next;
    end;
  end loop;
end
$$;
revoke all on function anum_integration.apply_reader_policies() from public, anon, authenticated, service_role;

select * from anum_integration.apply_reader_policies() where outcome not like 'no reads%';

-- -------------------------------------------------------------------------------------
-- 4. PostgREST pre-request guard: a reader may only GET / HEAD an allowed table.
--    PostgREST runs it before every API request; it returns at once for anyone else.
-- -------------------------------------------------------------------------------------
create or replace function anum_integration.pre_request()
returns void language plpgsql stable security invoker
set search_path = ''
as $$
declare
  v_method text := upper(coalesce(current_setting('request.method', true), ''));
  v_path   text := coalesce(current_setting('request.path', true), '');
  v_table  text;
begin
  -- Two steps: anon and service requests carry no user and must not touch the registry.
  if (select auth.uid()) is null then
    return;
  end if;
  if not anum_integration.is_reader() then
    return;
  end if;
  -- PostgREST passes the path under the API root: '/tyre_records' (or 'tyre_records').
  v_table := substring(v_path from '^/?([a-z_][a-z0-9_]*)/?$');
  if v_method not in ('GET', 'HEAD')
     or v_table is null
     or not exists (select 1 from anum_integration.reader_tables a where a.table_name = v_table) then
    raise sqlstate 'PT403' using
      message = 'ANUM reader: read-only access to the allowed tables only',
      hint = 'This user may only GET the tables in anum_integration.reader_tables.';
  end if;
end
$$;
revoke all on function anum_integration.pre_request() from public;
grant execute on function anum_integration.pre_request() to anon, authenticated, service_role;

do $guard$
declare
  v_current text;
begin
  select split_part(cfg, '=', 2) into v_current
  from pg_db_role_setting s
  join pg_roles r on r.oid = s.setrole, unnest(s.setconfig) cfg
  where r.rolname = 'authenticator' and s.setdatabase = 0 and cfg like 'pgrst.db_pre_request=%';

  if v_current = 'anum_integration.pre_request' then
    raise notice 'pre-request guard: already installed';
  elsif v_current is not null and v_current <> '' then
    raise notice 'pre-request guard: NOT installed, because PostgREST already uses %. Add "perform anum_integration.pre_request();" at the start of that function.', v_current;
  elsif not exists (select 1 from pg_roles where rolname = 'authenticator') then
    raise notice 'pre-request guard: no authenticator role (not a Supabase project?)';
  else
    begin
      alter role authenticator set pgrst.db_pre_request = 'anum_integration.pre_request';
      notify pgrst, 'reload config';
      raise notice 'pre-request guard: installed';
    exception when others then
      raise notice 'pre-request guard: NOT installed (%). See docs/tyre-planning.md (Supabase dashboard steps).', sqlerrm;
    end;
  end if;
end
$guard$;

-- -------------------------------------------------------------------------------------
-- 5. Register a reader, and check it.
-- -------------------------------------------------------------------------------------
create or replace function anum_integration.register_reader(p_email text, p_organisation_id uuid default null)
returns table (user_id uuid, organisation_id uuid, role text)
language plpgsql security invoker
set search_path = ''
as $$
declare
  v_user    uuid;
  v_profile jsonb;
  v_org     uuid;
begin
  select u.id into v_user from auth.users u where lower(u.email) = lower(btrim(p_email));
  if v_user is null then
    raise exception 'No Supabase user has the e-mail %: create the TyrePulse user first', p_email;
  end if;
  select to_jsonb(p) into v_profile from public.profiles p where p.id = v_user;
  if v_profile is null then
    raise exception 'User % has no TyrePulse profile: create it through TyrePulse''s user management', p_email;
  end if;
  v_org := coalesce(nullif(v_profile ->> 'org_id', ''), nullif(v_profile ->> 'organisation_id', ''))::uuid;
  if v_org is null then
    raise exception 'User % belongs to no organisation: set its organisation in TyrePulse first', p_email;
  end if;
  if p_organisation_id is not null and p_organisation_id <> v_org then
    raise exception 'User % belongs to organisation %, not %: use a user of the organisation ANUM may read', p_email, v_org, p_organisation_id;
  end if;
  if coalesce((v_profile ->> 'is_super_admin')::boolean, false) or v_profile ->> 'role' = 'Admin' then
    raise exception 'User % is an administrator: use a dedicated user with a read-only role (for example Reporter)', p_email;
  end if;

  insert into anum_integration.readers as r (user_id, organisation_id)
  values (v_user, v_org)
  on conflict on constraint readers_pkey do update set organisation_id = excluded.organisation_id;

  user_id := v_user; organisation_id := v_org; role := v_profile ->> 'role';
  return next;
end
$$;
revoke all on function anum_integration.register_reader(text, uuid) from public, anon, authenticated, service_role;

-- Impersonates the reader in sub-transactions that always roll back: reads, other
-- organisations, a table it may not read, insert / update / delete, and the API guard.
create or replace function anum_integration.verify_reader(p_email text)
returns table (check_name text, status text, detail text)
language plpgsql security invoker
set search_path = ''
as $$
declare
  v_user  uuid;
  v_org   uuid;
  v_claims text;
  t       record;
  v_n     bigint;
  v_m     bigint;
  v_state text;
  v_guard text;
  v_other text;
begin
  select r.user_id, r.organisation_id into v_user, v_org
  from anum_integration.readers r join auth.users u on u.id = r.user_id
  where lower(u.email) = lower(btrim(p_email));
  if v_user is null then
    check_name := 'registered'; status := 'fail';
    detail := 'Not registered: select * from anum_integration.register_reader(''' || p_email || ''');';
    return next; return;
  end if;
  check_name := 'registered'; status := 'pass'; detail := 'organisation ' || v_org; return next;
  v_claims := json_build_object('sub', v_user, 'role', 'authenticated')::text;

  for t in
    select a.table_name from anum_integration.reader_tables a
    where to_regclass('public.' || quote_ident(a.table_name)) is not null
    order by a.table_name
  loop
    -- Reads: rows of the reader's organisation, and none of any other.
    v_n := null; v_m := null; v_state := null;
    begin
      perform set_config('role', 'authenticated', true);
      perform set_config('request.jwt.claims', v_claims, true);
      execute format('select count(*), count(*) filter (where organisation_id is distinct from %L::uuid) from public.%I',
                     v_org, t.table_name) into v_n, v_m;
      raise exception using errcode = 'P0001', message = 'anum_verify_rollback';
    exception when others then
      if sqlerrm <> 'anum_verify_rollback' then v_state := sqlstate || ' ' || sqlerrm; end if;
    end;
    check_name := 'read ' || t.table_name;
    status := case when v_state is not null then 'fail' when v_m = 0 and v_n > 0 then 'pass' when v_m = 0 then 'warn' else 'fail' end;
    detail := coalesce(v_state, v_n || ' rows visible, ' || v_m || ' of another organisation'
              || case when v_n = 0 then ' (none visible: check the user''s organisation, country and site scope in TyrePulse)' else '' end);
    return next;

    -- Insert must be refused by policy (42501).
    v_state := null;
    begin
      perform set_config('role', 'authenticated', true);
      perform set_config('request.jwt.claims', v_claims, true);
      execute format('insert into public.%I default values', t.table_name);
      raise exception using errcode = 'P0001', message = 'anum_verify_inserted';
    exception when others then
      v_state := case when sqlerrm = 'anum_verify_inserted' then 'inserted' else sqlstate end;
    end;
    check_name := 'insert ' || t.table_name;
    status := case when v_state = '42501' then 'pass' when v_state = 'inserted' then 'fail' else 'warn' end;
    detail := case when v_state = '42501' then 'refused by row level security (rolled back)'
                   when v_state = 'inserted' then 'A ROW WAS ACCEPTED (rolled back): the write policy is missing'
                   else 'refused with ' || v_state || ' before the policy check (rolled back)' end;
    return next;

    -- Update and delete must touch nothing.
    foreach v_other in array array['update', 'delete'] loop
      v_n := null; v_state := null;
      begin
        perform set_config('role', 'authenticated', true);
        perform set_config('request.jwt.claims', v_claims, true);
        if v_other = 'update' then
          execute format('update public.%I set organisation_id = organisation_id', t.table_name);
        else
          execute format('delete from public.%I', t.table_name);
        end if;
        get diagnostics v_n = row_count;
        raise exception using errcode = 'P0001', message = 'anum_verify_rollback';
      exception when others then
        if sqlerrm <> 'anum_verify_rollback' then v_state := sqlstate; end if;
      end;
      check_name := v_other || ' ' || t.table_name;
      status := case when v_state = '42501' or v_n = 0 then 'pass' when v_state is not null then 'warn' else 'fail' end;
      detail := case when v_state is not null then 'refused with ' || v_state || ' (rolled back)'
                     when v_n = 0 then '0 rows changed'
                     else v_n || ' ROWS WOULD CHANGE (rolled back): the write policy is missing' end;
      return next;
    end loop;
  end loop;

  -- A table outside the allow-list (profiles) shows nothing.
  if to_regclass('public.profiles') is not null then
    v_n := null; v_state := null;
    begin
      perform set_config('role', 'authenticated', true);
      perform set_config('request.jwt.claims', v_claims, true);
      execute 'select count(*) from public.profiles' into v_n;
      raise exception using errcode = 'P0001', message = 'anum_verify_rollback';
    exception when others then
      if sqlerrm <> 'anum_verify_rollback' then v_state := sqlstate; end if;
    end;
    check_name := 'read profiles (not allowed)';
    status := case when v_n = 0 or v_state = '42501' then 'pass' else 'fail' end;
    detail := coalesce('refused with ' || v_state, v_n || ' rows visible');
    return next;
  end if;

  -- The API guard: GET on an allowed table passes, anything else is refused.
  select split_part(cfg, '=', 2) into v_guard
  from pg_db_role_setting s join pg_roles r on r.oid = s.setrole, unnest(s.setconfig) cfg
  where r.rolname = 'authenticator' and cfg like 'pgrst.db_pre_request=%';
  check_name := 'api guard installed';
  status := case when v_guard = 'anum_integration.pre_request' then 'pass' else 'fail' end;
  detail := coalesce('PostgREST pre-request = ' || v_guard, 'not set: /rpc and views are not blocked for the reader');
  return next;
  foreach v_other in array array['GET /tyre_records', 'POST /tyre_records', 'PATCH /tyre_records', 'DELETE /tyre_records',
                                 'POST /rpc/anything', 'GET /rpc/anything', 'GET /profiles', 'POST /graphql'] loop
    v_state := 'allowed';
    begin
      perform set_config('role', 'authenticated', true);
      perform set_config('request.jwt.claims', v_claims, true);
      perform set_config('request.method', split_part(v_other, ' ', 1), true);
      perform set_config('request.path', split_part(v_other, ' ', 2), true);
      perform anum_integration.pre_request();
      raise exception using errcode = 'P0001', message = 'anum_verify_rollback';
    exception when others then
      if sqlerrm <> 'anum_verify_rollback' then v_state := 'refused'; end if;
    end;
    check_name := 'api ' || v_other;
    status := case when (v_other = 'GET /tyre_records') = (v_state = 'allowed') then 'pass' else 'fail' end;
    detail := v_state;
    return next;
  end loop;

  -- Tables the policies do not cover: public tables without RLS, and RLS tables added
  -- since this file last ran.
  select string_agg(c.relname, ', ' order by c.relname) into v_other
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity;
  check_name := 'public tables without row level security';
  status := case when v_other is null then 'pass' else 'warn' end;
  detail := coalesce(v_other || ': any signed-in user (the reader too) is limited only by grants there; the API guard still blocks the reader', 'none');
  return next;
  select string_agg(c.relname, ', ' order by c.relname) into v_other
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind in ('r', 'p') and c.relrowsecurity
    and not exists (select 1 from pg_policy p where p.polrelid = c.oid and p.polname = 'anum_reader_no_insert');
  check_name := 'reader policies on every RLS table';
  status := case when v_other is null then 'pass' else 'fail' end;
  detail := coalesce('missing on ' || v_other || ': run this file again', 'all covered');
  return next;

  check_name := 'old anum_reader login removed';
  status := case when exists (select 1 from pg_roles where rolname = 'anum_reader' and rolcanlogin) then 'fail' else 'pass' end;
  detail := case when status = 'pass' then 'no anum_reader login' else 'anum_reader can still log in' end;
  return next;
end
$$;
revoke all on function anum_integration.verify_reader(text) from public, anon, authenticated, service_role;

commit;

-- =====================================================================================
-- NEXT (edit the e-mail, run each line on its own):
--
--   select * from anum_integration.register_reader('anum-reader@your-company.example');
--   select * from anum_integration.verify_reader('anum-reader@your-company.example');
--
-- Every row of verify_reader should say pass. `warn` on an insert means a trigger or a
-- constraint refused the row before the policy did (still nothing written); `warn` on a
-- read means the user sees no rows (check its organisation, countries and sites).
--
-- Read-only checks you can run any time:
--   select * from anum_integration.readers;
--   select * from anum_integration.reader_tables;
--   select schemaname, tablename, policyname, permissive, cmd from pg_policies
--    where policyname like 'anum_reader_%' and tablename in (select table_name from anum_integration.reader_tables);
--   select rolname from pg_roles where rolname = 'anum_reader';   -- expect no row
-- =====================================================================================
