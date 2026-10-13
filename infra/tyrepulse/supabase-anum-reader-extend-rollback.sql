-- =====================================================================================
-- Undo infra/tyrepulse/supabase-anum-reader-extend.sql.     docs/tyre-planning.md#connecting
-- =====================================================================================
-- Run in the TyrePulse project's Supabase SQL Editor. Idempotent. Takes the operational
-- tables off anum_integration.reader_tables again (the five tyre tables of the first file
-- stay), removes the two helper functions and re-applies the reader policies, so the
-- reader can no longer read those tables (its reads there are denied, writes stay denied).
-- To remove ANUM's access completely run infra/tyrepulse/supabase-anum-reader-rollback.sql.
-- =====================================================================================

begin;

do $rollback$
begin
  if to_regclass('anum_integration.reader_tables') is null then
    raise notice 'anum_integration is not installed: nothing to undo';
    return;
  end if;
  delete from anum_integration.reader_tables
  where table_name not in ('vehicle_fleet', 'tyre_records', 'tyre_service_events', 'tpms_readings', 'suppliers');
  drop function if exists anum_integration.add_reader_table(text);
  drop function if exists anum_integration.is_excluded_table(text);
  if to_regprocedure('anum_integration.apply_reader_policies()') is not null then
    perform anum_integration.apply_reader_policies();
  end if;
  raise notice 'operational tables removed from the reader tables';
end
$rollback$;

commit;

-- Check (expect the five tyre tables only):
--   select * from anum_integration.reader_tables order by 1;
