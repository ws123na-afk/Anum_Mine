"""Setup plans: the ledger of applied blueprint items (docs/setup-plan.md).

- ``setup_plan_items``: one row per applied item of a setup plan (the object it created or
  linked, a fingerprint of what it set, the object's version then). ``workspace_id`` is the
  request's workspace for workspace-level items and ``''`` for tenant-level ones (policy
  packs, approval rules, role templates, memory governance, published skill versions and
  created workspaces), so a second workspace sees that the tenant's governance is already
  applied. Forced RLS on ``anum.tenant_id`` and on ``workspace_id`` being the request's
  workspace or ``''``.

- ``setup_plan_checks``: the Week 1 pilot checks a person recorded for the tenant (pass,
  fail or not run, a note, an evidence reference, who and when). Tenant-level: forced RLS on
  ``anum.tenant_id``. Every change is also an ``audit_records`` row.

No role is granted anything new and nothing bypasses RLS: nothing reads the ledger across
tenants.

Expand only: two new tables the previous release never reads.

Chained after ``0051_chat_depth``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_setup_plans"
down_revision = "0051_chat_depth"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and (workspace_id = nullif(current_setting('anum.workspace_id', true), '')\n"
    "               or workspace_id = '')"
)


def upgrade() -> None:
    op.create_table(
        "setup_plan_items",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("plan_id", sa.String(80), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=True),
        sa.Column("target_version", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("applied_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "plan_id", "item_key", name="pk_setup_plan_items"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_setup_plan_items_tenant"),
        sa.CheckConstraint("state in ('claimed', 'applied')", name="ck_setup_plan_items_state"),
        sa.CheckConstraint("origin in ('created', 'linked')", name="ck_setup_plan_items_origin"),
    )
    op.execute("alter table setup_plan_items enable row level security")
    op.execute("alter table setup_plan_items force row level security")
    op.execute(
        f"""
        create policy scope_isolation_setup_plan_items on setup_plan_items
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )

    op.create_table(
        "setup_plan_checks",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("plan_id", sa.String(80), nullable=False),
        sa.Column("check_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False, server_default=""),
        sa.Column("evidence", sa.String(300), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "plan_id", "check_id", name="pk_setup_plan_checks"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_setup_plan_checks_tenant"),
        sa.CheckConstraint("status in ('pass', 'fail', 'not_run')", name="ck_setup_plan_checks_status"),
    )
    op.execute("alter table setup_plan_checks enable row level security")
    op.execute("alter table setup_plan_checks force row level security")
    op.execute(
        """
        create policy tenant_isolation_setup_plan_checks on setup_plan_checks
        using (tenant_id = nullif(current_setting('anum.tenant_id', true), ''))
        with check (tenant_id = nullif(current_setting('anum.tenant_id', true), ''))
        """
    )


def downgrade() -> None:
    op.drop_table("setup_plan_checks")
    op.drop_table("setup_plan_items")
