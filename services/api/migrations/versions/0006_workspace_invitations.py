"""Workspace invitations and the append-only audit table, both under tenant RLS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_workspace_invitations"
down_revision = "0005_workspace_model_configs"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        # SHA-256 hex of the single-use token; the token itself is never stored.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("invitee_user_id", sa.String(120), nullable=True),
        sa.Column("invitee_email", sa.String(320), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_by_user_id", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by_user_id", sa.String(120), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_invitations_workspace",
        ),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invitations_token_hash"),
        sa.CheckConstraint(
            "role in ('owner', 'member', 'viewer')", name="ck_workspace_invitations_role"
        ),
        sa.CheckConstraint(
            "status in ('pending', 'accepted', 'revoked')",
            name="ck_workspace_invitations_status",
        ),
        sa.CheckConstraint(
            "invitee_user_id is not null or invitee_email is not null",
            name="ck_workspace_invitations_invitee",
        ),
    )
    op.create_index(
        "ix_workspace_invitations_scope_status",
        "workspace_invitations",
        ["tenant_id", "workspace_id", "status"],
    )
    op.execute("alter table workspace_invitations enable row level security")
    op.execute("alter table workspace_invitations force row level security")
    op.execute(
        """
        create policy tenant_isolation_workspace_invitations on workspace_invitations
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

    op.create_table(
        "audit_records",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("action", sa.String(160), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_audit_records_workspace",
        ),
    )
    op.create_index(
        "ix_audit_records_scope_created",
        "audit_records",
        ["tenant_id", "workspace_id", "created_at"],
    )
    op.execute("alter table audit_records enable row level security")
    op.execute("alter table audit_records force row level security")
    # Append-only: there is deliberately no update or delete policy, so with RLS forced
    # those statements match no rows for every role that does not bypass RLS.
    op.execute(
        """
        create policy tenant_isolation_audit_records_read on audit_records
        for select using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        """
    )
    op.execute(
        """
        create policy tenant_isolation_audit_records_insert on audit_records
        for insert with check (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
        )
        """
    )


def downgrade() -> None:
    op.drop_table("audit_records")
    op.drop_table("workspace_invitations")
