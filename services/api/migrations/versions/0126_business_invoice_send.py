"""Business invoices: emailing an invoice, payment reminders, payment links and idempotent
payments (docs/business.md#emailing-an-invoice-and-reminders, #payment-links, #payments-by-hand).

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the pattern of ``0096_invoice_credit_notes``):

* ``business_invoice_messages``: each invoice email and payment reminder a person asked for.
  Nothing here sends anything: the message is a delivery task that waits for an approval of
  its exact payload; the row records the request, the approval and the task, and what came of
  them. A partial unique index allows one live request per reminder number.
* ``business_invoice_payment_links``: a hosted payment page (Moyasar) made for a sent invoice's
  balance. ANUM stores the provider's invoice id and the page address only; card details are
  entered on the provider's page and never reach ANUM.

Three nullable columns on ``business_invoice_payments``: ``idempotency_key`` (one payment per
key per invoice, so a retried form never records money twice), and ``provider`` with
``provider_payment_id`` (a payment Moyasar reported, recorded once per provider payment).

Expand only: a replica from before this migration ignores the new tables and columns and never
writes them (the new columns are nullable). Rollback never reverses it.

Organisation and workspace deletion (migration ``0079_tenant_lifecycle``): both tables get the
``anum_tenant_purger`` grants (scope columns only, plus delete) and the purge policy, copied
from 0096, and are listed in ``anum_api/tenant_lifecycle_tables.py``. No other role is granted
anything.

Numbered 0126 and chained after ``0125_unify_whatsapp_leads``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0126_business_invoice_send"
down_revision = "0125_unify_whatsapp_leads"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_invoice_messages", "business_invoice_payment_links")
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)


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
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "invoice_id"],
            ["business_invoices.tenant_id", "business_invoices.workspace_id", "business_invoices.id"],
            name=f"fk_{table}_invoice_id", ondelete="CASCADE",
        ),
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
    """Deletion of the organisation or of one workspace may remove these rows (0096's ``_purge_policy``)."""
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
    op.add_column("business_invoice_payments", sa.Column("idempotency_key", sa.String(80), nullable=True))
    op.add_column("business_invoice_payments", sa.Column("provider", sa.String(20), nullable=True))
    op.add_column("business_invoice_payments", sa.Column("provider_payment_id", sa.String(200), nullable=True))
    op.create_check_constraint(
        "ck_business_invoice_payments_provider", "business_invoice_payments",
        "(provider is null and provider_payment_id is null) or (provider = 'moyasar' and provider_payment_id is not null)",
    )
    op.create_index(
        "uq_business_invoice_payments_key", "business_invoice_payments", ["tenant_id", "workspace_id", "invoice_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key is not null"),
    )
    op.create_index(
        "uq_business_invoice_payments_provider", "business_invoice_payments", ["tenant_id", "workspace_id", "provider", "provider_payment_id"],
        unique=True, postgresql_where=sa.text("provider_payment_id is not null"),
    )

    op.create_table(
        "business_invoice_messages",
        sa.Column("invoice_id", sa.String(80), nullable=False),
        *_base("business_invoice_messages"),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("reminder_number", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("detail", sa.String(500), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("kind", ("invoice", "reminder")), name="ck_business_invoice_messages_kind"),
        sa.CheckConstraint(_in("language", ("en", "ar")), name="ck_business_invoice_messages_language"),
        sa.CheckConstraint(_in("status", ("waiting_approval", "sent", "rejected", "failed", "blocked")), name="ck_business_invoice_messages_status"),
        sa.CheckConstraint(
            "(kind = 'reminder' and reminder_number >= 1) or (kind = 'invoice' and reminder_number is null)",
            name="ck_business_invoice_messages_reminder",
        ),
        sa.CheckConstraint("status <> 'waiting_approval' or approval_id is not null", name="ck_business_invoice_messages_waiting"),
    )
    op.create_index("ix_business_invoice_messages_invoice", "business_invoice_messages", ["tenant_id", "workspace_id", "invoice_id", "created_at"])
    # One live request per reminder number: a second press waits on the first.
    op.create_index(
        "uq_business_invoice_messages_reminder", "business_invoice_messages", ["tenant_id", "workspace_id", "invoice_id", "reminder_number"],
        unique=True, postgresql_where=sa.text("kind = 'reminder' and status in ('waiting_approval', 'sent')"),
    )

    op.create_table(
        "business_invoice_payment_links",
        sa.Column("invoice_id", sa.String(80), nullable=False),
        *_base("business_invoice_payment_links"),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_invoice_id", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("provider", ("moyasar",)), name="ck_business_invoice_payment_links_provider"),
        sa.CheckConstraint(_in("status", ("open", "paid", "replaced")), name="ck_business_invoice_payment_links_status"),
        sa.CheckConstraint("amount > 0", name="ck_business_invoice_payment_links_amount"),
        sa.CheckConstraint("url like 'https://%'", name="ck_business_invoice_payment_links_url"),
    )
    op.create_index("ix_business_invoice_payment_links_invoice", "business_invoice_payment_links", ["tenant_id", "workspace_id", "invoice_id", "created_at"])
    op.create_index(
        "uq_business_invoice_payment_links_provider", "business_invoice_payment_links", ["tenant_id", "workspace_id", "provider", "provider_invoice_id"],
        unique=True,
    )

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_index("uq_business_invoice_payments_provider", table_name="business_invoice_payments")
    op.drop_index("uq_business_invoice_payments_key", table_name="business_invoice_payments")
    op.drop_constraint("ck_business_invoice_payments_provider", "business_invoice_payments", type_="check")
    op.drop_column("business_invoice_payments", "provider_payment_id")
    op.drop_column("business_invoice_payments", "provider")
    op.drop_column("business_invoice_payments", "idempotency_key")
