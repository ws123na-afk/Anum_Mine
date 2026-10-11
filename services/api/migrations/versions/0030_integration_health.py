"""Integration health history and workspace credentials (docs/tools-and-integrations.md).

``integration_health_checks`` keeps, per tenant, workspace and integration, the latest
health check (status, detail, latency, who or what ran it) and the latest failure, so
the Integrations page can show "last error" after the service recovered.

``integration_credentials`` holds a workspace's own credential for an integration that
accepts one (today the external REST webhook behind ``external.action``). The value is
stored only as Fernet ciphertext (``anum_api.secret_box``); ``credential_hint`` keeps
the last four characters the API shows, so listing never decrypts.

Both tables are workspace-scoped under forced RLS like ``integration_configurations``.
``anum_maintenance`` may discover which workspaces hold a credential (the ``tenant_id``,
``workspace_id`` and ``integration_id`` columns, nothing else) so
``python -m anum_api.rotate_secrets`` re-encrypts them as the application role inside
each workspace's RLS context, as it does for model provider keys.

Expand only: two new tables; the previous release ignores them.

Chained after ``0025_user_preferences``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_integration_health"
down_revision = "0029_file_management"
branch_labels = None
depends_on = None

TABLES = ("integration_health_checks", "integration_credentials")
WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def upgrade() -> None:
    op.create_table(
        "integration_health_checks",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("integration_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.String(500), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("checked_by", sa.String(160), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "integration_id", name="pk_integration_health_checks"),
        _workspace_fk("integration_health_checks"),
        sa.CheckConstraint(
            "status in ('connected', 'degraded', 'configured', 'disabled')", name="ck_integration_health_checks_status"
        ),
    )
    op.create_table(
        "integration_credentials",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("integration_id", sa.String(80), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("credential_hint", sa.String(16), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "integration_id", name="pk_integration_credentials"),
        _workspace_fk("integration_credentials"),
    )
    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {WORKSPACE_PREDICATE}
            )
            with check (
              {WORKSPACE_PREDICATE}
            )
            """
        )
    op.execute(
        "grant select (tenant_id, workspace_id, integration_id) on integration_credentials to anum_maintenance"
    )
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_integration_credentials on integration_credentials
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_integration_credentials on integration_credentials")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on integration_credentials from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
