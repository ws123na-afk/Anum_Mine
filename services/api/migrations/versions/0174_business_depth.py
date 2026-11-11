"""Business depth: recurring invoices, planned reminders and customer replies in the portal (expand only).

docs/business.md#recurring-invoices, #reminders-planned-by-the-daily-pass, docs/business-portal.md#replying-inside-a-request.

Three workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id`` (``tenant_isolation_<table>``,
the pattern of ``0126_business_invoice_send``), each with the ``anum_tenant_purger`` grants and purge policy of
``0079_tenant_lifecycle`` and listed in ``anum_api/tenant_lifecycle_tables.py``:

* ``business_invoice_schedules``: a recurring invoice (the account, the lines, the terms and a schedule). The
  scheduler pass drafts one invoice per period through the invoice service and, when the schedule says so, asks
  for the owner's approval to send it; nothing is sent by the schedule itself.
* ``business_invoice_automation``: one row per workspace with the owner's switches for the daily pass: plan the
  payment reminders of overdue invoices, and plan the email of an invoice a schedule made once it is sent. Each
  planned email still waits in Approvals. ``last_run_on`` is the claim that makes the pass run once a day.
* ``business_ticket_messages``: the conversation inside a support request between the customer (from the portal
  link) and the team. Nothing here is delivered anywhere: the customer reads it in the portal.

Discovery for the scheduler pass: ``anum_maintenance`` may read only the scope, status and next date of active
schedules and the scope and switches of the automation rows (the pattern of ``0138_books_v2``); the work then runs as
the application role inside each workspace's own RLS context.

Two nullable columns on ``business_invoices`` (``schedule_id``, ``period_date``) with a partial unique index: one
invoice per schedule and period, so two passes that overlap draft each period once. One nullable column on
``business_invoice_messages`` (``schedule_step``): the reminder step (1 to 4) the daily pass planned, so it asks
for each step once per invoice.

Chained after ``0173_favorites_position``. Expand only: the previous release never reads or writes the new tables
and columns (all nullable or defaulted), so it keeps working. Rollback never reverses it in production.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0174_business_depth"
down_revision = "0173_favorites_position"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_invoice_schedules", "business_invoice_automation", "business_ticket_messages")
PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)


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


def _purge_policy(table: str) -> None:
    """Deletion of the organisation or of one workspace may remove these rows (0126's ``_purge_policy``)."""
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
        "business_invoice_schedules",
        *_base("business_invoice_schedules"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("lines", JSONB(), nullable=False),
        sa.Column("due_in_days", sa.Integer(), nullable=False),
        sa.Column("customer_reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_terms", sa.Text(), nullable=False, server_default=""),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_count", sa.Integer(), nullable=True),
        sa.Column("issue_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("next_index", sa.Integer(), nullable=False),
        sa.Column("next_date", sa.Date(), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("last_period", sa.Date(), nullable=True),
        sa.Column("last_invoice_id", sa.String(80), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
        sa.Column("end_reason", sa.String(20), nullable=False, server_default=""),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("frequency", ("monthly", "quarterly", "yearly", "days", "weeks", "months")), name="ck_business_invoice_schedules_frequency"),
        sa.CheckConstraint(_in("issue_mode", ("draft", "request_approval")), name="ck_business_invoice_schedules_issue_mode"),
        sa.CheckConstraint(_in("status", ("active", "paused", "ended")), name="ck_business_invoice_schedules_status"),
        sa.CheckConstraint("interval between 1 and 366", name="ck_business_invoice_schedules_interval"),
        sa.CheckConstraint("due_in_days between 0 and 365", name="ck_business_invoice_schedules_due"),
        sa.CheckConstraint("max_count is null or max_count between 1 and 1000", name="ck_business_invoice_schedules_count"),
        sa.CheckConstraint("end_date is null or end_date >= start_date", name="ck_business_invoice_schedules_end"),
        sa.CheckConstraint("created_count >= 0 and next_index >= 0", name="ck_business_invoice_schedules_counts"),
    )
    op.create_index("ix_business_invoice_schedules_due", "business_invoice_schedules", ["tenant_id", "workspace_id", "status", "next_date"])
    op.create_index("ix_business_invoice_schedules_account", "business_invoice_schedules", ["tenant_id", "workspace_id", "account_id"])

    op.create_table(
        "business_invoice_automation",
        *_base("business_invoice_automation"),
        sa.Column("auto_reminders", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_scheduled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("last_run_on", sa.Date(), nullable=True),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("last_run_summary", JSONB(), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("language", ("en", "ar")), name="ck_business_invoice_automation_language"),
        # One row per workspace.
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_invoice_automation_workspace"),
    )

    op.create_table(
        "business_ticket_messages",
        *_base("business_ticket_messages"),
        sa.Column("ticket_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("author_name", sa.String(160), nullable=False),
        sa.Column("access_id", sa.String(80), nullable=True),
        sa.Column("contact_id", sa.String(80), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_key", sa.String(80), nullable=True),
        sa.Column("read_at", TS, nullable=True),
        sa.Column("read_by", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "ticket_id"],
            ["business_support_tickets.tenant_id", "business_support_tickets.workspace_id", "business_support_tickets.id"],
            name="fk_business_ticket_messages_ticket_id", ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("author", ("customer", "staff")), name="ck_business_ticket_messages_author"),
        sa.CheckConstraint("char_length(body) between 1 and 4000", name="ck_business_ticket_messages_body"),
        sa.CheckConstraint("author <> 'customer' or access_id is not null", name="ck_business_ticket_messages_customer"),
    )
    op.create_index("ix_business_ticket_messages_ticket", "business_ticket_messages", ["tenant_id", "workspace_id", "ticket_id", "created_at"])
    # A customer's submit sent twice (a retry) is one message.
    op.create_index(
        "uq_business_ticket_messages_key", "business_ticket_messages", ["tenant_id", "workspace_id", "ticket_id", "client_key"],
        unique=True, postgresql_where=sa.text("client_key is not null"),
    )

    op.add_column("business_invoices", sa.Column("schedule_id", sa.String(80), nullable=True))
    op.add_column("business_invoices", sa.Column("period_date", sa.Date(), nullable=True))
    op.create_index(
        "uq_business_invoices_schedule_period", "business_invoices", ["tenant_id", "workspace_id", "schedule_id", "period_date"],
        unique=True, postgresql_where=sa.text("schedule_id is not null"),
    )
    op.add_column("business_invoice_messages", sa.Column("schedule_step", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_business_invoice_messages_step", "business_invoice_messages", "schedule_step is null or schedule_step between 1 and 4")

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)

    # Discovery for the scheduler pass: scope and switches only, never an amount, a line or a customer.
    op.execute(f"grant select (tenant_id, workspace_id, status, next_date) on business_invoice_schedules to {MAINT}")
    op.execute(f"""
        create policy maintenance_business_invoice_schedules_due on business_invoice_schedules for select to {MAINT}
        using (status = 'active' and next_date is not null)
    """)
    op.execute(f"grant select (tenant_id, workspace_id, auto_reminders, email_scheduled, last_run_on) on business_invoice_automation to {MAINT}")
    op.execute(f"""
        create policy maintenance_business_invoice_automation_on on business_invoice_automation for select to {MAINT}
        using (auto_reminders or email_scheduled)
    """)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on business_invoice_schedules, business_invoice_automation from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_constraint("ck_business_invoice_messages_step", "business_invoice_messages", type_="check")
    op.drop_column("business_invoice_messages", "schedule_step")
    op.drop_index("uq_business_invoices_schedule_period", table_name="business_invoices")
    op.drop_column("business_invoices", "period_date")
    op.drop_column("business_invoices", "schedule_id")
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
