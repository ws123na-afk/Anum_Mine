"""Calendar: repeating events, all-day events and reminders (docs/calendar.md#recurrence).

``calendar_events`` gains:

* ``all_day`` and ``timezone``: whole-day events and the IANA zone a series expands in.
* ``recurrence`` (the RFC 5545 subset as JSON), ``recurrence_until`` (when the last occurrence
  ends; null = no end, used to find series that reach a window) and ``skipped`` (original
  starts of occurrences deleted on their own).
* ``series_id`` and ``recurrence_start``: a child row that replaces one occurrence of its series
  (deleted with the series).
* ``reminders``: minutes before the start a reminder is sent.

``calendar_reminder_sends`` (workspace table) records every reminder sent: one row per event,
occurrence, reminder and person, claimed with ``insert ... on conflict do nothing`` so a pass
that runs twice, or after a restart, never sends one twice. Forced RLS and the lifecycle purge
policy like its neighbours.

``anum_maintenance`` discovers events that may have a reminder due (ids, times, the rule, the
reminders; never a title, note, place or attendee) and the sends already recorded; each
reminder is then sent as the application role inside the event's tenant and workspace context.

Expand only: nullable or defaulted columns and one new table; the previous release ignores
them and keeps inserting events (``all_day`` false, no rule, no reminders). Rollback:
``alembic downgrade 0165_autonomy_undo_claim`` drops them (series rows become one-off events).

Chained after ``0165_autonomy_undo_claim``; renumber at merge if another migration lands first.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0166_calendar_recurrence"
down_revision = "0165_autonomy_undo_claim"
branch_labels = None
depends_on = None

TABLE = "calendar_reminder_sends"
TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TENANT = "nullif(current_setting('anum.tenant_id', true), '')"
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
MAINTENANCE_EVENT_COLUMNS = (
    "tenant_id, workspace_id, id, status, starts_at, ends_at, all_day, timezone, recurrence, recurrence_until, skipped, reminders, "
    "series_id, recurrence_start"
)


def _enable_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def _purge_policy(table: str) -> None:
    """Organisation or workspace deletion may remove these rows (0079's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = {TENANT}
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("calendar_events", sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column("calendar_events", sa.Column("recurrence", postgresql.JSONB(), nullable=True))
    op.add_column("calendar_events", sa.Column("recurrence_until", TS, nullable=True))
    op.add_column("calendar_events", sa.Column("skipped", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("calendar_events", sa.Column("reminders", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("calendar_events", sa.Column("series_id", sa.String(80), nullable=True))
    op.add_column("calendar_events", sa.Column("recurrence_start", TS, nullable=True))
    op.create_foreign_key("fk_calendar_events_series", "calendar_events", "calendar_events", ["series_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_calendar_events_series", "calendar_events", ["tenant_id", "workspace_id", "series_id"], postgresql_where=sa.text("series_id is not null"))
    op.create_index("ix_calendar_events_series_run", "calendar_events", ["tenant_id", "workspace_id", "recurrence_until"], postgresql_where=sa.text("recurrence is not null"))

    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("occurrence_start", TS, nullable=False),
        sa.Column("minutes_before", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("sent_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "event_id", "occurrence_start", "minutes_before", "user_id", name=f"pk_{TABLE}"),
        sa.ForeignKeyConstraint(["event_id"], ["calendar_events.id"], name=f"fk_{TABLE}_event", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.CheckConstraint("minutes_before between 0 and 40320", name=f"ck_{TABLE}_minutes"),
    )
    _enable_rls(TABLE)
    _purge_policy(TABLE)

    # Discovery of due reminders: ids, times and the rule only, scheduled events with reminders.
    op.execute(f"grant select ({MAINTENANCE_EVENT_COLUMNS}) on calendar_events to anum_maintenance")
    op.execute(
        """
        create policy maintenance_calendar_reminders_due on calendar_events
        for select to anum_maintenance
        using (status = 'scheduled' and jsonb_array_length(reminders) > 0)
        """
    )
    op.execute(f"grant select (tenant_id, workspace_id, event_id, occurrence_start, minutes_before, user_id) on {TABLE} to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_{TABLE} on {TABLE}
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute(f"drop policy if exists maintenance_{TABLE} on {TABLE}")
    op.execute("drop policy if exists maintenance_calendar_reminders_due on calendar_events")
    op.execute(
        f"""
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select ({MAINTENANCE_EVENT_COLUMNS}) on calendar_events from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_table(TABLE)
    op.drop_index("ix_calendar_events_series_run", table_name="calendar_events")
    op.drop_index("ix_calendar_events_series", table_name="calendar_events")
    op.drop_constraint("fk_calendar_events_series", "calendar_events", type_="foreignkey")
    for column in ("recurrence_start", "series_id", "reminders", "skipped", "recurrence_until", "recurrence", "timezone", "all_day"):
        op.drop_column("calendar_events", column)
