"""Owner-governed sales follow-up delivery, consent evidence and kill switch.

Expand migration after 0102_sales_crm. Pending actions remain stopped unless
explicitly approved and an owner enables the automation policy.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0103_sales_execution"
down_revision = "0102_sales_crm"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)

def _workspace_fk(table: str):
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )

def _rls(table: str):
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"create policy tenant_isolation_{table} on {table} "
        f"using ({SCOPE}) with check ({SCOPE})"
    )
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to anum_tenant_purger")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to anum_tenant_purger "
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def upgrade():
    op.create_table(
        "sales_automation_settings",
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_sends_per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(160), nullable=False, server_default="system"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("sales_automation_settings"),
        sa.CheckConstraint(
            "max_sends_per_day between 0 and 100", name="ck_sales_auto_daily_limit"
        ),
    )
    op.create_table(
        "sales_consent_events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("lead_id", sa.String(80), nullable=False),
        sa.Column("evidence_message_id", sa.String(80), nullable=True),
        sa.Column("evidence_note", sa.String(500), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("recorded_by", sa.String(160), nullable=False),
        sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("sales_consent_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "lead_id"],
            ["sales_leads.tenant_id", "sales_leads.workspace_id", "sales_leads.id"],
            name="fk_sales_consent_events_lead",
        ),
    )
    op.create_index(
        "ix_sales_consent_history",
        "sales_consent_events",
        ["tenant_id", "workspace_id", "lead_id", "recorded_at"],
    )
    op.add_column("sales_followups", sa.Column("template_name", sa.String(512)))
    op.add_column("sales_followups", sa.Column("template_language", sa.String(20)))
    op.add_column(
        "sales_followups",
        sa.Column("template_parameters", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("sales_followups", sa.Column("payload_hash", sa.String(64)))
    op.add_column("sales_followups", sa.Column("approved_by", sa.String(160)))
    op.add_column("sales_followups", sa.Column("approved_at", TS))
    op.add_column("sales_followups", sa.Column("sent_wamid", sa.String(200)))
    op.add_column("sales_followups", sa.Column("last_error", sa.Text()))
    op.add_column(
        "sales_followups", sa.Column("attempts", sa.Integer(),
                                   nullable=False, server_default="0")
    )
    op.add_column("sales_followups", sa.Column("last_attempt_at", TS))
    op.drop_constraint(
        "ck_sales_followups_status", "sales_followups", type_="check"
    )
    op.create_check_constraint(
        "ck_sales_followups_status", "sales_followups",
        "status in ('proposed','approved','dispatching','sent',"
        "'completed','cancelled','held','failed','unknown')",
    )
    op.create_index(
        "ix_sales_followups_pending_delivery", "sales_followups",
        ["due_at"],
        postgresql_where=sa.text("status = 'approved'"),
    )
    for table in ("sales_automation_settings", "sales_consent_events"):
        _rls(table)
    # Maintenance may discover only due approved actions; no customer data or tokens.
    op.execute(
        "grant select (id, tenant_id, workspace_id, status, due_at, last_attempt_at) "
        "on sales_followups to anum_maintenance"
    )
    op.execute(
        "create policy maintenance_due_sales_followups on sales_followups "
        "for select to anum_maintenance using (status in ('approved','dispatching'))"
    )

def downgrade():
    op.execute(
        "drop policy if exists maintenance_due_sales_followups on sales_followups"
    )
    op.execute(
        "revoke select (id, tenant_id, workspace_id, status, due_at, last_attempt_at) "
        "on sales_followups from anum_maintenance"
    )
    op.drop_index("ix_sales_followups_pending_delivery",
                  table_name="sales_followups")
    op.drop_constraint(
        "ck_sales_followups_status", "sales_followups", type_="check"
    )
    op.create_check_constraint(
        "ck_sales_followups_status", "sales_followups",
        "status in ('proposed','approved','completed','cancelled')",
    )
    for column in (
        "last_attempt_at", "attempts", "last_error", "sent_wamid",
        "approved_at", "approved_by", "payload_hash",
        "template_parameters", "template_language", "template_name",
    ):
        op.drop_column("sales_followups", column)
    op.drop_table("sales_consent_events")
    op.drop_table("sales_automation_settings")
