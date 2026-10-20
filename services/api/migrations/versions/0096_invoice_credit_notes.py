"""Business invoices: credit notes and customer payment notices (docs/business.md#credit-notes, docs/business-portal.md#reporting-a-payment).

Two workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the pattern of ``0089_business_invoices_portal``):

* ``business_credit_notes``: money an owner credits against a sent invoice. A sent invoice's
  content never changes, so a credit note is its own record and the invoice carries only the
  running total in the new ``amount_credited`` column. A credit note keeps its number; one
  issued by mistake is voided, never deleted.
* ``business_payment_notices``: what a customer says they paid, reported from the portal link.
  A notice moves no money and changes no balance: an owner confirms it (which records an
  ordinary payment) or rejects it.

One new column on ``business_invoices`` (``amount_credited``, default 0) and a looser paid
check: an invoice is paid when payments plus credits equal the total.

Expand/contract: this migration only widens. A replica from before it ignores the new column and
tables, and the looser check accepts everything the old check accepted. The one thing an old
replica cannot read correctly is an invoice that has credits (it computes the balance as total
minus payments). So credit notes are off (``ANUM_BUSINESS_CREDIT_NOTES_ENABLED``, default false)
until every replica runs a release that knows ``amount_credited``; no row has a credit while the
old code can run, whatever the rollback. Turning the setting on is the contract step
(docs/business.md#credit-notes). Rollback never reverses this migration; with the setting off it is
safe to leave.

Organisation and workspace deletion (migration ``0079_tenant_lifecycle``): both tables get the
``anum_tenant_purger`` grants (scope columns only, plus delete) and the purge policy, copied
from 0089. No other role is granted anything.

Numbered 0096 and chained after ``0095_agent_controls``, the head of ``main``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0096_invoice_credit_notes"
down_revision = "0095_agent_controls"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_credit_notes", "business_payment_notices")
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


def _purge_policy(table: str) -> None:
    """Deletion of the organisation or of one workspace may remove these rows (0089's ``_purge_policy``)."""
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
    op.add_column("business_invoices", sa.Column("amount_credited", MONEY, nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_business_invoices_credited", "business_invoices", "amount_credited >= 0 and amount_paid + amount_credited <= total"
    )
    # Paid means settled: payments plus credits cover the total (credits are 0 for every existing row).
    op.drop_constraint("ck_business_invoices_paid", "business_invoices", type_="check")
    op.create_check_constraint(
        "ck_business_invoices_paid", "business_invoices", "status <> 'paid' or (amount_paid + amount_credited = total and paid_at is not null)"
    )

    op.create_table(
        "business_credit_notes",
        *_base("business_credit_notes"),
        sa.Column("invoice_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(60), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("tax", MONEY, nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("issued_by", sa.String(160), nullable=False),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_credit_notes", "invoice_id", "business_invoices", cascade=False),
        _ref("business_credit_notes", "account_id", "business_accounts", cascade=False),
        sa.CheckConstraint(_in("status", ("issued", "void")), name="ck_business_credit_notes_status"),
        sa.CheckConstraint("amount > 0 and subtotal >= 0 and tax >= 0 and amount = subtotal + tax", name="ck_business_credit_notes_amounts"),
        sa.CheckConstraint("seq >= 1", name="ck_business_credit_notes_seq"),
        sa.CheckConstraint("status <> 'void' or (voided_at is not null and voided_by is not null)", name="ck_business_credit_notes_void"),
    )
    op.create_index("ix_business_credit_notes_invoice", "business_credit_notes", ["tenant_id", "workspace_id", "invoice_id", "seq"])
    op.create_index("ix_business_credit_notes_account", "business_credit_notes", ["tenant_id", "workspace_id", "account_id", "updated_at"])
    op.create_index("uq_business_credit_notes_number", "business_credit_notes", ["tenant_id", "workspace_id", "number"], unique=True)

    op.create_table(
        "business_payment_notices",
        *_base("business_payment_notices"),
        sa.Column("invoice_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("access_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("payer_name", sa.String(160), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decision_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("payment_id", sa.String(80), nullable=True),
        sa.Column("client_key", sa.String(80), nullable=True),
        _ref("business_payment_notices", "invoice_id", "business_invoices", cascade=True),
        _ref("business_payment_notices", "account_id", "business_accounts", cascade=True),
        sa.CheckConstraint("amount > 0", name="ck_business_payment_notices_amount"),
        sa.CheckConstraint(_in("method", ("bank_transfer", "cash", "card", "cheque", "other")), name="ck_business_payment_notices_method"),
        sa.CheckConstraint(_in("status", ("pending", "confirmed", "rejected")), name="ck_business_payment_notices_status"),
        sa.CheckConstraint(
            "status = 'pending' or (decided_by is not null and decided_at is not null)", name="ck_business_payment_notices_decided"
        ),
        sa.CheckConstraint("status <> 'confirmed' or payment_id is not null", name="ck_business_payment_notices_confirmed"),
    )
    op.create_index("ix_business_payment_notices_invoice", "business_payment_notices", ["tenant_id", "workspace_id", "invoice_id", "created_at"])
    # One report per submit: a repeat with the same client key finds the first.
    op.create_index(
        "uq_business_payment_notices_key", "business_payment_notices", ["tenant_id", "workspace_id", "invoice_id", "client_key"],
        unique=True, postgresql_where=sa.text("client_key is not null"),
    )
    op.create_index("ix_business_payment_notices_status", "business_payment_notices", ["tenant_id", "workspace_id", "status", "created_at"])

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_constraint("ck_business_invoices_paid", "business_invoices", type_="check")
    op.create_check_constraint("ck_business_invoices_paid", "business_invoices", "status <> 'paid' or (amount_paid = total and paid_at is not null)")
    op.drop_constraint("ck_business_invoices_credited", "business_invoices", type_="check")
    op.drop_column("business_invoices", "amount_credited")
