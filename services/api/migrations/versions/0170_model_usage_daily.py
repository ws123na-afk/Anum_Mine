"""Model spend by day, person, agent and feature, and a budget's own alert percent (expand only).

docs/model-gateway.md#spend-breakdown, docs/model-gateway.md#budget-alerts.

* ``model_usage_daily``: every model call the budgeted gateway records is also added to one row per
  workspace, UTC day, caller (``user_id``), agent (``agent_id``, the default assistant included) and
  feature (the module that made the call: ``tasks``, ``voice``, ``email`` and so on). The same five
  figures as ``model_usage_monthly`` (``0009_model_budgets``), added with ``insert ... on conflict do
  update`` so concurrent calls never lose an increment. ``GET /api/v1/model-budgets/usage`` groups it.
  Forced RLS like ``model_usage_monthly``: tenant-wide reads (the organisation breakdown needs every
  workspace's rows), writes only for the current workspace; the tenant purge role may delete the rows
  of a purging organisation or workspace (``0095``'s ``_purge_policy``). The monthly table stays the
  budget's source of truth: its figures are exact for months before this migration ran, which the
  daily table never has.
* ``model_budgets`` gains ``alert_percent`` (integer, null): an owner's own alert threshold, told to
  the workspace owners in the app like the fixed 50, 75, 90 and 100 percent notices.

Chained after ``0169_contacts_directory``.

Expand only. The previous release ignores the new table and column: it reads and writes the monthly
table as before, so a rollback to it only stops the daily breakdown from growing. Rollback:
``alembic downgrade 0169_contacts_directory`` drops the table and the column.
"""

import sqlalchemy as sa
from alembic import op

revision = "0170_model_usage_daily"
down_revision = "0169_contacts_directory"
branch_labels = None
depends_on = None

TABLE = "model_usage_daily"
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE = "workspace_id = nullif(current_setting('anum.workspace_id', true), '')"


def _policies(table: str, write_predicate: str) -> None:
    """0009's shape: tenant-wide select, writes only where ``write_predicate`` holds."""
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_read_{table} on {table} for select using ({TENANT})")
    op.execute(f"create policy tenant_insert_{table} on {table} for insert with check ({write_predicate})")
    op.execute(
        f"create policy tenant_update_{table} on {table} for update "
        f"using ({write_predicate}) with check ({write_predicate})"
    )
    op.execute(f"create policy tenant_delete_{table} on {table} for delete using ({write_predicate})")


def _purge_policy(table: str) -> None:
    """Organisation or workspace deletion may remove these rows (0079's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
            and (j.kind = 'organisation_deletion'
                 or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("agent_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("feature", sa.String(80), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unpriced_calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "day", "user_id", "agent_id", "feature", name="pk_model_usage_daily"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_model_usage_daily_workspace",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 and output_tokens >= 0 and estimated_cost_usd >= 0 "
            "and calls >= 0 and unpriced_calls >= 0",
            name="ck_model_usage_daily_non_negative",
        ),
    )
    # The breakdown reads one tenant (or workspace) over a day range.
    op.create_index("ix_model_usage_daily_tenant_day", TABLE, ["tenant_id", "day"])
    _policies(TABLE, f"{TENANT} and {WORKSPACE}")
    _purge_policy(TABLE)

    op.add_column("model_budgets", sa.Column("alert_percent", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_model_budgets_alert_percent", "model_budgets", "alert_percent is null or (alert_percent between 1 and 99)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_model_budgets_alert_percent", "model_budgets", type_="check")
    op.drop_column("model_budgets", "alert_percent")
    op.execute(f"drop policy if exists lifecycle_purge_{TABLE} on {TABLE}")
    op.drop_index("ix_model_usage_daily_tenant_day", table_name=TABLE)
    op.drop_table(TABLE)
