"""Attendance: the workspace-wide manager view, off by default (expand only). docs/attendance.md#manager-view.

* ``attendance_workspace_settings``: one row per workspace, ``manager_view`` (default false). Forced RLS on
  ``anum.tenant_id`` and ``anum.workspace_id`` (every member of the workspace may read whether it is on, so
  their own screen can say so). A row is written only in a unit where the API set ``anum.attendance_manager``
  after checking the caller is a workspace owner or an organisation administrator (restrictive insert and
  update policies). The ``anum_tenant_purger`` grants and purge policy of ``0079_tenant_lifecycle``; listed in
  ``anum_api/tenant_lifecycle_tables.py``.
* The restrictive read policies of ``attendance_profiles``, ``attendance_sites``, ``attendance_events`` and
  ``attendance_day_totals`` (``owner_read_<table>`` of ``0132_work_attendance``) gain one more way in: a unit
  with ``anum.attendance_manager = 'on'`` (set by the API for an owner or organisation administrator, read only
  routes) while the workspace's ``manager_view`` is on. Both must hold: the database checks the setting itself,
  so a unit that sets the flag in a workspace that never turned the view on reads nothing more. Daily reports,
  corrections and shares are not widened: a report is still read only through the person's own share.

Chained after ``0180_app_call_rooms``. Expand only: the previous release never sets
``anum.attendance_manager`` and never reads the new table, so for it every policy admits exactly the rows it
did. Rollback never reverses it in production.
"""

import sqlalchemy as sa
from alembic import op

revision = "0181_attendance_manager_view"
down_revision = "0180_app_call_rooms"
branch_labels = None
depends_on = None

PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
MANAGER_FLAG = "current_setting('anum.attendance_manager', true) = 'on'"

# The read policies of 0132, written out per table (bandit B608: no SQL is built from variables).
OLD_READ = {
    "attendance_profiles": """
        create policy owner_read_attendance_profiles on attendance_profiles as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_profiles.tenant_id
                     and s.workspace_id = attendance_profiles.workspace_id and s.owner_user_id = attendance_profiles.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
        )""",
    "attendance_sites": """
        create policy owner_read_attendance_sites on attendance_sites as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_sites.tenant_id
                     and s.workspace_id = attendance_sites.workspace_id and s.owner_user_id = attendance_sites.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
        )""",
    "attendance_events": """
        create policy owner_read_attendance_events on attendance_events as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_events.tenant_id
                     and s.workspace_id = attendance_events.workspace_id and s.owner_user_id = attendance_events.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
        )""",
    "attendance_day_totals": """
        create policy owner_read_attendance_day_totals on attendance_day_totals as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_day_totals.tenant_id
                     and s.workspace_id = attendance_day_totals.workspace_id and s.owner_user_id = attendance_day_totals.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
        )""",
}

NEW_READ = {
    "attendance_profiles": """
        create policy owner_read_attendance_profiles on attendance_profiles as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_profiles.tenant_id
                     and s.workspace_id = attendance_profiles.workspace_id and s.owner_user_id = attendance_profiles.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
          or (current_setting('anum.attendance_manager', true) = 'on'
              and exists (select 1 from attendance_workspace_settings w where w.tenant_id = attendance_profiles.tenant_id
                          and w.workspace_id = attendance_profiles.workspace_id and w.manager_view))
        )""",
    "attendance_sites": """
        create policy owner_read_attendance_sites on attendance_sites as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_sites.tenant_id
                     and s.workspace_id = attendance_sites.workspace_id and s.owner_user_id = attendance_sites.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
          or (current_setting('anum.attendance_manager', true) = 'on'
              and exists (select 1 from attendance_workspace_settings w where w.tenant_id = attendance_sites.tenant_id
                          and w.workspace_id = attendance_sites.workspace_id and w.manager_view))
        )""",
    "attendance_events": """
        create policy owner_read_attendance_events on attendance_events as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_events.tenant_id
                     and s.workspace_id = attendance_events.workspace_id and s.owner_user_id = attendance_events.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
          or (current_setting('anum.attendance_manager', true) = 'on'
              and exists (select 1 from attendance_workspace_settings w where w.tenant_id = attendance_events.tenant_id
                          and w.workspace_id = attendance_events.workspace_id and w.manager_view))
        )""",
    "attendance_day_totals": """
        create policy owner_read_attendance_day_totals on attendance_day_totals as restrictive for select using (
          owner_user_id = nullif(current_setting('anum.user_id', true), '')
          or exists (select 1 from attendance_shares s where s.tenant_id = attendance_day_totals.tenant_id
                     and s.workspace_id = attendance_day_totals.workspace_id and s.owner_user_id = attendance_day_totals.owner_user_id
                     and s.viewer_user_id = nullif(current_setting('anum.user_id', true), '') and s.revoked_at is null)
          or current_user = 'anum_tenant_purger'::name or current_user = 'anum_maintenance'::name
          or (current_setting('anum.attendance_manager', true) = 'on'
              and exists (select 1 from attendance_workspace_settings w where w.tenant_id = attendance_day_totals.tenant_id
                          and w.workspace_id = attendance_day_totals.workspace_id and w.manager_view))
        )""",
}

DROP_READ = {
    "attendance_profiles": "drop policy if exists owner_read_attendance_profiles on attendance_profiles",
    "attendance_sites": "drop policy if exists owner_read_attendance_sites on attendance_sites",
    "attendance_events": "drop policy if exists owner_read_attendance_events on attendance_events",
    "attendance_day_totals": "drop policy if exists owner_read_attendance_day_totals on attendance_day_totals",
}


def upgrade() -> None:
    op.create_table(
        "attendance_workspace_settings",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("manager_view", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_attendance_workspace_settings"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
                                name="fk_attendance_workspace_settings_workspace"),
        sa.CheckConstraint("version > 0", name="ck_attendance_workspace_settings_version"),
    )
    op.execute("alter table attendance_workspace_settings enable row level security")
    op.execute("alter table attendance_workspace_settings force row level security")
    op.execute(
        f"create policy tenant_isolation_attendance_workspace_settings on attendance_workspace_settings "
        f"using ({SCOPE}) with check ({SCOPE})"
    )
    op.execute(
        "create policy manager_insert_attendance_workspace_settings on attendance_workspace_settings as restrictive "
        f"for insert with check ({MANAGER_FLAG})"
    )
    op.execute(
        "create policy manager_update_attendance_workspace_settings on attendance_workspace_settings as restrictive "
        f"for update using ({MANAGER_FLAG} or current_user = '{PURGER}'::name) with check ({MANAGER_FLAG})"
    )
    op.execute(
        "create policy manager_delete_attendance_workspace_settings on attendance_workspace_settings as restrictive "
        f"for delete using ({MANAGER_FLAG} or current_user = '{PURGER}'::name)"
    )
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on attendance_workspace_settings to {PURGER}")
    op.execute(
        """
        create policy lifecycle_purge_attendance_workspace_settings on attendance_workspace_settings
        for all to anum_tenant_purger
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from tenant_lifecycle_jobs j
            where j.tenant_id = attendance_workspace_settings.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = attendance_workspace_settings.workspace_id))
          )
        )
        with check (false)
        """
    )
    # The widened read policies refer to the new table as whoever runs the query: the purger and the
    # maintenance role read its scope columns and the switch (they see no row of it: no policy admits them).
    op.execute(f"grant select (tenant_id, workspace_id, manager_view) on attendance_workspace_settings to {MAINT}, {PURGER}")
    for table in ("attendance_profiles", "attendance_sites", "attendance_events", "attendance_day_totals"):
        op.execute(DROP_READ[table])
        op.execute(NEW_READ[table])


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    for table in ("attendance_profiles", "attendance_sites", "attendance_events", "attendance_day_totals"):
        op.execute(DROP_READ[table])
        op.execute(OLD_READ[table])
    op.drop_table("attendance_workspace_settings")
