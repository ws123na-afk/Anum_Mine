"""Standard retention for meeting transcripts and the reception visitor register (expand only).

Owner decision O5 of 28 October 2026 ("keep what is standard", docs/ANUM_MINE_FEATURE_COMPLETION_MATRIX.md):

* ``workspace_record_retention``: one row per workspace, only once an owner changed a period. No row, or a null
  column, means the standard period: meeting transcripts 90 days after the meeting ended, visitor records 365 days
  after the visit. Forced row level security by tenant and workspace and the tenant purge policy, like 0107.
* Discovery for the daily retention job (``python -m anum_api.voice_retention``), migration 0011's pattern, never
  BYPASSRLS: ``anum_maintenance`` may read ``tenant_id`` and ``workspace_id`` (nothing else) of

  - ended meetings whose transcript is still stored and that ended more than 7 days ago (the shortest period an
    owner may choose), and
  - visits whose latest time (expected, checked in, checked out or cancelled) is more than 30 days ago (the
    shortest period for visitor records).

  It cannot read a title, a message, a name, a company or a purpose, and it cannot write. Each workspace is then
  purged as the application role inside its own tenant and workspace context, where its periods are read.

Expand only: a new table, two column grants and two policies. The previous release ignores all of them.
Chained on 0119_desk_work_hours (Green Concrete working hours), which landed first.
"""
import sqlalchemy as sa
from alembic import op

revision = "0120_record_retention"
down_revision = "0119_desk_work_hours"
branch_labels = None
depends_on = None

TABLE = "workspace_record_retention"
TS = sa.DateTime(timezone=True)
SCOPE = ("tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
         "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')")
# The shortest periods an owner may choose (anum_api/record_retention.py keeps the same numbers).
MIN_TRANSCRIPT_DAYS = 7
MIN_VISITOR_DAYS = 30
MAX_DAYS = 1825


def _rls(table: str) -> None:
    """Forced workspace RLS and the tenant purge policy of 0079 (organisation and workspace deletion)."""
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    op.execute(f"grant select (tenant_id, workspace_id, ctid), delete on {table} to anum_tenant_purger")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("transcript_days", sa.Integer(), nullable=True),
        sa.Column("visitor_days", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_workspace_record_retention"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
                                name="fk_workspace_record_retention_workspace"),
        sa.CheckConstraint(f"transcript_days is null or transcript_days between {MIN_TRANSCRIPT_DAYS} and {MAX_DAYS}",
                           name="ck_record_retention_transcript"),
        sa.CheckConstraint(f"visitor_days is null or visitor_days between {MIN_VISITOR_DAYS} and {MAX_DAYS}",
                           name="ck_record_retention_visitor"),
        sa.CheckConstraint("revision > 0", name="ck_record_retention_revision"),
    )
    _rls(TABLE)

    # Discovery only: scope columns of rows the purge may act on, never content, no write.
    op.execute("grant select (tenant_id, workspace_id) on agent_meeting_sessions to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_transcript_retention on agent_meeting_sessions
        for select to anum_maintenance
        using (status in ('finished', 'cancelled', 'failed') and transcript_deleted_at is null
               and coalesce(ended_at, updated_at) < now() - interval '{MIN_TRANSCRIPT_DAYS} days')
        """
    )
    op.execute("grant select (tenant_id, workspace_id) on office_visitors to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_visitor_retention on office_visitors
        for select to anum_maintenance
        using (greatest(expected_at, checked_in_at, checked_out_at, cancelled_at) < now() - interval '{MIN_VISITOR_DAYS} days')
        """
    )


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("drop policy if exists maintenance_visitor_retention on office_visitors")
    op.execute("drop policy if exists maintenance_transcript_retention on agent_meeting_sessions")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on office_visitors from anum_maintenance;
            revoke all on agent_meeting_sessions from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_table(TABLE)
