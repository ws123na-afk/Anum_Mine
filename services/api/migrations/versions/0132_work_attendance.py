"""Work attendance and daily reports, phase A (docs/attendance.md#storage).

Seven new tables, each one person's own record inside one tenant and workspace:

* ``attendance_profiles``: organisation, department, role, workplace, time zone, schedule,
  overtime thresholds, location consent and pause, AI drafts switch (off by default).
* ``attendance_sites``: named workplaces, sites and clients with a geofence radius hint. No
  coordinates: the phone's own geofence decides, the server only learns the site's name.
* ``attendance_events``: arrivals, departures, breaks and remote work. Raw events are kept 90
  days (owner default), then reduced to ``attendance_day_totals`` and deleted. Unique per person
  on ``idem_key``, so a retried phone event is stored once.
* ``attendance_corrections``: every manual adjustment with its required reason, before and after.
* ``attendance_day_totals``: the reduced days (hours, first in, last out, sites, flags).
* ``attendance_daily_reports``: one report per person and day (tasks, issues, site visits,
  priority, follow up, notes, Files references by id), with a version for safe edits.
* ``attendance_shares``: a person lets one other member read their attendance (and, if chosen,
  their reports). A share never lets the viewer write.

Row level security (forced on every table):

* ``tenant_isolation_<table>``: the usual tenant and workspace predicate.
* Restrictive per command: SELECT admits the owner (``anum.user_id``), a viewer named in an
  active share by that owner (reports only when the share includes them), the tenant purger and
  the maintenance role; INSERT and UPDATE admit only the owner; DELETE the owner and the purger.
  Restrictive policies are AND-ed with the permissive ones, so a share widens reading only,
  inside the same tenant and workspace.
* ``attendance_shares``: readable by its owner and its viewer, written by its owner only.
* ``lifecycle_purge_<table>`` and its grants (0079's pattern): organisation and workspace deletion.
* Discovery for the daily job (``python -m anum_api.attendance.jobs``, run by the retention job),
  migration 0011's pattern, never BYPASSRLS: ``anum_maintenance`` may read the scope columns of
  events older than 90 days and of profiles with missed check alerts on, nothing else, no write.
  It also reads the scope columns of ``attendance_shares`` because the policies above refer to it.

Expand only: new tables, policies and grants. The previous release ignores them. Numbered 0132 and
chained after ``0131_finance_sms_ledger``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0132_work_attendance"
down_revision = "0131_finance_sms_ledger"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
ME = "nullif(current_setting('anum.user_id', true), '')"
OWNED = (
    "attendance_profiles",
    "attendance_sites",
    "attendance_events",
    "attendance_corrections",
    "attendance_day_totals",
    "attendance_daily_reports",
)
TABLES = (*OWNED, "attendance_shares")
RAW_DAYS = 90


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _shared(table: str, *, reports: bool) -> str:
    extra = " and s.include_reports" if reports else ""
    return (
        f"exists (select 1 from attendance_shares s where s.tenant_id = {table}.tenant_id "
        f"and s.workspace_id = {table}.workspace_id and s.owner_user_id = {table}.owner_user_id "
        f"and s.viewer_user_id = {ME} and s.revoked_at is null{extra})"
    )


def _purge_policy(table: str) -> None:
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to {PURGER} "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    owner = f"owner_user_id = {ME}"
    roles = f"current_user = '{PURGER}'::name or current_user = '{MAINT}'::name"
    if table == "attendance_shares":
        read = f"{owner} or viewer_user_id = {ME} or {roles}"
    else:
        read = f"{owner} or {_shared(table, reports=table == 'attendance_daily_reports')} or {roles}"
    op.execute(f"create policy owner_read_{table} on {table} as restrictive for select using ({read})")
    op.execute(f"create policy owner_insert_{table} on {table} as restrictive for insert with check ({owner})")
    op.execute(f"create policy owner_update_{table} on {table} as restrictive for update "
               f"using ({owner} or current_user = '{PURGER}'::name) with check ({owner})")
    op.execute(f"create policy owner_delete_{table} on {table} as restrictive for delete using ({owner} or current_user = '{PURGER}'::name)")
    _purge_policy(table)


def upgrade() -> None:
    # Created first: every other table's read policy refers to it.
    op.create_table(
        "attendance_shares",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("viewer_user_id", sa.String(160), nullable=False),
        sa.Column("include_reports", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", TS, nullable=True),
        _workspace_fk("attendance_shares"),
        sa.CheckConstraint("viewer_user_id <> owner_user_id", name="ck_attendance_shares_other"),
    )
    op.create_index("uq_attendance_shares_active", "attendance_shares", ["tenant_id", "workspace_id", "owner_user_id", "viewer_user_id"],
                    unique=True, postgresql_where=sa.text("revoked_at is null"))
    op.create_index("ix_attendance_shares_viewer", "attendance_shares", ["tenant_id", "workspace_id", "viewer_user_id"])

    op.create_table(
        "attendance_profiles",
        *_owned(),
        sa.Column("organisation", sa.String(160), nullable=True),
        sa.Column("department", sa.String(120), nullable=True),
        sa.Column("role_title", sa.String(120), nullable=True),
        sa.Column("workplace_site_id", sa.String(80), nullable=True),
        sa.Column("time_zone", sa.String(64), nullable=False, server_default="Asia/Dubai"),
        sa.Column("week_starts_on", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("schedule", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("daily_overtime_minutes", sa.Integer(), nullable=True),
        sa.Column("weekly_overtime_minutes", sa.Integer(), nullable=True),
        sa.Column("location_consent_at", TS, nullable=True),
        sa.Column("tracking_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused_at", TS, nullable=True),
        sa.Column("ai_drafts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("missed_check_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_alert_day", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_attendance_profiles"),
        _workspace_fk("attendance_profiles"),
        sa.CheckConstraint("week_starts_on between 0 and 6", name="ck_attendance_profiles_week"),
        sa.CheckConstraint("daily_overtime_minutes is null or daily_overtime_minutes between 60 and 1440", name="ck_attendance_profiles_daily_ot"),
        sa.CheckConstraint("weekly_overtime_minutes is null or weekly_overtime_minutes between 60 and 10080", name="ck_attendance_profiles_weekly_ot"),
        sa.CheckConstraint("version > 0", name="ck_attendance_profiles_version"),
    )

    op.create_table(
        "attendance_sites",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("address_label", sa.String(200), nullable=True),
        sa.Column("radius_m", sa.Integer(), nullable=False, server_default="150"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("attendance_sites"),
        sa.CheckConstraint(_in("kind", ("workplace", "site", "client", "home")), name="ck_attendance_sites_kind"),
        sa.CheckConstraint("radius_m between 50 and 2000", name="ck_attendance_sites_radius"),
        sa.CheckConstraint("length(name) > 0", name="ck_attendance_sites_name"),
    )
    op.create_index("uq_attendance_sites_name", "attendance_sites",
                    ["tenant_id", "workspace_id", "owner_user_id", sa.text("lower(name)")], unique=True)

    op.create_table(
        "attendance_events",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("site_id", sa.String(80), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("quality", sa.String(10), nullable=False, server_default="ok"),
        sa.Column("device_id", sa.String(80), nullable=True),
        sa.Column("idem_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("note", sa.String(300), nullable=True),
        _workspace_fk("attendance_events"),
        sa.CheckConstraint(_in("kind", ("arrive", "leave", "break_start", "break_end", "remote_start", "remote_end")),
                           name="ck_attendance_events_kind"),
        sa.CheckConstraint(_in("source", ("web", "device", "manual")), name="ck_attendance_events_source"),
        sa.CheckConstraint(_in("quality", ("ok", "poor")), name="ck_attendance_events_quality"),
        sa.CheckConstraint(_in("status", ("active", "voided", "superseded")), name="ck_attendance_events_status"),
        # A retried phone or web event is stored once (docs/attendance.md#idempotency).
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_attendance_events_idem"),
    )
    op.create_index("ix_attendance_events_owner", "attendance_events", ["tenant_id", "workspace_id", "owner_user_id", "occurred_at"])

    op.create_table(
        "attendance_corrections",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=True),
        sa.Column("replacement_event_id", sa.String(80), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("attendance_corrections"),
        sa.CheckConstraint(_in("action", ("add", "change", "void")), name="ck_attendance_corrections_action"),
        sa.CheckConstraint("length(btrim(reason)) >= 3", name="ck_attendance_corrections_reason"),
    )
    op.create_index("ix_attendance_corrections_owner", "attendance_corrections", ["tenant_id", "workspace_id", "owner_user_id", "created_at"])

    op.create_table(
        "attendance_day_totals",
        *_owned(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in (
            "presence_seconds", "remote_seconds", "break_seconds", "worked_seconds", "verified_seconds",
            "estimated_seconds", "scheduled_seconds", "event_count")],
        sa.Column("overtime_seconds", sa.Integer(), nullable=True),
        sa.Column("first_arrival", TS, nullable=True),
        sa.Column("last_departure", TS, nullable=True),
        sa.Column("site_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", "day", name="pk_attendance_day_totals"),
        _workspace_fk("attendance_day_totals"),
        sa.CheckConstraint("worked_seconds >= 0 and verified_seconds >= 0 and verified_seconds <= worked_seconds",
                           name="ck_attendance_day_totals_seconds"),
    )

    op.create_table(
        "attendance_daily_reports",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("project", sa.String(160), nullable=True),
        sa.Column("tasks_done", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tasks_pending", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("site_visits", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("issues", sa.Text(), nullable=True),
        sa.Column("time_spent_minutes", sa.Integer(), nullable=True),
        sa.Column("priority", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("follow_up_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("file_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("drafted_with_ai", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("attendance_daily_reports"),
        sa.CheckConstraint(_in("priority", ("low", "normal", "high", "urgent")), name="ck_attendance_daily_reports_priority"),
        sa.CheckConstraint("time_spent_minutes is null or time_spent_minutes between 0 and 1440", name="ck_attendance_daily_reports_time"),
        sa.CheckConstraint("version > 0", name="ck_attendance_daily_reports_version"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "day", name="uq_attendance_daily_reports_day"),
    )

    for table in TABLES:
        _rls(table)

    # The policies above read attendance_shares as whoever runs the query: the purger and the
    # maintenance role need the scope columns of it (they see no row of it: no policy admits them).
    op.execute(
        f"grant select (tenant_id, workspace_id, owner_user_id, viewer_user_id, include_reports, revoked_at) "
        f"on attendance_shares to {MAINT}, {PURGER}"
    )
    # Discovery only: scope columns of rows the daily job acts on, never content, no write.
    op.execute(f"grant select (tenant_id, workspace_id, owner_user_id) on attendance_events to {MAINT}")
    op.execute(
        f"""
        create policy maintenance_attendance_reduce on attendance_events
        for select to {MAINT}
        using (occurred_at < now() - interval '{RAW_DAYS} days')
        """
    )
    op.execute(f"grant select (tenant_id, workspace_id, owner_user_id) on attendance_profiles to {MAINT}")
    op.execute(
        f"""
        create policy maintenance_attendance_alerts on attendance_profiles
        for select to {MAINT}
        using (missed_check_alerts and (last_alert_day is null or last_alert_day < current_date))
        """
    )


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on attendance_events, attendance_profiles, attendance_shares from anum_maintenance;
          end if;
        end
        $$
        """
    )
    # The owned tables' read policies refer to attendance_shares: drop them first.
    for table in reversed(OWNED):
        op.drop_table(table)
    op.drop_table("attendance_shares")
