"""Governance depth: probed routing targets and role templates on memberships (expand only).

docs/governance-and-scale.md#routing-targets, docs/governance-and-scale.md#role-templates.

``routing_targets`` gains ``enabled`` (in routing, default true), ``last_checked_at``,
``measured_latency_ms``, ``last_error`` and ``probe_workspace_id``: what the health probe
(``POST /routing/targets/{id}/probe`` and the scheduler pass ``routing_probe``) measured, and
the workspace whose model configuration the scheduled probe reuses. ``anum_maintenance`` may
discover probed, enabled targets (ids and timestamps only, never provider details or writes);
the probe itself runs as the application role inside that tenant and workspace.

``workspace_memberships`` gains ``role_template_id`` and ``permission_limit`` (the template's
permissions, copied at assignment): a member's effective permissions are their role's
permissions intersected with the limit. Owners never carry a template.

Chained after ``0165_autonomy_undo_claim``.

Expand only. The previous release runs against this schema: it never reads the new columns,
``enabled`` defaults to true and the probe columns to null ("not yet probed"). Rollback:
``alembic downgrade 0165_autonomy_undo_claim`` drops the columns, the grant and the policy.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0166_governance_depth"
down_revision = "0165_autonomy_undo_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routing_targets", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("routing_targets", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("routing_targets", sa.Column("measured_latency_ms", sa.Integer(), nullable=True))
    op.add_column("routing_targets", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("routing_targets", sa.Column("probe_workspace_id", sa.String(80), nullable=True))
    # Scheduled probe discovery (anum_api.routing_probe): ids, the probing workspace and the time only.
    op.execute(
        "grant select (tenant_id, id, probe_workspace_id, last_checked_at, enabled) "
        "on routing_targets to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_routing_probe on routing_targets
        for select to anum_maintenance
        using (probe_workspace_id is not null and enabled)
        """
    )
    op.add_column("workspace_memberships", sa.Column("role_template_id", sa.String(80), nullable=True))
    op.add_column(
        "workspace_memberships",
        sa.Column("permission_limit", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_memberships", "permission_limit")
    op.drop_column("workspace_memberships", "role_template_id")
    op.execute("drop policy if exists maintenance_routing_probe on routing_targets")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, id, probe_workspace_id, last_checked_at, enabled) on routing_targets from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for column in ("probe_workspace_id", "last_error", "measured_latency_ms", "last_checked_at", "enabled"):
        op.drop_column("routing_targets", column)
