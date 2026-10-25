"""Governed agent provisioning ledger for the ANUM MINE headquarters (expand only).

One row per (workspace, template) says which agent an owner confirmed as the handler of a kind of
work (orders, unowned leads, ...). It is the idempotency claim for the confirm call (a retry never
creates a second agent) and the record the live flow reads to seat unowned orders in the handler's
office. The table is new: the previous release never reads or writes it, so a rollback is safe.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0106_agent_provisioning"
down_revision = "0105_office_builder"
branch_labels = None
depends_on = None

TABLE = "office_agent_provisions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("template_id", sa.String(40), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("work_kinds", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("agent_id", sa.String(80), nullable=True),
        sa.Column("desk_id", sa.String(80), nullable=True),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]),
        sa.CheckConstraint("status in ('claimed', 'active')", name="ck_office_provision_status"),
        sa.CheckConstraint("jsonb_typeof(work_kinds) = 'array'", name="ck_office_provision_kinds"),
        sa.CheckConstraint("status = 'claimed' or agent_id is not null", name="ck_office_provision_active_agent"),
    )
    op.create_index("ix_office_provision_agent", TABLE, ["tenant_id", "workspace_id", "agent_id"])
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(f"""create policy tenant_isolation_{TABLE} on {TABLE}
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))
        with check (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
                and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))""")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {TABLE} to anum_tenant_purger")
    op.execute(f"""create policy lifecycle_purge_{TABLE} on {TABLE}
        for all to anum_tenant_purger
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and exists (select 1 from tenant_lifecycle_jobs j
                       where j.tenant_id = {TABLE}.tenant_id and j.status = 'purging'
                         and (j.kind = 'organisation_deletion'
                              or (j.kind = 'workspace_deletion' and j.workspace_id = {TABLE}.workspace_id))))
        with check (false)""")


def downgrade() -> None:
    op.drop_table(TABLE)
