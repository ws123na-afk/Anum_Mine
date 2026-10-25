"""Staff my campus ledger for the ANUM MINE headquarters (expand only).

One row per desk says that an owner either asked to keep the desk empty (``keep_empty``) or that
"Staff my campus" claimed the desk (``claimed``) and later seated an agent there (``active``). The
primary key (workspace, desk) is the idempotency claim: a retry, a double click or two owners at once
never create a second agent for a desk. The table is new: the previous release never reads or writes
it, so a rollback is safe.
"""
import sqlalchemy as sa
from alembic import op

revision = "0107_staff_desks"
down_revision = "0106_agent_provisioning"
branch_labels = None
depends_on = None

TABLE = "office_desk_staffing"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("desk_id", sa.String(80), primary_key=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("template_id", sa.String(40), nullable=True),
        sa.Column("agent_id", sa.String(80), nullable=True),
        sa.Column("proposal_hash", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]),
        sa.CheckConstraint("status in ('keep_empty', 'claimed', 'active')", name="ck_office_desk_staffing_status"),
        sa.CheckConstraint("status <> 'active' or agent_id is not null", name="ck_office_desk_staffing_agent"),
        sa.CheckConstraint("status = 'keep_empty' or template_id is not null", name="ck_office_desk_staffing_role"),
    )
    op.create_index("ix_office_desk_staffing_agent", TABLE, ["tenant_id", "workspace_id", "agent_id"])
    op.execute("alter table office_desk_staffing enable row level security")
    op.execute("alter table office_desk_staffing force row level security")
    op.execute("""create policy tenant_isolation_office_desk_staffing on office_desk_staffing
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))
        with check (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
                and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))""")
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on office_desk_staffing to anum_tenant_purger")
    op.execute("""create policy lifecycle_purge_office_desk_staffing on office_desk_staffing
        for all to anum_tenant_purger
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and exists (select 1 from tenant_lifecycle_jobs j
                       where j.tenant_id = office_desk_staffing.tenant_id and j.status = 'purging'
                         and (j.kind = 'organisation_deletion'
                              or (j.kind = 'workspace_deletion' and j.workspace_id = office_desk_staffing.workspace_id))))
        with check (false)""")


def downgrade() -> None:
    op.drop_table(TABLE)
