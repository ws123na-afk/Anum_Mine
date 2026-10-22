"""Workspace-scoped, transactional idempotency claims for office creation."""

import sqlalchemy as sa
from alembic import op

revision = "0099_office_create_keys"
down_revision = "0098_agent_live_meetings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "office_create_keys",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"]
        ),
    )
    op.execute("alter table office_create_keys enable row level security")
    op.execute("alter table office_create_keys force row level security")
    op.execute("""create policy tenant_isolation_office_create_keys on office_create_keys
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))
        with check (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
                and workspace_id = nullif(current_setting('anum.workspace_id', true), ''))""")
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on office_create_keys to anum_tenant_purger")
    op.execute("""create policy lifecycle_purge_office_create_keys on office_create_keys
        for all to anum_tenant_purger
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), '')
           and exists (select 1 from tenant_lifecycle_jobs j
                       where j.tenant_id = office_create_keys.tenant_id and j.status = 'purging'
                         and (j.kind = 'organisation_deletion'
                              or (j.kind = 'workspace_deletion' and j.workspace_id = office_create_keys.workspace_id))))
        with check (false)""")


def downgrade() -> None:
    op.drop_table("office_create_keys")
