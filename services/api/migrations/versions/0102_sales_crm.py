"""Sales CRM persistence: scoped leads, audit events, quotes and follow-ups.

Expand-only migration. All tables enforce tenant and workspace RLS.
"""
import sqlalchemy as sa
from alembic import op

revision = "0102_sales_crm"
down_revision = "0101_executive_grants"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("sales_leads", "sales_lead_events", "sales_quotes", "sales_followups")


def scoped(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"create policy tenant_isolation_{table} on {table} "
        f"using ({SCOPE}) with check ({SCOPE})"
    )


def workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def upgrade() -> None:
    op.create_table(
        "sales_leads",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("source_conversation_id", sa.String(80), nullable=False),
        sa.Column("source_message_id", sa.String(160), nullable=False),
        sa.Column("source_campaign_hint", sa.String(64)),
        sa.Column("preferred_language", sa.String(5), nullable=False),
        sa.Column("intent", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="new"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("company_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone_e164", sa.String(20)),
        sa.Column("requirements", sa.Text(), nullable=False, server_default=""),
        sa.Column("budget_min", sa.Numeric(14, 2)),
        sa.Column("budget_max", sa.Numeric(14, 2)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("assigned_to", sa.String(160)),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_contact_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        workspace_fk("sales_leads"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "source_message_id", name="uq_sales_leads_inbound_message"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_sales_leads_scope_id"),
        sa.CheckConstraint("revision >= 1", name="ck_sales_leads_revision"),
        sa.CheckConstraint("budget_min is null or budget_min >= 0", name="ck_sales_leads_min"),
        sa.CheckConstraint("budget_max is null or budget_max >= 0", name="ck_sales_leads_max"),
        sa.CheckConstraint("budget_min is null or budget_max is null or budget_min <= budget_max", name="ck_sales_leads_budget"),
        sa.CheckConstraint("preferred_language in ('ar','en')", name="ck_sales_leads_language"),
    )
    op.create_index("ix_sales_leads_pipeline", "sales_leads", ["tenant_id", "workspace_id", "status", "updated_at"])
    op.create_index("ix_sales_leads_owner", "sales_leads", ["tenant_id", "workspace_id", "assigned_to"])
    op.create_table(
        "sales_lead_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("from_status", sa.String(40)),
        sa.Column("to_status", sa.String(40)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        workspace_fk("sales_lead_events"),
        sa.ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"], ["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"], name="fk_sales_lead_events_lead"),
        sa.UniqueConstraint("tenant_id","workspace_id","lead_id","revision",name="uq_sales_lead_events_revision"),
    )
    op.create_index("ix_sales_lead_events_history", "sales_lead_events", ["tenant_id", "workspace_id", "lead_id", "occurred_at"])
    op.create_table(
        "sales_quotes",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("line_items", sa.JSON(), nullable=False),
        sa.Column("total", sa.Numeric(14,2), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("approval_id", sa.String(80)),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        workspace_fk("sales_quotes"),
        sa.ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"],["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"],name="fk_sales_quotes_lead"),
        sa.CheckConstraint("total >= 0",name="ck_sales_quotes_total"),
        sa.CheckConstraint("status in ('draft','pending_approval','approved','rejected','sent')",name="ck_sales_quotes_status"),
    )
    op.create_index("ix_sales_quotes_lead", "sales_quotes", ["tenant_id","workspace_id","lead_id","created_at"])
    op.create_table(
        "sales_followups",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("due_at", TS, nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column("approval_id", sa.String(80)),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        workspace_fk("sales_followups"),
        sa.ForeignKeyConstraint(["tenant_id","workspace_id","lead_id"],["sales_leads.tenant_id","sales_leads.workspace_id","sales_leads.id"],name="fk_sales_followups_lead"),
        sa.CheckConstraint("status in ('proposed','approved','completed','cancelled')",name="ck_sales_followups_status"),
        sa.CheckConstraint("channel in ('whatsapp','email','call')",name="ck_sales_followups_channel"),
    )
    op.create_index("ix_sales_followups_due", "sales_followups", ["tenant_id","workspace_id","status","due_at"])
    for table in TABLES:
        scoped(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
