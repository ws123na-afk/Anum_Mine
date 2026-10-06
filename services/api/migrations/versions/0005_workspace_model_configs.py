import sqlalchemy as sa
from alembic import op

revision = "0005_workspace_model_configs"
down_revision = "0004_run_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_model_configs",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        # Fernet ciphertext only; the plaintext provider key is never stored.
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_hint", sa.String(16), nullable=True),
        sa.Column("updated_by_user_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_model_configs_workspace",
        ),
        sa.CheckConstraint(
            "provider in ('mock', 'openai_compatible', 'ollama')",
            name="ck_workspace_model_configs_provider",
        ),
        sa.CheckConstraint(
            "provider <> 'openai_compatible' or api_key_ciphertext is not null",
            name="ck_workspace_model_configs_credential",
        ),
    )
    op.execute("alter table workspace_model_configs enable row level security")
    op.execute("alter table workspace_model_configs force row level security")
    op.execute(
        """
        create policy tenant_isolation_workspace_model_configs on workspace_model_configs
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
    op.drop_table("workspace_model_configs")
