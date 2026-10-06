"""Per-tenant and per-workspace model budgets and monthly usage (threat model G4).

``model_budgets`` holds one optional budget per tenant (``workspace_id`` null) and per
workspace. ``model_usage_monthly`` accumulates each workspace's model usage per UTC
calendar month; a tenant's usage is the sum of its workspaces' rows.

Both tables are under forced RLS. Reads are tenant-wide (the tenant budget and tenant
usage need every workspace's rows of the same tenant); writes are limited to the
current workspace's rows, plus the tenant-level budget row.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_model_budgets"
down_revision = "0008_control_plane_stores"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE = "workspace_id = nullif(current_setting('anum.workspace_id', true), '')"


def _policies(table: str, write_predicate: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_read_{table} on {table} for select using ({TENANT})")
    op.execute(
        f"create policy tenant_insert_{table} on {table} for insert with check ({write_predicate})"
    )
    op.execute(
        f"create policy tenant_update_{table} on {table} for update "
        f"using ({write_predicate}) with check ({write_predicate})"
    )
    op.execute(f"create policy tenant_delete_{table} on {table} for delete using ({write_predicate})")


def upgrade() -> None:
    op.create_table(
        "model_budgets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        # Null for the tenant-wide budget.
        sa.Column("workspace_id", sa.String(80), nullable=True),
        sa.Column("monthly_cost_limit_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_model_budgets_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_model_budgets_workspace",
        ),
        sa.CheckConstraint(
            "monthly_cost_limit_usd is null or monthly_cost_limit_usd >= 0",
            name="ck_model_budgets_cost_limit",
        ),
        sa.CheckConstraint(
            "monthly_token_limit is null or monthly_token_limit >= 0",
            name="ck_model_budgets_token_limit",
        ),
    )
    op.execute(
        "create unique index uq_model_budgets_scope "
        "on model_budgets (tenant_id, coalesce(workspace_id, ''))"
    )
    _policies("model_budgets", f"{TENANT} and (workspace_id is null or {WORKSPACE})")

    op.create_table(
        "model_usage_monthly",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unpriced_calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "month", name="pk_model_usage_monthly"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_model_usage_monthly_workspace",
        ),
        sa.CheckConstraint("extract(day from month) = 1", name="ck_model_usage_monthly_month_start"),
        sa.CheckConstraint(
            "input_tokens >= 0 and output_tokens >= 0 and estimated_cost_usd >= 0 "
            "and calls >= 0 and unpriced_calls >= 0",
            name="ck_model_usage_monthly_non_negative",
        ),
    )
    _policies("model_usage_monthly", f"{TENANT} and {WORKSPACE}")


def downgrade() -> None:
    op.drop_table("model_usage_monthly")
    op.execute("drop index if exists uq_model_budgets_scope")
    op.drop_table("model_budgets")
