"""Event stream index and discovery for the event history's retention (expand only).

docs/realtime.md#event-history. Reliability audit, November 2026.

* ``ix_domain_events_scope_created`` on ``domain_events (tenant_id, workspace_id, created_at, id)``. An open event
  stream reads ``where tenant_id = :t and workspace_id = :w and (created_at, id) > (:created_at, :id) order by
  created_at, id limit :n`` once a second (``SqlAlchemyRepository.list_events_after``). The only index on those
  columns had ``type`` before ``created_at``, so each poll fetched and sorted the workspace's whole history. The
  retention delete below reads the same index. Built ``CONCURRENTLY`` (outside the migration transaction), like
  ``0017_tasks_scope_created``, so events keep being written during the build.
* Discovery for the daily retention job (``python -m anum_api.voice_retention``, step ``events``), migration 0011's
  pattern, never BYPASSRLS: ``anum_maintenance`` may read ``tenant_id`` and ``workspace_id`` (nothing else) of events
  older than 7 days, the shortest period ``ANUM_EVENT_RETENTION_DAYS`` accepts. It cannot read a type, a subject or
  a payload and it cannot write. Each scope is then purged as the application role inside its own tenant and
  workspace context (``anum_api.event_retention``). ``audit_records`` are another table and are not touched.

Chained after ``0161_finance_bank_loans``.

Expand only: one index, one column grant and one policy. The previous release ignores all three. Rollback:
``alembic downgrade 0161_finance_bank_loans`` drops them; no data is lost.
"""
from alembic import op

revision = "0162_event_stream_retention"
down_revision = "0161_finance_bank_loans"
branch_labels = None
depends_on = None

# The shortest period the setting accepts (anum_api/event_retention.py keeps the same number).
MIN_RETENTION_DAYS = 7


def upgrade() -> None:
    # The index first, outside a transaction: a concurrent build that fails (a cancelled deploy, a lock
    # timeout) has changed nothing else, and a second run goes through. PostgreSQL leaves a failed concurrent
    # build behind as an INVALID index that "if not exists" would keep, so one is dropped before building.
    with op.get_context().autocommit_block():
        op.execute(
            """
            do $$
            begin
              if exists (select 1 from pg_index i join pg_class c on c.oid = i.indexrelid
                         where c.relname = 'ix_domain_events_scope_created' and not i.indisvalid) then
                drop index ix_domain_events_scope_created;
              end if;
            end
            $$
            """
        )
        op.execute(
            "create index concurrently if not exists ix_domain_events_scope_created "
            "on domain_events (tenant_id, workspace_id, created_at, id)"
        )
    # Discovery only: scope columns of rows the purge may act on, never content, no write.
    op.execute("grant select (tenant_id, workspace_id) on domain_events to anum_maintenance")
    op.execute("drop policy if exists maintenance_event_retention on domain_events")
    op.execute(
        f"""
        create policy maintenance_event_retention on domain_events
        for select to anum_maintenance
        using (created_at < now() - interval '{MIN_RETENTION_DAYS} days')
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_domain_events_scope_created")
    op.execute("drop policy if exists maintenance_event_retention on domain_events")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, workspace_id) on domain_events from anum_maintenance;
          end if;
        end
        $$
        """
    )
