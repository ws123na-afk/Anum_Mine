"""Approval decision reasons, integration targets and per-workspace approval policy.

Adds to ``approvals`` (docs/approvals-and-risk.md, threat model A4, A6 and G2):

* ``decision_reason``: the optional reason the approver or rejecter gave (500 chars).
* ``requested_by``: the user whose run proposed the call (two-person rule).
* ``target``: the configured integration host the tool will contact (host only).

``approvals`` keeps its existing forced tenant/workspace RLS policy unchanged.

Creates ``workspace_approval_policies``: one optional row per workspace with
``two_person_rule`` (a high-risk approval needs an owner other than the task creator
and the requester) and ``medium_risk_requires_approval`` (A5). The table is under
forced RLS scoped to the current tenant and workspace, like the other workspace tables;
no role bypasses it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_approval_policy"
down_revision = "0011_voice_automation"
branch_labels = None
depends_on = None

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.add_column("approvals", sa.Column("decision_reason", sa.String(length=500), nullable=True))
    op.add_column("approvals", sa.Column("requested_by", sa.String(length=160), nullable=True))
    op.add_column("approvals", sa.Column("target", sa.String(length=255), nullable=True))

    op.create_table(
        "workspace_approval_policies",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("two_person_rule", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "medium_risk_requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_workspace_approval_policies"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_approval_policies_workspace",
        ),
    )
    op.execute("alter table workspace_approval_policies enable row level security")
    op.execute("alter table workspace_approval_policies force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_workspace_approval_policies on workspace_approval_policies
        using (
          {WORKSPACE_PREDICATE}
        )
        with check (
          {WORKSPACE_PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table("workspace_approval_policies")
    op.drop_column("approvals", "target")
    op.drop_column("approvals", "requested_by")
    op.drop_column("approvals", "decision_reason")
