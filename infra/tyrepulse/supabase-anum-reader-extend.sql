-- =====================================================================================
-- ANUM read-only access to TyrePulse: the operational tables.   docs/tyre-planning.md#connecting
-- =====================================================================================
--
-- Run this whole file in the TyrePulse project's Supabase SQL Editor (it runs as
-- `postgres`) AFTER infra/tyrepulse/supabase-anum-reader.sql. It is additive and
-- idempotent: run it again whenever you like. Undo it with
-- infra/tyrepulse/supabase-anum-reader-extend-rollback.sql.
--
-- WHAT IT DOES
--   Adds TyrePulse's operational tables (tyre readings in `inspections`, schedules,
--   workshop checklists, work orders and their line items, parts issued, meter readings,
--   sites, washes, accidents, workshop status, tyre catalogue, rotations, retreads...) to
--   anum_integration.reader_tables, then re-applies the reader policies from the first
--   file to every RLS table. For each added table the registered reader may now:
--     * GET it through the API (the pre-request guard reads reader_tables), and
--     * see only rows of its own organisation (`organisation_id` = readers.organisation_id;
--       rows without an organisation stay hidden), on top of TyrePulse's own policies.
--   Writes stay refused on every table (RESTRICTIVE insert / update / delete policies).
--
-- SAFETY (the same rules as the first file)
--   No BYPASSRLS, no disabled RLS, no blanket grants, no SECURITY DEFINER, no new roles.
--   A table is added only if it exists in `public`, has row level security on and has a
--   uuid `organisation_id` column; otherwise it is skipped with a NOTICE (ksa_kms and
--   uae_kms have no organisation_id, so they are skipped: a reader could not be pinned to
--   one organisation there).
--   Never added, whatever the list below says: people's accounts and security (profiles,
--   auth, logins, sessions, devices, API keys, audit and access logs, notifications, AI
--   and cron or system tables, passwords) and personal data (drivers and driver_*).
--   ANUM itself also never selects columns about people (names of staff and drivers,
--   e-mails, phones, signatures, photos, GPS); see docs/tyre-planning.md#what-anum-reads.
--
-- =====================================================================================

begin;

do $check$
begin
  if to_regclass('anum_integration.reader_tables') is null
     or to_regprocedure('anum_integration.apply_reader_policies()') is null
     or to_regprocedure('anum_integration.pre_request()') is null then
    raise exception 'Run infra/tyrepulse/supabase-anum-reader.sql first (anum_integration is missing).';
  end if;
end
$check$;

-- Tables that are never readable by a reader, even if listed below or added by hand.
create or replace function anum_integration.is_excluded_table(p_table text)
returns boolean language sql immutable security invoker parallel safe
set search_path = ''
as $$
  select p_table ~* '^(profiles|users?|auth.*|login.*|.*_logins?|sessions?|.*_sessions?|devices?|.*_devices?|api_keys?.*|.*audit.*|access_.*|.*_access_log.*|notifications?|.*_notifications?|ai_.*|cron_.*|system_.*|password.*|.*_passwords?|console_.*|user_.*|.*_users?|drivers?|driver_.*|.*_drivers?|invitations?|.*_tokens?|secrets?|.*_secrets?)$';
$$;
revoke all on function anum_integration.is_excluded_table(text) from public, anon, authenticated, service_role;

-- Adds one table if it is safe to pin to one organisation; says what happened.
create or replace function anum_integration.add_reader_table(p_table text)
returns text language plpgsql security invoker
set search_path = ''
as $$
declare
  v_rel oid;
  v_rls boolean;
begin
  if p_table !~ '^[a-z_][a-z0-9_]*$' then
    return 'SKIPPED: not a plain table name';
  end if;
  if anum_integration.is_excluded_table(p_table) then
    return 'SKIPPED: accounts, security or personal data are never readable';
  end if;
  select c.oid, c.relrowsecurity into v_rel, v_rls
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname = p_table and c.relkind in ('r', 'p');
  if v_rel is null then
    return 'SKIPPED: no such table in public';
  end if;
  if not v_rls then
    return 'SKIPPED: row level security is off, so the reader could not be pinned to its organisation';
  end if;
  if not exists (select 1 from pg_attribute a
                 where a.attrelid = v_rel and a.attname = 'organisation_id'
                   and not a.attisdropped and a.atttypid = 'uuid'::regtype) then
    return 'SKIPPED: no uuid organisation_id column, so the reader could not be pinned to its organisation';
  end if;
  insert into anum_integration.reader_tables (table_name) values (p_table) on conflict do nothing;
  return 'added: own organisation only, GET only, no writes';
end
$$;
revoke all on function anum_integration.add_reader_table(text) from public, anon, authenticated, service_role;

-- Tables already listed that are now excluded or unsafe are taken off the list again.
do $prune$
declare
  t record;
begin
  for t in select a.table_name from anum_integration.reader_tables a loop
    if anum_integration.is_excluded_table(t.table_name) then
      delete from anum_integration.reader_tables where table_name = t.table_name;
      raise notice '%: removed from the reader tables (accounts, security or personal data)', t.table_name;
    end if;
  end loop;
end
$prune$;

do $extend$
declare
  v_table text;
  v_outcome text;
begin
  foreach v_table in array array[
    -- tyres
    'tyre_records', 'inspections', 'inspection_schedules', 'tyre_status_marks', 'tyre_spec_catalog',
    'tyre_rotations', 'tyre_tread_readings', 'tyre_disposals', 'retread_jobs', 'tpms_readings',
    'tyre_service_events', 'suppliers',
    -- vehicles and meters
    'vehicle_fleet', 'odometer_logs', 'engine_hours_logs', 'ksa_kms', 'uae_kms', 'cpk_asset_meter', 'sites',
    -- workshop
    'checklist_submissions', 'checklist_templates', 'work_orders', 'work_order_line_items', 'parts_consumption',
    'workshop_status_records', 'workshop_status_events', 'wash_records', 'accidents', 'accident_damage_assessments',
    'production_logs'
  ] loop
    v_outcome := anum_integration.add_reader_table(v_table);
    raise notice '%: %', v_table, v_outcome;
  end loop;
end
$extend$;

-- Re-apply the reader policies (select scope, no insert / update / delete) to every RLS table.
select * from anum_integration.apply_reader_policies()
where table_name in (select 'public.' || a.table_name from anum_integration.reader_tables a);

commit;

-- =====================================================================================
-- NEXT: check the reader again (every row should say pass; `warn` on a read means the
-- reader sees no rows in that table, for example an empty table):
--
--   select * from anum_integration.verify_reader('anum-reader@your-company.example');
--
-- Then in ANUM: Tyre planning > Connection > Discover tables, Use the TyrePulse preset,
-- Save mapping, Sync now.
--
-- Read-only checks:
--   select * from anum_integration.reader_tables order by 1;
--   select tablename, policyname, permissive, cmd from pg_policies
--    where policyname like 'anum_reader_%' and tablename in (select table_name from anum_integration.reader_tables)
--    order by 1, 2;
-- =====================================================================================
