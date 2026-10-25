"""Workspace-wide monthly cost cap for live agent meetings (security finding S-02).

Expand only. Chained on 0106_agent_provisioning; the number is renumbered at merge when
parallel branches collide. Two small scoped tables, both under forced row level security
like the 0098 and 0104 tables:

* ``agent_meeting_budgets``: one row per workspace, only once an owner changed the cap.
  No row means the default (USD 50 per calendar month, UTC).
* ``agent_meeting_usage_monthly``: the cost of every committed meeting turn per workspace
  and UTC month, written in the same transaction as the turn. It survives the owner
  deleting a transcript, which removes the turn rows.

No existing table or column changes, so the previous release still runs on this schema.
"""
import sqlalchemy as sa
from alembic import op

revision = "0107_agent_meeting_budget"
down_revision = "0106_agent_provisioning"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLES = ("agent_meeting_budgets", "agent_meeting_usage_monthly")
PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '') and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"


def _scope(table: str) -> list:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
            name=f"fk_{table}_workspace",
        ),
    ]


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"grant select (tenant_id, workspace_id, ctid), delete on {table} to anum_tenant_purger")
    purge_policy = (f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "  # nosec B608: fixed TABLES only
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)")
    op.execute(purge_policy)


def upgrade() -> None:
    op.create_table(
        "agent_meeting_budgets", *_scope("agent_meeting_budgets"),
        sa.Column("monthly_cost_limit_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_agent_meeting_budgets_scoped"),
        sa.CheckConstraint("monthly_cost_limit_usd >= 0 and monthly_cost_limit_usd <= 100000", name="ck_agent_meeting_budget_limit"),
    )
    op.create_table(
        "agent_meeting_usage_monthly", *_scope("agent_meeting_usage_monthly"),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("turns", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "period_start", name="pk_agent_meeting_usage_scoped"),
        sa.CheckConstraint("cost_usd >= 0 and turns >= 0", name="ck_agent_meeting_usage_values"),
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Manual operator approval and a backup are required for live data.
    for table in reversed(TABLES):
        op.drop_table(table)
