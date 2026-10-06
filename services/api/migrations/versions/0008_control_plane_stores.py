"""Control-plane stores under tenant RLS.

Skills, governance (policy packs, role templates, approval rules, memory governance),
the marketplace, routing targets, integration configurations, workspace file metadata
and notification preferences move from process memory to PostgreSQL. Governance audit
records reuse the append-only ``audit_records`` table from 0006.

Tenant-level tables are isolated by ``anum.tenant_id``; workspace-level tables by
``anum.tenant_id`` and ``anum.workspace_id``, like every other tenant table.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_control_plane_stores"
down_revision = "0007_event_outbox"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "skill_versions",
    "policy_packs",
    "role_templates",
    "approval_rules",
    "memory_governance",
    "marketplace_packages",
    "routing_targets",
)
WORKSPACE_TABLES = (
    "skill_installations",
    "marketplace_installs",
    "integration_configurations",
    "workspace_files",
    "notification_preferences",
)

TENANT_PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def _json_list(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant")


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def _enable_rls(table: str, predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        _json_list("required_tools"),
        sa.Column("risk_level", sa.String(40), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _tenant_fk("skill_versions"),
        sa.UniqueConstraint("tenant_id", "skill_id", "version", name="uq_skill_versions_tenant_skill_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skill_versions_tenant_id"),
        sa.CheckConstraint(
            "risk_level in ('low', 'medium', 'high', 'blocked')", name="ck_skill_versions_risk_level"
        ),
    )
    op.create_table(
        "skill_installations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("skill_version_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        _json_list("approved_tools"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("installed_by", sa.String(160), nullable=False),
        _timestamp("installed_at"),
        _workspace_fk("skill_installations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_version_id"],
            ["skill_versions.tenant_id", "skill_versions.id"],
            name="fk_skill_installations_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "skill_id", name="uq_skill_installations_scope_skill"
        ),
    )

    op.create_table(
        "policy_packs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _json_list("rules"),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _tenant_fk("policy_packs"),
        sa.CheckConstraint("version >= 1", name="ck_policy_packs_version"),
    )
    op.execute(
        "create unique index uq_policy_packs_tenant_name_version "
        "on policy_packs (tenant_id, lower(name), version)"
    )
    op.create_table(
        "role_templates",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        _json_list("permissions"),
        _timestamp("created_at"),
        _tenant_fk("role_templates"),
    )
    op.execute("create unique index uq_role_templates_tenant_name on role_templates (tenant_id, lower(name))")
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("action_pattern", sa.String(160), nullable=False),
        sa.Column("minimum_approvers", sa.Integer(), nullable=False),
        _json_list("required_roles"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _timestamp("created_at"),
        _tenant_fk("approval_rules"),
        sa.CheckConstraint(
            "minimum_approvers between 1 and 10", name="ck_approval_rules_minimum_approvers"
        ),
    )
    op.create_index("ix_approval_rules_tenant_created", "approval_rules", ["tenant_id", "created_at"])
    op.create_table(
        "memory_governance",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("default_retention_days", sa.Integer(), nullable=False),
        sa.Column("allow_permanent_retention", sa.Boolean(), nullable=False),
        sa.Column("require_provenance", sa.Boolean(), nullable=False),
        _json_list("allowed_source_types"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        _timestamp("updated_at"),
        _tenant_fk("memory_governance"),
        sa.CheckConstraint(
            "default_retention_days between 1 and 3650",
            name="ck_memory_governance_retention_days",
        ),
    )

    op.create_table(
        "marketplace_packages",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("publisher", sa.String(160), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        _json_list("permissions"),
        _json_list("regions"),
        _timestamp("updated_at"),
        _tenant_fk("marketplace_packages"),
        sa.CheckConstraint("kind in ('skill', 'integration')", name="ck_marketplace_packages_kind"),
    )
    op.create_table(
        "marketplace_installs",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("package_id", sa.String(160), primary_key=True),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("installed_by", sa.String(160), nullable=False),
        _timestamp("installed_at"),
        _workspace_fk("marketplace_installs"),
        # A package installed in any workspace of the tenant cannot leave the catalog.
        # Foreign-key checks are not filtered by RLS, so this holds across workspaces.
        sa.ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["marketplace_packages.tenant_id", "marketplace_packages.id"],
            name="fk_marketplace_installs_package",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "routing_targets",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _json_list("modalities"),
        _json_list("sensitivity"),
        sa.Column("cost_per_1k_tokens", sa.Double(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        _timestamp("updated_at"),
        _tenant_fk("routing_targets"),
        sa.CheckConstraint(
            "status in ('healthy', 'degraded', 'offline', 'unverified')",
            name="ck_routing_targets_status",
        ),
        sa.CheckConstraint("cost_per_1k_tokens >= 0", name="ck_routing_targets_cost"),
        sa.CheckConstraint("latency_ms > 0", name="ck_routing_targets_latency"),
    )

    op.create_table(
        "integration_configurations",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("integration_id", sa.String(80), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        _timestamp("updated_at"),
        _workspace_fk("integration_configurations"),
    )
    op.create_table(
        "workspace_files",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        # Object storage key; the bytes never enter the database.
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _workspace_fk("workspace_files"),
        sa.UniqueConstraint("storage_key", name="uq_workspace_files_storage_key"),
        sa.CheckConstraint("size_bytes > 0", name="ck_workspace_files_size"),
    )
    op.create_index(
        "ix_workspace_files_scope_created", "workspace_files", ["tenant_id", "workspace_id", "created_at"]
    )
    op.create_table(
        "notification_preferences",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("user_id", sa.String(160), primary_key=True),
        sa.Column("task_completed", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("run_failed", sa.Boolean(), nullable=False),
        sa.Column("automation_failed", sa.Boolean(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("desktop_enabled", sa.Boolean(), nullable=False),
        _timestamp("updated_at"),
        _workspace_fk("notification_preferences"),
    )

    for table in TENANT_TABLES:
        _enable_rls(table, TENANT_PREDICATE)
    for table in WORKSPACE_TABLES:
        _enable_rls(table, WORKSPACE_PREDICATE)


def downgrade() -> None:
    for table in (
        "notification_preferences",
        "workspace_files",
        "integration_configurations",
        "routing_targets",
        "marketplace_installs",
        "marketplace_packages",
        "memory_governance",
        "approval_rules",
        "role_templates",
        "policy_packs",
        "skill_installations",
        "skill_versions",
    ):
        op.drop_table(table)
