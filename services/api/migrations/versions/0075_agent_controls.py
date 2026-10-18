"""Owner agent controls: the emergency stop and per-agent monthly cost caps (docs/agent-controls.md).

Three tables under forced RLS:

* ``agent_emergency_stops``: one row per tenant (``tenant_isolation_<table>`` on
  ``anum.tenant_id``): whether the organization's "stop all agents and sends" switch is on,
  who changed it last, when and why. While it is on the runtime refuses model and tool
  calls, approvals of held sends and scheduled automations for that tenant.
* ``agent_cost_caps``: one row per agent profile with a monthly estimated-cost limit in USD,
  scoped by tenant and workspace like the agent profile itself.
* ``agent_usage_monthly``: the model usage attributed to each agent per UTC month, added with
  one ``insert ... on conflict do update`` per call, scoped by tenant and workspace.

The scheduled senders (marketing publisher, Email Center scheduled sends) find due work as
``anum_maintenance`` before acting inside each tenant: that role gets a column grant of
``tenant_id`` and ``engaged`` only on ``agent_emergency_stops`` and a select policy, so
discovery skips a stopped tenant's work without reading anything else.

Expand only: new tables, one column grant, one policy. The previous release never reads
them and keeps running against this schema.

Chained after ``0074_brevo_campaigns``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_agent_controls"
down_revision = "0074_brevo_campaigns"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
WORKSPACE_TABLES = ("agent_cost_caps", "agent_usage_monthly")
TS = sa.DateTime(timezone=True)


def _force_rls(table: str, predicate: str) -> None:
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


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.create_table(
        "agent_emergency_stops",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name="fk_agent_emergency_stops_tenant"), nullable=False),
        sa.Column("engaged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("changed_by", sa.String(160), nullable=False),
        sa.Column("changed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("engaged_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_agent_emergency_stops"),
        sa.CheckConstraint("version > 0", name="ck_agent_emergency_stops_version"),
    )
    op.create_table(
        "agent_cost_caps",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("monthly_cost_limit_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "agent_id", name="pk_agent_cost_caps"),
        sa.CheckConstraint("monthly_cost_limit_usd >= 0", name="ck_agent_cost_caps_limit"),
        _workspace_fk("agent_cost_caps"),
    )
    op.create_table(
        "agent_usage_monthly",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unpriced_calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "agent_id", "month", name="pk_agent_usage_monthly"),
        sa.CheckConstraint("extract(day from month) = 1", name="ck_agent_usage_monthly_month_start"),
        _workspace_fk("agent_usage_monthly"),
    )
    _force_rls("agent_emergency_stops", TENANT_PREDICATE)
    for table in WORKSPACE_TABLES:
        _force_rls(table, WORKSPACE_PREDICATE)
    # Discovery of due sends skips stopped tenants: two columns only, never the reason.
    op.execute("grant select (tenant_id, engaged) on agent_emergency_stops to anum_maintenance")
    op.execute(
        """
        create policy maintenance_agent_emergency_stops on agent_emergency_stops
        for select to anum_maintenance
        using (true)
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_agent_emergency_stops on agent_emergency_stops")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on agent_emergency_stops from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(WORKSPACE_TABLES):
        op.drop_table(table)
    op.drop_table("agent_emergency_stops")
