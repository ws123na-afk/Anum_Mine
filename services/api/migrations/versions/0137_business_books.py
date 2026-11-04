"""Business books: expenses, payables, budgets and the P&L inputs (expand only). docs/business-books.md#storage.

The books extend Business accounting; they are **not** a second finance system. Revenue stays the
existing invoices, payments and credit notes (``0089``, ``0096``); money paid from a person's own
Finance ledger reaches the business only as an allocation row (``business_ledger_allocations``,
``0134``). These tables hold what neither can express, inside one business workspace:

* ``business_book_settings``: one row per business: base currency, country, first month of the
  financial year, accrual or cash basis, the tax label, how allocation categories map to P&L
  sections, and how old an exchange rate may be.
* ``business_book_cost_centres``: codes the business reports by.
* ``business_book_vendors``: suppliers (name, tax number, country, default category). No personal
  contact details are kept.
* ``business_book_bills``: vendor bills (payables): reference, dates, currency, net, tax and total
  kept apart, category, P&L section, cost centre, project, recurrence, useful life of a capital
  item. ``status`` is ``open`` or ``void``; paid or part paid is worked out from live payments.
* ``business_book_bill_payments``: a bill's payments: an allocation row of this business (money a
  member paid from their own ledger, never posted again) or a payment recorded by hand (cash or a
  bank transfer outside ANUM, like an invoice payment by hand). One allocation pays one bill.
* ``business_book_allocation_tags``: how the business classifies an allocation row (cost centre,
  section, or ``excluded``) without touching the row or the person's ledger.
* ``business_book_budgets``: a budget per month, quarter, half year or year, by section, category,
  project or cost centre.
* ``business_book_rates``: exchange rates the business uses, each with its date and source. ANUM
  never invents a rate; a figure without one is shown as not converted.
* ``business_book_adjustments``: journal adjustments only for what the invoices and allocations
  cannot express (accruals, prepayments, capital items and their depreciation, loans and their
  interest, owner contributions, drawings and reimbursements). They never change: a mistake is
  voided with a reason (a trigger refuses any other update), so the trail stays.
* ``business_book_intercompany``: an entry between two businesses the same person manages (a
  recharge, a loan or a settlement), written in both workspaces with the same ``pair_id`` and the
  direction mirrored.

Row level security (forced on every table): ``tenant_isolation_<table>`` on ``anum.tenant_id``
and ``anum.workspace_id``, and ``lifecycle_purge_<table>`` with the 0079 grants. These are
business records: every member of the workspace with Business access reads them; the API
checks ``business:read``, ``business:write`` and ``business:manage`` per route.

Expand only: new tables, a trigger function and indexes. The previous release ignores them.
Numbered 0137 and chained after ``0136_purchases``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0137_business_books"
down_revision = "0136_purchases"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "business_book_settings",
    "business_book_cost_centres",
    "business_book_vendors",
    "business_book_bills",
    "business_book_bill_payments",
    "business_book_allocation_tags",
    "business_book_budgets",
    "business_book_rates",
    "business_book_adjustments",
    "business_book_intercompany",
)
SECTIONS = ("cost_of_sales", "operating_expense", "capital")
TAG_SECTIONS = (*SECTIONS, "excluded")
ADJUSTMENT_KINDS = ("accrual", "prepayment", "capital_item", "loan_received", "loan_repayment", "loan_interest",
                    "owner_contribution", "owner_drawing", "owner_reimbursement")
CURRENCY = "~ '^[A-Z]{3}$'"
MONEY = sa.Numeric(20, 4)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _base(table: str) -> list[object]:
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{table}_scope_id"),
    ]


def _ref(table: str, column: str, target: str, *, cascade: bool = False) -> sa.ForeignKeyConstraint:
    """A composite foreign key: a row never points into another tenant or workspace."""
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column], [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"],
        name=f"fk_{table}_{column}", ondelete="CASCADE" if cascade else None,
    )


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({PREDICATE}) with check ({PREDICATE})")
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to {PURGER} "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def upgrade() -> None:
    op.create_table(
        "business_book_settings",
        *_base("business_book_settings"),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("fiscal_year_start_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("basis", sa.String(10), nullable=False, server_default="accrual"),
        sa.Column("tax_label", sa.String(40), nullable=False, server_default=""),
        sa.Column("category_sections", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rate_max_age_days", sa.Integer(), nullable=False, server_default="31"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_book_settings_workspace"),
        sa.CheckConstraint(f"base_currency {CURRENCY}", name="ck_business_book_settings_currency"),
        sa.CheckConstraint("country is null or country ~ '^[A-Z]{2}$'", name="ck_business_book_settings_country"),
        sa.CheckConstraint("fiscal_year_start_month between 1 and 12", name="ck_business_book_settings_fy"),
        sa.CheckConstraint("basis in ('accrual', 'cash')", name="ck_business_book_settings_basis"),
        sa.CheckConstraint("rate_max_age_days between 1 and 366", name="ck_business_book_settings_rate_age"),
        sa.CheckConstraint("jsonb_typeof(category_sections) = 'object'", name="ck_business_book_settings_sections"),
    )

    op.create_table(
        "business_book_cost_centres",
        *_base("business_book_cost_centres"),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("code ~ '^[A-Za-z0-9._-]{1,20}$'", name="ck_business_book_cost_centres_code"),
    )
    op.execute("create unique index uq_business_book_cost_centres_code on business_book_cost_centres (tenant_id, workspace_id, lower(code))")

    op.create_table(
        "business_book_vendors",
        *_base("business_book_vendors"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("tax_number", sa.String(60), nullable=False, server_default=""),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("default_category", sa.String(40), nullable=True),
        sa.Column("default_section", sa.String(20), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "name_key", name="uq_business_book_vendors_name"),
        sa.CheckConstraint("country is null or country ~ '^[A-Z]{2}$'", name="ck_business_book_vendors_country"),
        sa.CheckConstraint(f"default_section is null or {_in('default_section', SECTIONS)}", name="ck_business_book_vendors_section"),
    )

    op.create_table(
        "business_book_bills",
        *_base("business_book_bills"),
        sa.Column("vendor_id", sa.String(80), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False, server_default=""),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("tax", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("recurrence", sa.String(10), nullable=True),
        sa.Column("useful_life_months", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("idem_key", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_book_bills", "vendor_id", "business_book_vendors"),
        _ref("business_book_bills", "cost_centre_id", "business_book_cost_centres"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_bills_currency"),
        sa.CheckConstraint("subtotal >= 0 and tax >= 0 and total > 0 and total = subtotal + tax", name="ck_business_book_bills_money"),
        sa.CheckConstraint("due_date >= bill_date", name="ck_business_book_bills_due"),
        sa.CheckConstraint(_in("section", SECTIONS), name="ck_business_book_bills_section"),
        sa.CheckConstraint("recurrence is null or recurrence in ('monthly', 'quarterly', 'yearly')", name="ck_business_book_bills_recurrence"),
        sa.CheckConstraint("useful_life_months is null or (section = 'capital' and useful_life_months between 1 and 600)",
                           name="ck_business_book_bills_life"),
        sa.CheckConstraint("status in ('open', 'void')", name="ck_business_book_bills_status"),
        sa.CheckConstraint("(status = 'void') = (voided_at is not null and voided_by is not null)", name="ck_business_book_bills_void"),
    )
    op.create_index("ix_business_book_bills_date", "business_book_bills", ["tenant_id", "workspace_id", "bill_date"])
    op.execute("create unique index uq_business_book_bills_idem on business_book_bills (tenant_id, workspace_id, idem_key) where idem_key is not null")

    op.create_table(
        "business_book_bill_payments",
        *_base("business_book_bill_payments"),
        sa.Column("bill_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("allocation_id", sa.String(80), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("recorded_by", sa.String(160), nullable=False),
        sa.Column("idem_key", sa.String(64), nullable=True),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_book_bill_payments", "bill_id", "business_book_bills", cascade=True),
        sa.CheckConstraint("kind in ('recorded', 'allocation')", name="ck_business_book_bill_payments_kind"),
        sa.CheckConstraint("(kind = 'allocation') = (allocation_id is not null)", name="ck_business_book_bill_payments_allocation"),
        sa.CheckConstraint("amount > 0", name="ck_business_book_bill_payments_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_bill_payments_currency"),
        sa.CheckConstraint("method in ('allocation', 'bank_transfer', 'cash', 'card', 'cheque', 'other')", name="ck_business_book_bill_payments_method"),
        sa.CheckConstraint("(voided_at is null) = (voided_by is null)", name="ck_business_book_bill_payments_void"),
    )
    op.create_index("ix_business_book_bill_payments_bill", "business_book_bill_payments", ["tenant_id", "workspace_id", "bill_id"])
    # One allocation pays one bill; a voided payment frees it. A retried request finds its payment.
    op.execute("create unique index uq_business_book_bill_payments_allocation on business_book_bill_payments "
               "(tenant_id, workspace_id, allocation_id) where allocation_id is not null and voided_at is null")
    op.execute("create unique index uq_business_book_bill_payments_idem on business_book_bill_payments "
               "(tenant_id, workspace_id, bill_id, idem_key) where idem_key is not null")

    op.create_table(
        "business_book_allocation_tags",
        *_base("business_book_allocation_tags"),
        sa.Column("allocation_id", sa.String(80), nullable=False),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("section", sa.String(20), nullable=True),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _ref("business_book_allocation_tags", "cost_centre_id", "business_book_cost_centres"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "allocation_id", name="uq_business_book_allocation_tags_allocation"),
        sa.CheckConstraint(f"section is null or {_in('section', TAG_SECTIONS)}", name="ck_business_book_allocation_tags_section"),
    )

    op.create_table(
        "business_book_budgets",
        *_base("business_book_budgets"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("section", sa.String(20), nullable=True),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _ref("business_book_budgets", "cost_centre_id", "business_book_cost_centres"),
        sa.CheckConstraint("extract(day from period_start) = 1", name="ck_business_book_budgets_start"),
        sa.CheckConstraint("months in (1, 3, 6, 12)", name="ck_business_book_budgets_months"),
        sa.CheckConstraint("amount > 0", name="ck_business_book_budgets_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_budgets_currency"),
        sa.CheckConstraint(f"section is null or {_in('section', SECTIONS)}", name="ck_business_book_budgets_section"),
    )

    op.create_table(
        "business_book_rates",
        *_base("business_book_rates"),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "base", "quote", "as_of", name="uq_business_book_rates_day"),
        sa.CheckConstraint(f"base {CURRENCY} and quote {CURRENCY} and base <> quote", name="ck_business_book_rates_codes"),
        sa.CheckConstraint("rate > 0", name="ck_business_book_rates_rate"),
        sa.CheckConstraint("length(trim(source)) >= 3", name="ck_business_book_rates_source"),
    )

    op.create_table(
        "business_book_adjustments",
        *_base("business_book_adjustments"),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("section", sa.String(20), nullable=True),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("start_month", sa.Date(), nullable=True),
        sa.Column("months", sa.Integer(), nullable=True),
        sa.Column("reverse_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("idem_key", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_book_adjustments", "cost_centre_id", "business_book_cost_centres"),
        sa.CheckConstraint(_in("kind", ADJUSTMENT_KINDS), name="ck_business_book_adjustments_kind"),
        sa.CheckConstraint("amount > 0", name="ck_business_book_adjustments_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_adjustments_currency"),
        sa.CheckConstraint(f"section is null or {_in('section', SECTIONS)}", name="ck_business_book_adjustments_section"),
        sa.CheckConstraint("length(trim(description)) >= 3", name="ck_business_book_adjustments_description"),
        sa.CheckConstraint("(kind in ('prepayment', 'capital_item')) = (start_month is not null and months is not null)",
                           name="ck_business_book_adjustments_spread"),
        sa.CheckConstraint("start_month is null or extract(day from start_month) = 1", name="ck_business_book_adjustments_start"),
        sa.CheckConstraint("months is null or months between 1 and 600", name="ck_business_book_adjustments_months"),
        sa.CheckConstraint("reverse_on is null or (kind = 'accrual' and reverse_on > entry_date)", name="ck_business_book_adjustments_reverse"),
        sa.CheckConstraint("status in ('active', 'void')", name="ck_business_book_adjustments_status"),
        sa.CheckConstraint("(status = 'void') = (voided_at is not null and voided_by is not null)", name="ck_business_book_adjustments_void"),
    )
    op.create_index("ix_business_book_adjustments_date", "business_book_adjustments", ["tenant_id", "workspace_id", "entry_date"])
    op.execute("create unique index uq_business_book_adjustments_idem on business_book_adjustments (tenant_id, workspace_id, idem_key) "
               "where idem_key is not null")
    # The trail never changes: only an active adjustment may become void, and nothing else on it moves.
    op.execute(
        """
        create function business_book_adjustments_frozen() returns trigger language plpgsql as $$
        begin
          if old.status <> 'active' or new.status <> 'void'
             or (new.id, new.tenant_id, new.workspace_id, new.kind, new.entry_date, new.currency, new.amount, new.section,
                 new.category, new.cost_centre_id, new.project_id, new.description, new.start_month, new.months,
                 new.reverse_on, new.idem_key, new.created_by, new.created_at)
                is distinct from
                (old.id, old.tenant_id, old.workspace_id, old.kind, old.entry_date, old.currency, old.amount, old.section,
                 old.category, old.cost_centre_id, old.project_id, old.description, old.start_month, old.months,
                 old.reverse_on, old.idem_key, old.created_by, old.created_at) then
            raise exception 'a business adjustment never changes: void it with a reason and record a new one'
              using errcode = 'check_violation';
          end if;
          return new;
        end
        $$
        """
    )
    op.execute("create trigger business_book_adjustments_frozen before update on business_book_adjustments "
               "for each row execute function business_book_adjustments_frozen()")

    op.create_table(
        "business_book_intercompany",
        *_base("business_book_intercompany"),
        sa.Column("pair_id", sa.String(80), nullable=False),
        sa.Column("counterparty_workspace_id", sa.String(80), nullable=False),
        sa.Column("counterparty_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        sa.UniqueConstraint("tenant_id", "workspace_id", "pair_id", name="uq_business_book_intercompany_pair"),
        sa.CheckConstraint("kind in ('recharge', 'loan', 'settlement')", name="ck_business_book_intercompany_kind"),
        sa.CheckConstraint("direction in ('receivable', 'payable')", name="ck_business_book_intercompany_direction"),
        sa.CheckConstraint("counterparty_workspace_id <> workspace_id", name="ck_business_book_intercompany_other"),
        sa.CheckConstraint("amount > 0", name="ck_business_book_intercompany_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_intercompany_currency"),
        sa.CheckConstraint("length(trim(description)) >= 3", name="ck_business_book_intercompany_description"),
        sa.CheckConstraint("status in ('active', 'void')", name="ck_business_book_intercompany_status"),
        sa.CheckConstraint("(status = 'void') = (voided_at is not null and voided_by is not null)", name="ck_business_book_intercompany_void"),
    )
    op.create_index("ix_business_book_intercompany_date", "business_book_intercompany", ["tenant_id", "workspace_id", "entry_date"])

    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists business_book_adjustments_frozen()")
