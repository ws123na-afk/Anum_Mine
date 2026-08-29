from alembic import op
import sqlalchemy as sa


revision = "0003_workspace_memberships"
down_revision = "0002_memory_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_memberships",
        sa.Column("user_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_memberships_workspace",
        ),
    )
    op.create_index(
        "ix_workspace_memberships_user",
        "workspace_memberships",
        ["user_id", "tenant_id", "workspace_id"],
    )
    op.execute("alter table workspace_memberships enable row level security")
    op.execute("alter table workspace_memberships force row level security")
    op.execute(
        """
        create policy tenant_isolation_workspace_memberships on workspace_memberships
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


def downgrade() -> None:
    op.drop_table("workspace_memberships")
