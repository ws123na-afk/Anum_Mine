"""Business: invoices, manual payments, invoice numbering and the customer portal (docs/business.md#invoices, docs/business-portal.md).

Five workspace tables under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``
(``tenant_isolation_<table>``, the 0040 pattern, as in ``0070_business`` and ``0080``):

* ``business_invoices``: an invoice for one client account (and optionally one project)
  with priced lines, computed totals, the number the owner's approval assigned, the seller
  and buyer details as they were when it was sent, and the status (draft, pending approval,
  sent, paid, void). A trigger keeps a sent, paid or voided invoice's content and number
  from changing and a voided invoice void.
* ``business_invoice_payments``: payments a person recorded by hand against an invoice.
* ``business_invoice_counters``: the last number issued per workspace and year, locked
  while the next number is taken.
* ``business_portal_access``: a customer contact's access link, stored as the SHA-256 of
  its secret (the secret itself is never stored), with expiry and revocation.
* ``business_portal_decisions``: what a customer decided about one exact proposal or
  change order version; one decision per version.

One nullable column on ``business_settings``: the owner's billing profile (seller name,
tax number, bank details, numbering prefix and defaults).

References carry the scope (``(tenant_id, workspace_id, <id>)`` composite foreign keys), so
a row can never point into another tenant or workspace.

Organisation and workspace deletion (migration ``0079_tenant_lifecycle``): every table gets
the ``anum_tenant_purger`` grants (scope columns only, plus delete) and the purge policy
``lifecycle_purge_<table>``, copied from 0080. No other role is granted anything.

Expand only: five new tables, one nullable column and indexes. The previous release
ignores them.

Numbered 0089 and chained after ``0088_approvals_paging``, the head of ``main`` (it builds on
``0080_business_change_orders``, already in that chain).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0089_business_invoices_portal"
down_revision = "0088_approvals_paging"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "business_invoices",
    "business_invoice_payments",
    "business_invoice_counters",
    "business_portal_access",
    "business_portal_decisions",
)
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)
CURRENCIES = ("SAR", "AED", "USD")


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
    """Deletion of the organisation or of one workspace may remove these rows (0079's ``_purge_policy``)."""
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
    op.add_column("business_settings", sa.Column("billing_profile", postgresql.JSONB(), nullable=True))

    op.create_table(
        "business_invoices",
        *_base("business_invoices"),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("subtotal", MONEY, nullable=False, server_default="0"),
        sa.Column("tax", MONEY, nullable=False, server_default="0"),
        sa.Column("total", MONEY, nullable=False, server_default="0"),
        sa.Column("amount_paid", MONEY, nullable=False, server_default="0"),
        sa.Column("due_in_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("number", sa.String(40), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("customer_reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_terms", sa.Text(), nullable=False, server_default=""),
        sa.Column("bill_to_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("bill_to_address", sa.Text(), nullable=False, server_default=""),
        sa.Column("bill_to_email", sa.String(254), nullable=False, server_default=""),
        sa.Column("bill_to_tax_number", sa.String(60), nullable=False, server_default=""),
        sa.Column("seller", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("requested_hash", sa.String(64), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=True),
        sa.Column("approval_task_id", sa.String(80), nullable=True),
        sa.Column("requested_by", sa.String(160), nullable=True),
        sa.Column("requested_at", TS, nullable=True),
        sa.Column("sent_by", sa.String(160), nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("paid_at", TS, nullable=True),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("marked_milestones", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decision_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_invoices", "account_id", "business_accounts", cascade=False),
        _ref("business_invoices", "project_id", "business_projects", cascade=False),
        sa.CheckConstraint(_in("status", ("draft", "pending_approval", "sent", "paid", "void")), name="ck_business_invoices_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_business_invoices_currency"),
        sa.CheckConstraint("subtotal >= 0 and tax >= 0 and total >= 0 and amount_paid >= 0 and total = subtotal + tax", name="ck_business_invoices_amounts"),
        sa.CheckConstraint("amount_paid <= total", name="ck_business_invoices_paid_within_total"),
        # A sent, paid or void invoice has its number, dates, hash and sender.
        sa.CheckConstraint(
            "status in ('draft', 'pending_approval') or (number is not null and seq is not null and issue_date is not null "
            "and due_date is not null and content_hash is not null and sent_at is not null and sent_by is not null)",
            name="ck_business_invoices_issued",
        ),
        sa.CheckConstraint("status <> 'paid' or (amount_paid = total and paid_at is not null)", name="ck_business_invoices_paid"),
        sa.CheckConstraint("status <> 'void' or (voided_at is not null and voided_by is not null)", name="ck_business_invoices_void"),
    )
    op.create_index("ix_business_invoices_account", "business_invoices", ["tenant_id", "workspace_id", "account_id", "updated_at"])
    op.create_index("ix_business_invoices_project", "business_invoices", ["tenant_id", "workspace_id", "project_id"])
    op.create_index("ix_business_invoices_status_due", "business_invoices", ["tenant_id", "workspace_id", "status", "due_date"])
    op.create_index("ix_business_invoices_scope_updated_at", "business_invoices", ["tenant_id", "workspace_id", "updated_at", "id"])
    op.create_index(
        "uq_business_invoices_number", "business_invoices", ["tenant_id", "workspace_id", "number"],
        unique=True, postgresql_where=sa.text("number is not null"),
    )
    # A sent, paid or void invoice never changes what it says, and a void invoice stays void.
    op.execute(
        """
        create function business_invoice_frozen() returns trigger
        language plpgsql security invoker set search_path = pg_catalog, public as $$
        begin
          if old.status = 'void' then
            raise exception 'business invoice % is void', old.id using errcode = 'check_violation';
          end if;
          if (new.account_id, new.project_id, new.currency, new.lines, new.subtotal, new.tax, new.total, new.seq, new.number,
              new.issue_date, new.due_date, new.customer_reference, new.notes, new.payment_terms, new.bill_to_name,
              new.bill_to_address, new.bill_to_email, new.bill_to_tax_number, new.seller, new.content_hash)
             is distinct from
             (old.account_id, old.project_id, old.currency, old.lines, old.subtotal, old.tax, old.total, old.seq, old.number,
              old.issue_date, old.due_date, old.customer_reference, old.notes, old.payment_terms, old.bill_to_name,
              old.bill_to_address, old.bill_to_email, old.bill_to_tax_number, old.seller, old.content_hash) then
            raise exception 'business invoice % was sent and cannot change', old.id using errcode = 'check_violation';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger business_invoices_frozen before update on business_invoices "
        "for each row when (old.status in ('sent', 'paid', 'void')) execute function business_invoice_frozen()"
    )

    op.create_table(
        "business_invoice_payments",
        *_base("business_invoice_payments"),
        sa.Column("invoice_id", sa.String(80), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("recorded_by", sa.String(160), nullable=False),
        _ref("business_invoice_payments", "invoice_id", "business_invoices", cascade=True),
        sa.CheckConstraint("amount > 0", name="ck_business_invoice_payments_amount"),
        sa.CheckConstraint(_in("method", ("bank_transfer", "cash", "card", "cheque", "other")), name="ck_business_invoice_payments_method"),
    )
    op.create_index("ix_business_invoice_payments_invoice", "business_invoice_payments", ["tenant_id", "workspace_id", "invoice_id", "paid_on"])

    op.create_table(
        "business_invoice_counters",
        *_base("business_invoice_counters"),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "year", name="uq_business_invoice_counters_year"),
        sa.CheckConstraint("year between 2000 and 9999 and last_seq >= 0", name="ck_business_invoice_counters_values"),
    )

    op.create_table(
        "business_portal_access",
        *_base("business_portal_access"),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("contact_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("can_decide", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_by", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _ref("business_portal_access", "account_id", "business_accounts", cascade=True),
        _ref("business_portal_access", "contact_id", "business_contacts", cascade=True),
        sa.CheckConstraint(_in("status", ("active", "revoked")), name="ck_business_portal_access_status"),
        sa.CheckConstraint("status <> 'revoked' or (revoked_at is not null and revoked_by is not null)", name="ck_business_portal_access_revoked"),
    )
    op.create_index("uq_business_portal_access_token", "business_portal_access", ["token_hash"], unique=True)
    op.create_index("ix_business_portal_access_account", "business_portal_access", ["tenant_id", "workspace_id", "account_id", "created_at"])

    op.create_table(
        "business_portal_decisions",
        *_base("business_portal_decisions"),
        sa.Column("access_id", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("contact_id", sa.String(80), nullable=False),
        sa.Column("subject_kind", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("signer_name", sa.String(160), nullable=False),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("decided_at", TS, nullable=False),
        _ref("business_portal_decisions", "access_id", "business_portal_access", cascade=True),
        _ref("business_portal_decisions", "account_id", "business_accounts", cascade=True),
        sa.UniqueConstraint("tenant_id", "workspace_id", "subject_kind", "subject_id", "content_hash", name="uq_business_portal_decisions_version"),
        sa.CheckConstraint(_in("subject_kind", ("proposal", "change_order")), name="ck_business_portal_decisions_kind"),
        sa.CheckConstraint(_in("decision", ("approved", "declined")), name="ck_business_portal_decisions_decision"),
    )
    op.create_index("ix_business_portal_decisions_subject", "business_portal_decisions", ["tenant_id", "workspace_id", "subject_id", "decided_at"])

    for table in TABLES:
        _enable_rls(table)
        _purge_policy(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists business_invoice_frozen()")
    op.drop_column("business_settings", "billing_profile")
