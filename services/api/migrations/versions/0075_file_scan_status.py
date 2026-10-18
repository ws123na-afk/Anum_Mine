"""Malware scan state of workspace files, and discovery for the rescan job
(docs/files.md#malware-scanning).

``workspace_files`` gains ``scan_status`` (null until a scanner looked at the bytes,
``clean`` or ``infected``), ``scan_signature`` (what was found) and ``scanned_at``. An
``infected`` file is quarantined: still listed so it can be deleted, never served.

``python -m anum_api.file_rescan`` scans the files stored before scanning was turned on. It
finds them as ``anum_maintenance`` (migration 0011's pattern, never BYPASSRLS): a column grant
of ``id``, ``tenant_id``, ``workspace_id`` and ``scan_status`` only (never the name, key or
digest) and a select policy that shows only unscanned rows. Reading the bytes and recording
the verdict run as the application role inside each workspace's RLS context.

Expand only: three nullable columns, a check constraint, a partial index, one column grant
and one policy. The previous release ignores them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_file_scan_status"
down_revision = "0074_brevo_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_files", sa.Column("scan_status", sa.String(length=16), nullable=True))
    op.add_column("workspace_files", sa.Column("scan_signature", sa.String(length=200), nullable=True))
    op.add_column("workspace_files", sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_workspace_files_scan_status",
        "workspace_files",
        "scan_status is null or scan_status in ('clean', 'infected')",
    )
    op.create_index(
        "ix_workspace_files_unscanned",
        "workspace_files",
        ["id"],
        postgresql_where=sa.text("scan_status is null"),
    )
    op.execute("grant select (id, tenant_id, workspace_id, scan_status) on workspace_files to anum_maintenance")
    op.execute(
        """
        create policy maintenance_rescan_workspace_files on workspace_files
        for select to anum_maintenance
        using (scan_status is null)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_rescan_workspace_files on workspace_files")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (id, tenant_id, workspace_id, scan_status) on workspace_files from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_index("ix_workspace_files_unscanned", table_name="workspace_files")
    op.drop_constraint("ck_workspace_files_scan_status", "workspace_files", type_="check")
    op.drop_column("workspace_files", "scanned_at")
    op.drop_column("workspace_files", "scan_signature")
    op.drop_column("workspace_files", "scan_status")
