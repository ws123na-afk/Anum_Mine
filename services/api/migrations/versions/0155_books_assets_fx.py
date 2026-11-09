"""Business accounting completion: the fixed asset register, loan schedules and the period-end revaluation (expand only).

docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation.

* ``business_book_assets``: one fixed asset per capital bill or capital-item adjustment (unique per source, so registering
  twice never makes a duplicate): its cost, currency and acquisition day from the source, and how it is depreciated
  (straight line or reducing balance, useful life, residual value, start month), its disposal (sale or write-off, the
  proceeds). Existing capital bills and items are registered by the posting run and when the register is opened, with
  exactly the depreciation they posted before (straight line, no residual value, the same months), under the same event
  keys: nothing posted changes and nothing is counted twice.
* ``business_book_asset_impairments``: impairment requests and their decision by a second person (maker and checker).
* ``business_book_loans`` and ``business_book_loan_repayments``: a loan with its schedule terms (principal, currency,
  yearly rate, start, term, frequency, annuity or equal principal, what an early repayment changes), and each repayment
  with its split into interest and principal, optionally matched to the bill or the loan repayment adjustment that
  already records its cash (one match each: partial unique indexes).

Widened checks (expand: nothing that was valid becomes invalid): journal entries, lines and posting issues take the
source kinds ``asset``, ``loan``, ``loan_repayment`` and ``revaluation``; posting issues the reason
``missing_closing_rate``; bank lines the cash flow ``asset_sales``. The closed-period trigger function
``business_book_period_open`` (``create or replace``, an invoker function as before) also refuses a disposal, an
impairment decision or a loan repayment dated in a closed period, through triggers on the new tables. The chart gains
four standard accounts (4600 gains on disposal, 6150 impairment losses, 6160 losses on disposal, 7010 unrealised exchange
differences) when the posting run next runs; that needs no migration.

Row level security (forced) on every new table: ``tenant_isolation_<table>`` and ``lifecycle_purge_<table>`` with the
0079 grants, as in ``0151``. Nothing bypasses RLS; no function is ``security definer``.

Chained after ``0154_business_tax``. Rollback: the previous release ignores the new tables; it would fail to read a journal
entry of a new source kind only where it lists entries by kind, and never writes one. ``alembic downgrade
0154_business_tax`` (test databases only) restores the trigger function and the narrower checks (which fails while rows of
the new kinds exist) and drops the tables; production restores a backup.
"""

import sqlalchemy as sa
from alembic import op

revision = "0155_books_assets_fx"
down_revision = "0154_business_tax"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
PURGER = "anum_tenant_purger"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_book_assets", "business_book_asset_impairments", "business_book_loans", "business_book_loan_repayments")
TRIGGERED = ("business_book_assets", "business_book_asset_impairments", "business_book_loan_repayments")
OLD_SOURCE_KINDS = ("invoice", "credit_note", "invoice_payment", "bill", "bill_payment", "allocation", "adjustment", "intercompany", "manual", "closing")
SOURCE_KINDS = (*OLD_SOURCE_KINDS, "asset", "loan", "loan_repayment", "revaluation")
OLD_FLOWS = ("customers_paid", "suppliers_paid", "capital_spend", "loans_received", "loans_repaid", "interest_paid", "owner_contributions",
             "owner_drawings", "members_reimbursed", "intercompany_in", "intercompany_out", "intercompany_lent", "intercompany_borrowed", "other")
FLOWS = (*OLD_FLOWS, "asset_sales")
OLD_REASONS = ("missing_rate", "too_small")
REASONS = (*OLD_REASONS, "missing_closing_rate")
CURRENCY = "~ '^[A-Z]{3}$'"
# (constraint, table, column, wider values, narrower values): checks widened in place.
WIDENED = (
    ("ck_business_book_journal_entries_source", "business_book_journal_entries", "source_kind", SOURCE_KINDS, OLD_SOURCE_KINDS),
    ("ck_business_book_journal_lines_source", "business_book_journal_lines", "source_kind", SOURCE_KINDS, OLD_SOURCE_KINDS),
    ("ck_business_book_posting_issues_source", "business_book_posting_issues", "source_kind", SOURCE_KINDS, OLD_SOURCE_KINDS),
    ("ck_business_book_posting_issues_reason", "business_book_posting_issues", "reason", REASONS, OLD_REASONS),
)


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


def _ref(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    """A composite foreign key: a row never points into another tenant or workspace."""
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", column], [f"{target}.tenant_id", f"{target}.workspace_id", f"{target}.id"], name=f"fk_{table}_{column}",
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


NEW_PERIOD_OPEN = """
        create or replace function business_book_period_open() returns trigger language plpgsql as $$
        declare
            locked date;
            touched boolean := false;
        begin
            locked := business_book_locked_through(new.tenant_id, new.workspace_id);
            if locked is null then
                return new;
            end if;
            if tg_table_name = 'business_book_journal_lines' then
                touched := new.entry_date <= locked;
            elsif tg_table_name = 'business_book_bills' then
                if tg_op = 'INSERT' then
                    touched := new.bill_date <= locked and new.status <> 'void';
                elsif old.bill_date <= locked or new.bill_date <= locked then
                    touched := (new.bill_date, new.due_date, new.currency, new.subtotal, new.tax, new.total, new.category, new.section, new.status,
                                new.useful_life_months, new.project_id, new.cost_centre_id, new.vendor_id, new.tax_code, new.reverse_charge_tax)
                               is distinct from
                               (old.bill_date, old.due_date, old.currency, old.subtotal, old.tax, old.total, old.category, old.section, old.status,
                                old.useful_life_months, old.project_id, old.cost_centre_id, old.vendor_id, old.tax_code, old.reverse_charge_tax);
                end if;
            elsif tg_table_name = 'business_book_bill_payments' then
                touched := new.paid_on <= locked;
                if tg_op = 'UPDATE' and old.paid_on <= locked then
                    touched := true;
                end if;
            elsif tg_table_name = 'business_book_adjustments' then
                touched := new.entry_date <= locked;
                if tg_op = 'UPDATE' and ((old.start_month is not null and old.start_month <= locked)
                                         or (old.reverse_on is not null and old.reverse_on <= locked)) then
                    touched := true;
                end if;
            elsif tg_table_name = 'business_book_intercompany' then
                touched := new.entry_date <= locked;
            elsif tg_table_name = 'business_book_assets' then
                if tg_op = 'INSERT' then
                    touched := new.disposed_on is not null and new.disposed_on <= locked;
                else
                    touched := (new.disposed_on is not null and new.disposed_on <= locked
                                and (new.disposed_on, new.proceeds, new.disposal_kind) is distinct from (old.disposed_on, old.proceeds, old.disposal_kind))
                               or (old.disposed_on is not null and old.disposed_on <= locked and old.disposed_on is distinct from new.disposed_on);
                end if;
            elsif tg_table_name = 'business_book_asset_impairments' then
                touched := new.entry_date <= locked and (tg_op = 'INSERT' or old.status is distinct from new.status);
            elsif tg_table_name = 'business_book_loan_repayments' then
                touched := new.paid_on <= locked;
            end if;
            if touched then
                raise exception 'business books: this is dated in a closed period (closed through %); reopen it first', locked
                    using errcode = 'check_violation';
            end if;
            return new;
        end $$
"""

OLD_PERIOD_OPEN = """
        create or replace function business_book_period_open() returns trigger language plpgsql as $$
        declare
            locked date;
            touched boolean := false;
        begin
            locked := business_book_locked_through(new.tenant_id, new.workspace_id);
            if locked is null then
                return new;
            end if;
            if tg_table_name = 'business_book_journal_lines' then
                touched := new.entry_date <= locked;
            elsif tg_table_name = 'business_book_bills' then
                if tg_op = 'INSERT' then
                    touched := new.bill_date <= locked and new.status <> 'void';
                elsif old.bill_date <= locked or new.bill_date <= locked then
                    touched := (new.bill_date, new.due_date, new.currency, new.subtotal, new.tax, new.total, new.category, new.section, new.status,
                                new.useful_life_months, new.project_id, new.cost_centre_id, new.vendor_id, new.tax_code, new.reverse_charge_tax)
                               is distinct from
                               (old.bill_date, old.due_date, old.currency, old.subtotal, old.tax, old.total, old.category, old.section, old.status,
                                old.useful_life_months, old.project_id, old.cost_centre_id, old.vendor_id, old.tax_code, old.reverse_charge_tax);
                end if;
            elsif tg_table_name = 'business_book_bill_payments' then
                touched := new.paid_on <= locked;
                if tg_op = 'UPDATE' and old.paid_on <= locked then
                    touched := true;
                end if;
            elsif tg_table_name = 'business_book_adjustments' then
                touched := new.entry_date <= locked;
                if tg_op = 'UPDATE' and ((old.start_month is not null and old.start_month <= locked)
                                         or (old.reverse_on is not null and old.reverse_on <= locked)) then
                    touched := true;
                end if;
            elsif tg_table_name = 'business_book_intercompany' then
                touched := new.entry_date <= locked;
            end if;
            if touched then
                raise exception 'business books: this is dated in a closed period (closed through %); reopen it first', locked
                    using errcode = 'check_violation';
            end if;
            return new;
        end $$
"""


def upgrade() -> None:
    op.create_table(
        "business_book_assets",
        *_base("business_book_assets"),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="equipment"),
        sa.Column("acquired_on", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cost", MONEY, nullable=False),
        sa.Column("useful_life_months", sa.Integer(), nullable=True),
        sa.Column("residual_value", MONEY, nullable=False, server_default="0"),
        sa.Column("method", sa.String(20), nullable=False, server_default="straight_line"),
        sa.Column("rate_pct", sa.Numeric(7, 4), nullable=True),
        sa.Column("depreciation_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("disposed_on", sa.Date(), nullable=True),
        sa.Column("disposal_kind", sa.String(10), nullable=True),
        sa.Column("proceeds", MONEY, nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "source_kind", "source_id", name="uq_business_book_assets_source"),
        sa.CheckConstraint("source_kind in ('bill', 'adjustment')", name="ck_business_book_assets_source"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_assets_currency"),
        sa.CheckConstraint("cost > 0 and residual_value >= 0 and residual_value < cost and proceeds >= 0", name="ck_business_book_assets_money"),
        sa.CheckConstraint("useful_life_months is null or useful_life_months between 1 and 600", name="ck_business_book_assets_life"),
        sa.CheckConstraint("method in ('straight_line', 'reducing_balance')", name="ck_business_book_assets_method"),
        sa.CheckConstraint("(method = 'reducing_balance') = (rate_pct is not null) and (rate_pct is null or (rate_pct > 0 and rate_pct <= 100))",
                           name="ck_business_book_assets_rate"),
        sa.CheckConstraint("extract(day from depreciation_start) = 1", name="ck_business_book_assets_start"),
        sa.CheckConstraint("status in ('active', 'disposed')", name="ck_business_book_assets_status"),
        sa.CheckConstraint("(status = 'disposed') = (disposed_on is not null and disposal_kind is not null) "
                           "and (disposal_kind is null or disposal_kind in ('sale', 'write_off')) "
                           "and (disposal_kind = 'sale' or proceeds = 0)", name="ck_business_book_assets_disposal"),
        sa.CheckConstraint("length(trim(name)) >= 2", name="ck_business_book_assets_name"),
    )
    op.create_table(
        "business_book_asset_impairments",
        *_base("business_book_asset_impairments"),
        sa.Column("asset_id", sa.String(80), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _ref("business_book_asset_impairments", "asset_id", "business_book_assets"),
        sa.CheckConstraint("amount > 0", name="ck_business_book_asset_impairments_amount"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_asset_impairments_currency"),
        sa.CheckConstraint("status in ('pending', 'approved', 'rejected')", name="ck_business_book_asset_impairments_status"),
        # Maker and checker: a decision names who made it, never the person who asked.
        sa.CheckConstraint("(status = 'pending') = (decided_by is null and decided_at is null) and (decided_by is null or decided_by <> requested_by)",
                           name="ck_business_book_asset_impairments_decision"),
        sa.CheckConstraint("length(trim(reason)) >= 5", name="ck_business_book_asset_impairments_reason"),
    )
    op.create_table(
        "business_book_loans",
        *_base("business_book_loans"),
        sa.Column("lender", sa.String(120), nullable=False),
        sa.Column("principal", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("annual_rate", sa.Numeric(9, 4), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False, server_default="monthly"),
        sa.Column("method", sa.String(20), nullable=False, server_default="annuity"),
        sa.Column("early_repayment", sa.String(20), nullable=False, server_default="reduce_term"),
        sa.Column("received_adjustment_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("idem_key", sa.String(100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_book_loans", "received_adjustment_id", "business_book_adjustments"),
        sa.CheckConstraint("principal > 0", name="ck_business_book_loans_principal"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_loans_currency"),
        sa.CheckConstraint("annual_rate >= 0 and annual_rate <= 100", name="ck_business_book_loans_rate"),
        sa.CheckConstraint("term_months between 1 and 600", name="ck_business_book_loans_term"),
        sa.CheckConstraint("frequency in ('monthly', 'quarterly', 'yearly')", name="ck_business_book_loans_frequency"),
        sa.CheckConstraint("method in ('annuity', 'equal_principal')", name="ck_business_book_loans_method"),
        sa.CheckConstraint("early_repayment in ('reduce_term', 'reduce_payment')", name="ck_business_book_loans_early"),
        sa.CheckConstraint("status in ('active', 'void') and ((status = 'void') = (voided_at is not null and voided_by is not null))",
                           name="ck_business_book_loans_status"),
    )
    op.create_index("uq_business_book_loans_idem", "business_book_loans", ["tenant_id", "workspace_id", "idem_key"], unique=True,
                    postgresql_where=sa.text("idem_key is not null"))
    op.create_index("uq_business_book_loans_received", "business_book_loans", ["tenant_id", "workspace_id", "received_adjustment_id"], unique=True,
                    postgresql_where=sa.text("received_adjustment_id is not null and status = 'active'"))
    op.create_table(
        "business_book_loan_repayments",
        *_base("business_book_loan_repayments"),
        sa.Column("loan_id", sa.String(80), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("interest", MONEY, nullable=False),
        sa.Column("principal", MONEY, nullable=False),
        sa.Column("bill_id", sa.String(80), nullable=True),
        sa.Column("adjustment_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("idem_key", sa.String(100), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("voided_by", sa.String(160), nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        _ref("business_book_loan_repayments", "loan_id", "business_book_loans"),
        _ref("business_book_loan_repayments", "bill_id", "business_book_bills"),
        _ref("business_book_loan_repayments", "adjustment_id", "business_book_adjustments"),
        sa.CheckConstraint("amount > 0 and interest >= 0 and principal >= 0 and interest + principal = amount", name="ck_business_book_loan_repayments_split"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_loan_repayments_currency"),
        sa.CheckConstraint("bill_id is null or adjustment_id is null", name="ck_business_book_loan_repayments_match"),
        sa.CheckConstraint("status in ('active', 'void') and ((status = 'void') = (voided_at is not null and voided_by is not null))",
                           name="ck_business_book_loan_repayments_status"),
    )
    op.create_index("uq_business_book_loan_repayments_idem", "business_book_loan_repayments", ["tenant_id", "workspace_id", "loan_id", "idem_key"],
                    unique=True, postgresql_where=sa.text("idem_key is not null"))
    op.create_index("uq_business_book_loan_repayments_bill", "business_book_loan_repayments", ["tenant_id", "workspace_id", "bill_id"], unique=True,
                    postgresql_where=sa.text("bill_id is not null and status = 'active'"))
    op.create_index("uq_business_book_loan_repayments_adjustment", "business_book_loan_repayments", ["tenant_id", "workspace_id", "adjustment_id"],
                    unique=True, postgresql_where=sa.text("adjustment_id is not null and status = 'active'"))

    for name, table, column, values, _narrow in WIDENED:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _in(column, values))
    op.drop_constraint("ck_business_book_journal_lines_flow", "business_book_journal_lines", type_="check")
    op.create_check_constraint("ck_business_book_journal_lines_flow", "business_book_journal_lines", f"flow is null or {_in('flow', FLOWS)}")

    op.execute(NEW_PERIOD_OPEN)
    for table in TRIGGERED:
        op.execute(f"create trigger {table}_open before insert or update on {table} for each row execute function business_book_period_open()")

    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Only for a test database: production restores a backup.
    for table in TRIGGERED:
        op.execute(f"drop trigger if exists {table}_open on {table}")
    op.execute(OLD_PERIOD_OPEN)
    op.drop_constraint("ck_business_book_journal_lines_flow", "business_book_journal_lines", type_="check")
    op.create_check_constraint("ck_business_book_journal_lines_flow", "business_book_journal_lines", f"flow is null or {_in('flow', OLD_FLOWS)}")
    for name, table, column, _wide, values in WIDENED:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _in(column, values))
    for table in reversed(TABLES):
        op.drop_table(table)
