"""Business support tickets (docs/business.md#support-tickets).

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 and 0070 pattern):

* ``business_support_tickets``: a ticket with its requester, the account, contact and project
  it concerns where known, the entitlement that covers it (a project or a support plan, which
  is a catalog version), severity, status, assignee, the SLA due times the owner's policy
  gives it, the escalation flag and the resolution audit (code, summary, who and when). A
  ticket from an inbound email or WhatsApp conversation stores the message or conversation id
  only (``source_ref``): a link, never a copy of the text. A partial unique index keeps one
  open ticket per linked message.
* ``business_ticket_replies``: reply drafts and their hand-off to the inbox's own approval
  path (the email draft or WhatsApp message id, the approval and task ids, the outcome).

Two expand-only additions to 0070 tables, both nullable or defaulted so the previous release
ignores them: ``business_activities.ticket_id`` (the ticket's activity trail, with a scoped
foreign key) and ``business_settings.support_policy`` (working days, hours, time zone and the
targets per severity; null means the defaults).

References carry the scope (``(tenant_id, workspace_id, <id>)`` composite foreign keys), so a
ticket can never point into another tenant or workspace. No role is granted anything and no
discovery role reads these tables.

Numbered 0076 and chained after ``0074_brevo_campaigns`` (a sibling branch takes 0075; the
owner renumbers at merge).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076_business_support"
down_revision = "0074_brevo_campaigns"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_support_tickets", "business_ticket_replies")
TS = sa.DateTime(timezone=True)
SEVERITIES = ("critical", "high", "normal", "low")
STATUSES = ("new", "triaged", "in_progress", "waiting_on_customer", "resolved", "closed")
OPEN_STATUSES = ("new", "triaged", "in_progress", "waiting_on_customer")
RESOLUTIONS = ("fixed", "workaround", "answered", "no_fault_found", "duplicate", "out_of_scope")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _base(table: str) -> list:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{table}_scope_id"),
    ]


def _ref(table: str, column: str, target: str, *, cascade: bool) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column],
        [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"],
        name=f"fk_{table}_{column}",
        ondelete="CASCADE" if cascade else "RESTRICT",
    )


def _enable_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "business_support_tickets",
        *_base("business_support_tickets"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("requester_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("requester_email", sa.String(254), nullable=True),
        sa.Column("requester_phone", sa.String(40), nullable=True),
        sa.Column("requester_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entitlement", sa.String(20), nullable=False, server_default="none"),
        sa.Column("support_offer_id", sa.String(80), nullable=True),
        sa.Column("entitlement_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("assignee_id", sa.String(160), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(80), nullable=True),
        sa.Column("source_account_id", sa.String(80), nullable=True),
        sa.Column("reported_at", TS, nullable=False),
        sa.Column("response_due_at", TS, nullable=True),
        sa.Column("resolution_due_at", TS, nullable=True),
        sa.Column("first_responded_at", TS, nullable=True),
        sa.Column("sla_paused_at", TS, nullable=True),
        sa.Column("sla_paused_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("escalation_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("escalated_at", TS, nullable=True),
        sa.Column("escalated_by", sa.String(160), nullable=True),
        sa.Column("resolution_code", sa.String(30), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("resolved_by", sa.String(160), nullable=True),
        sa.Column("closed_at", TS, nullable=True),
        sa.Column("closed_by", sa.String(160), nullable=True),
        sa.Column("reopened_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_support_tickets", "account_id", "business_accounts", cascade=False),
        _ref("business_support_tickets", "contact_id", "business_contacts", cascade=False),
        _ref("business_support_tickets", "project_id", "business_projects", cascade=False),
        _ref("business_support_tickets", "support_offer_id", "business_offers", cascade=False),
        sa.CheckConstraint(_in("severity", SEVERITIES), name="ck_business_support_tickets_severity"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_business_support_tickets_status"),
        sa.CheckConstraint(_in("entitlement", ("project", "support_plan", "none")), name="ck_business_support_tickets_entitlement"),
        sa.CheckConstraint(_in("source", ("manual", "email", "whatsapp")), name="ck_business_support_tickets_source"),
        sa.CheckConstraint("resolution_code is null or " + _in("resolution_code", RESOLUTIONS), name="ck_business_support_tickets_resolution"),
        sa.CheckConstraint("source = 'manual' or source_ref is not null", name="ck_business_support_tickets_source_ref"),
        sa.CheckConstraint("entitlement <> 'project' or project_id is not null", name="ck_business_support_tickets_project"),
        sa.CheckConstraint("entitlement <> 'support_plan' or support_offer_id is not null", name="ck_business_support_tickets_plan"),
        # A resolved or closed ticket always says how and by whom.
        sa.CheckConstraint(
            "status not in ('resolved', 'closed') or (resolved_at is not null and resolved_by is not null and resolution_code is not null)",
            name="ck_business_support_tickets_resolved",
        ),
        sa.CheckConstraint("sla_paused_minutes >= 0 and reopened_count >= 0", name="ck_business_support_tickets_counts"),
    )
    op.create_index("ix_business_support_tickets_scope_updated", "business_support_tickets", ["tenant_id", "workspace_id", "updated_at", "id"])
    op.create_index("ix_business_support_tickets_status_due", "business_support_tickets", ["tenant_id", "workspace_id", "status", "resolution_due_at"])
    op.create_index("ix_business_support_tickets_account", "business_support_tickets", ["tenant_id", "workspace_id", "account_id"])
    op.create_index("ix_business_support_tickets_project", "business_support_tickets", ["tenant_id", "workspace_id", "project_id"])
    op.create_index(
        "uq_business_support_tickets_open_source", "business_support_tickets", ["tenant_id", "workspace_id", "source", "source_ref"],
        unique=True, postgresql_where=sa.text("source_ref is not null and " + _in("status", OPEN_STATUSES)),
    )

    op.create_table(
        "business_ticket_replies",
        *_base("business_ticket_replies"),
        sa.Column("ticket_id", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generator", sa.String(20), nullable=False),
        sa.Column("external_ref", sa.String(80), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(160), nullable=True),
        sa.Column("requested_at", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_ticket_replies", "ticket_id", "business_support_tickets", cascade=True),
        sa.CheckConstraint(_in("channel", ("email", "whatsapp")), name="ck_business_ticket_replies_channel"),
        sa.CheckConstraint(_in("status", ("draft", "pending_approval", "sent", "failed", "discarded")), name="ck_business_ticket_replies_status"),
        sa.CheckConstraint(_in("generator", ("person", "agent", "agent_edited")), name="ck_business_ticket_replies_generator"),
    )
    op.create_index("ix_business_ticket_replies_ticket", "business_ticket_replies", ["tenant_id", "workspace_id", "ticket_id", "created_at"])

    # The ticket's activity trail reuses business_activities (nullable column, scoped reference).
    op.add_column("business_activities", sa.Column("ticket_id", sa.String(80), nullable=True))
    op.create_foreign_key(
        "fk_business_activities_ticket_id", "business_activities", "business_support_tickets",
        ["tenant_id", "workspace_id", "ticket_id"], ["tenant_id", "workspace_id", "id"], ondelete="CASCADE",
    )
    op.create_index(
        "ix_business_activities_ticket", "business_activities", ["tenant_id", "workspace_id", "ticket_id", "created_at"],
        postgresql_where=sa.text("ticket_id is not null"),
    )
    # The support policy; null means the defaults (Sunday to Thursday, 09:00 to 17:00, Asia/Riyadh).
    op.add_column("business_settings", sa.Column("support_policy", postgresql.JSONB(), nullable=True))

    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    op.drop_column("business_settings", "support_policy")
    op.drop_index("ix_business_activities_ticket", table_name="business_activities")
    op.drop_constraint("fk_business_activities_ticket_id", "business_activities", type_="foreignkey")
    op.drop_column("business_activities", "ticket_id")
    for table in reversed(TABLES):
        op.drop_table(table)
