"""Calendar: series sync state, per-occurrence replies and email reminders (docs/calendar.md).

``calendar_events`` gains:

* ``sync_error`` and ``sync_attempts``: why the last send of the event to the connected Google or
  Microsoft calendar failed, and how many sends in a row failed. After five the event stops being
  retried on its own and its detail offers Retry (docs/calendar.md#how-events-sync).
* ``email_reminders``: the reminder minutes (a subset of ``reminders``) that also go out by email.
* ``occurrence_responses``: an attendee's reply to one occurrence of a series
  (``[{attendee_id, occurrence_start, response}]``); the attendee's own reply on the series is the
  default for every other occurrence.

``calendar_reminder_email_sends`` (workspace table) records every reminder email: one row per event,
occurrence, reminder and person, claimed with ``insert ... on conflict do nothing`` before the mail is
sent, so a pass that runs twice or in two API processes never emails twice. ``status`` says what
happened (``pending``, ``sent``, ``failed``, ``skipped``) and ``detail`` why, without an address or a
title. Forced RLS and the lifecycle purge policy like ``calendar_reminder_sends``.

Expand only: defaulted or nullable columns and one new table. The previous release ignores them (it
reads and writes the old columns; ``sync_state`` keeps its old values, so it simply keeps retrying an
event this release gave up on). Rollback: ``alembic downgrade 0170_model_usage_daily`` drops them.

Chained after ``0170_model_usage_daily``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0171_calendar_sync_depth"
down_revision = "0170_model_usage_daily"
branch_labels = None
depends_on = None

TABLE = "calendar_reminder_email_sends"
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("sync_error", sa.String(500), nullable=True))
    op.add_column("calendar_events", sa.Column("sync_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("calendar_events", sa.Column("email_reminders", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("calendar_events", sa.Column("occurrence_responses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_check_constraint("ck_calendar_events_sync_attempts", "calendar_events", "sync_attempts between 0 and 100")

    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("occurrence_start", TS, nullable=False),
        sa.Column("minutes_before", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("detail", sa.String(300), nullable=True),
        sa.Column("claimed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "event_id", "occurrence_start", "minutes_before", "user_id", name=f"pk_{TABLE}"),
        sa.ForeignKeyConstraint(["event_id"], ["calendar_events.id"], name=f"fk_{TABLE}_event", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"),
        sa.CheckConstraint("minutes_before between 0 and 40320", name=f"ck_{TABLE}_minutes"),
        sa.CheckConstraint("status in ('pending', 'sent', 'failed', 'skipped')", name=f"ck_{TABLE}_status"),
    )
    op.execute("alter table calendar_reminder_email_sends enable row level security")
    op.execute("alter table calendar_reminder_email_sends force row level security")
    op.execute(
        """
        create policy tenant_isolation_calendar_reminder_email_sends on calendar_reminder_email_sends
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        with check (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        """
    )
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on calendar_reminder_email_sends to anum_tenant_purger")
    op.execute(
        """
        create policy lifecycle_purge_calendar_reminder_email_sends on calendar_reminder_email_sends
        for all to anum_tenant_purger
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from tenant_lifecycle_jobs j
            where j.tenant_id = calendar_reminder_email_sends.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = calendar_reminder_email_sends.workspace_id))
          )
        )
        with check (false)
        """
    )


def downgrade() -> None:
    op.drop_table(TABLE)
    op.drop_constraint("ck_calendar_events_sync_attempts", "calendar_events", type_="check")
    for column in ("occurrence_responses", "email_reminders", "sync_attempts", "sync_error"):
        op.drop_column("calendar_events", column)
