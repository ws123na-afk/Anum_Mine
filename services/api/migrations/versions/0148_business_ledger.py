"""Business ledger: a double-entry journal per business, period close and accountants (expand only). docs/business-books.md#the-journal.

Until now the business books derived every figure from the source documents (invoices, bills,
payments, allocations, adjustments, intercompany entries). This release posts each source document
event into a journal in the business's base (functional) currency, and keeps the source documents as
they are: they stay the only place a figure is typed.

* ``business_book_ledger_accounts``: the chart of accounts of one business. A code of four to six
  digits, unique per business, whose first digit matches the account's kind (1 assets, 2 liabilities,
  3 equity, 4 revenue, 5 cost of sales, 6 operating expenses, 7 exchange differences and finance
  costs). ``system_key`` names the accounts the posting rules use (one per key and business).
* ``business_book_journal_entries``: one balanced entry per source document event. ``event_key`` is
  the event's stable name (``bill:<id>``, ``invoice_payment:<id>``...), so posting is idempotent: one
  live entry per key. A correction is a reversal (``reverses_id``) plus a new entry; an entry changes
  only from ``posted`` to ``reversed``.
* ``business_book_journal_lines``: debit or credit in the base currency, with the original currency,
  the signed original amount, the rate, its date and its source kept on each line. Lines never change.
* ``business_book_posting_issues``: events that could not be posted and why (a missing exchange
  rate: ANUM never guesses one). Replaced on every posting run.
* ``business_book_closes``: closed months and years. A closed period refuses postings and changes to
  the source documents dated in it until an owner reopens it with a reason; a close row changes
  only from ``closed`` to ``reopened``.
* ``business_book_accountants``: members an owner named accountant of the business (adjustments,
  manual journal entries, approving draft bills of others, closing periods).

Database rules: every entry balances (debits equal credits) and has at least two lines, checked when
the transaction commits by a deferred constraint trigger that also fires when an entry is inserted,
so an entry with no lines at all is refused (the personal ``finance_postings_balanced`` trigger fires
on postings only: audit defect B7, noted in docs/business-books.md). Journal lines and entries cannot
be edited or deleted except by the tenant purge role. A journal line, bill, bill payment, adjustment
or intercompany entry dated on or before the business's last closed day is refused.

Also: ``business_book_settings.policy_note`` (an owner's accounting policy text), two more adjustment
kinds (``loan_interest_accrued`` and ``loan_interest_paid``: interest owed and interest paid are
separate events, audit defect B5), and discovery for the daily posting run: ``anum_maintenance`` may
read only ``tenant_id`` and ``workspace_id`` of book settings.

Row level security (forced) on every new table: ``tenant_isolation_<table>`` and
``lifecycle_purge_<table>`` with the 0079 grants, as in ``0137``. Nothing bypasses RLS; the triggers
are invoker functions.

Expand only: new tables, a column with a default, a widened check and triggers on the book tables
that refuse only rows dated in a closed period (the previous release closes nothing, so they never
fire for it). The previous release ignores the journal; an adjustment of a new kind read by it would
fail to load, so roll back only before one is recorded. Chained after ``0147_finance_personal_money``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0148_business_ledger"
down_revision = "0147_finance_personal_money"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "business_book_ledger_accounts",
    "business_book_journal_entries",
    "business_book_journal_lines",
    "business_book_posting_issues",
    "business_book_closes",
    "business_book_accountants",
)
SUBTYPES = (
    "bank", "receivable", "tax_receivable", "prepayment", "current_asset", "fixed_asset", "accumulated_depreciation",
    "payable", "tax_payable", "accrual", "interest_payable", "owed_to_members", "loan", "current_liability",
    "capital", "drawings", "retained_earnings", "current_result",
    "revenue", "other_income", "cost_of_sales", "operating_expense", "depreciation", "finance_cost", "fx",
)
SOURCE_KINDS = ("invoice", "credit_note", "invoice_payment", "bill", "bill_payment", "allocation", "adjustment", "intercompany", "manual", "closing")
FLOWS = ("customers_paid", "suppliers_paid", "capital_spend", "loans_received", "loans_repaid", "interest_paid", "owner_contributions",
         "owner_drawings", "members_reimbursed", "intercompany_in", "intercompany_out", "intercompany_lent", "intercompany_borrowed", "other")
OLD_KINDS = ("accrual", "prepayment", "capital_item", "loan_received", "loan_repayment", "loan_interest", "owner_contribution", "owner_drawing",
             "owner_reimbursement")
NEW_KINDS = (*OLD_KINDS, "loan_interest_accrued", "loan_interest_paid")
CURRENCY = "~ '^[A-Z]{3}$'"
BASE = sa.Numeric(20, 4)


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


def upgrade() -> None:
    op.create_table(
        "business_book_ledger_accounts",
        *_base("business_book_ledger_accounts"),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("subtype", sa.String(30), nullable=False),
        sa.Column("system_key", sa.String(40), nullable=True),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", "code", name="uq_business_book_ledger_accounts_code"),
        sa.CheckConstraint("code ~ '^[1-7][0-9]{3,5}$'", name="ck_business_book_ledger_accounts_code"),
        sa.CheckConstraint(_in("subtype", SUBTYPES), name="ck_business_book_ledger_accounts_subtype"),
        sa.CheckConstraint("length(trim(name)) >= 2", name="ck_business_book_ledger_accounts_name"),
        sa.CheckConstraint("category is null or category ~ '^[a-z][a-z0-9_]{0,39}$'", name="ck_business_book_ledger_accounts_category"),
        sa.CheckConstraint("not (archived and system_key is not null)", name="ck_business_book_ledger_accounts_system"),
    )
    op.execute("create unique index uq_business_book_ledger_accounts_key on business_book_ledger_accounts (tenant_id, workspace_id, system_key) "
               "where system_key is not null")
    op.execute("create unique index uq_business_book_ledger_accounts_category on business_book_ledger_accounts (tenant_id, workspace_id, category) "
               "where category is not null")

    op.create_table(
        "business_book_journal_entries",
        *_base("business_book_journal_entries"),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=True),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="posted"),
        sa.Column("reverses_id", sa.String(80), nullable=True),
        sa.Column("reversed_by_id", sa.String(80), nullable=True),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_business_book_journal_entries_source"),
        sa.CheckConstraint("status in ('posted', 'reversed')", name="ck_business_book_journal_entries_status"),
        sa.CheckConstraint("(status = 'reversed') = (reversed_by_id is not null)", name="ck_business_book_journal_entries_reversed"),
        sa.CheckConstraint("reverses_id is null or status = 'posted'", name="ck_business_book_journal_entries_reversal"),
        sa.CheckConstraint("late = (entry_date > event_date)", name="ck_business_book_journal_entries_late"),
        sa.CheckConstraint("length(trim(description)) >= 2", name="ck_business_book_journal_entries_description"),
    )
    op.create_index("ix_business_book_journal_entries_date", "business_book_journal_entries", ["tenant_id", "workspace_id", "entry_date"])
    op.create_index("ix_business_book_journal_entries_source", "business_book_journal_entries", ["tenant_id", "workspace_id", "source_kind", "source_id"])
    # One live entry per source event (a re-run posts nothing twice); one reversal per entry.
    op.execute("create unique index uq_business_book_journal_entries_event on business_book_journal_entries (tenant_id, workspace_id, event_key) "
               "where status = 'posted' and reverses_id is null")
    op.execute("create unique index uq_business_book_journal_entries_reverses on business_book_journal_entries (tenant_id, workspace_id, reverses_id) "
               "where reverses_id is not null")

    op.create_table(
        "business_book_journal_lines",
        *_base("business_book_journal_lines"),
        sa.Column("entry_id", sa.String(80), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("debit", BASE, nullable=False, server_default="0"),
        sa.Column("credit", BASE, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", BASE, nullable=False),
        sa.Column("rate", sa.Numeric(24, 10), nullable=True),
        sa.Column("rate_date", sa.Date(), nullable=True),
        sa.Column("rate_source", sa.String(200), nullable=True),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("customer_id", sa.String(80), nullable=True),
        sa.Column("counterparty_workspace_id", sa.String(80), nullable=True),
        sa.Column("pair_id", sa.String(80), nullable=True),
        sa.Column("flow", sa.String(30), nullable=True),
        _ref("business_book_journal_lines", "entry_id", "business_book_journal_entries"),
        _ref("business_book_journal_lines", "account_id", "business_book_ledger_accounts"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "entry_id", "line_no", name="uq_business_book_journal_lines_no"),
        sa.CheckConstraint("(debit > 0 and credit = 0 and amount > 0) or (credit > 0 and debit = 0 and amount < 0)",
                           name="ck_business_book_journal_lines_side"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_journal_lines_currency"),
        sa.CheckConstraint("(rate is null) = (rate_date is null) and (rate is null) = (rate_source is null) and (rate is null or rate > 0)",
                           name="ck_business_book_journal_lines_rate"),
        sa.CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_business_book_journal_lines_source"),
        sa.CheckConstraint(f"flow is null or {_in('flow', FLOWS)}", name="ck_business_book_journal_lines_flow"),
    )
    op.create_index("ix_business_book_journal_lines_account", "business_book_journal_lines", ["tenant_id", "workspace_id", "account_id", "entry_date"])
    op.create_index("ix_business_book_journal_lines_entry", "business_book_journal_lines", ["tenant_id", "workspace_id", "entry_id"])
    op.create_index("ix_business_book_journal_lines_date", "business_book_journal_lines", ["tenant_id", "workspace_id", "entry_date"])

    op.create_table(
        "business_book_posting_issues",
        *_base("business_book_posting_issues"),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("rate_date", sa.Date(), nullable=True),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.UniqueConstraint("tenant_id", "workspace_id", "event_key", name="uq_business_book_posting_issues_event"),
        sa.CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_business_book_posting_issues_source"),
        sa.CheckConstraint("reason in ('missing_rate', 'too_small')", name="ck_business_book_posting_issues_reason"),
    )

    op.create_table(
        "business_book_closes",
        *_base("business_book_closes"),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="closed"),
        sa.Column("closed_by", sa.String(160), nullable=False),
        sa.Column("closing_entry_id", sa.String(80), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reopened_by", sa.String(160), nullable=True),
        sa.Column("reopened_at", TS, nullable=True),
        sa.Column("reopen_reason", sa.String(300), nullable=False, server_default=""),
        sa.CheckConstraint("kind in ('month', 'year')", name="ck_business_book_closes_kind"),
        sa.CheckConstraint("status in ('closed', 'reopened')", name="ck_business_book_closes_status"),
        sa.CheckConstraint("extract(day from period_start) = 1 and period_end >= period_start", name="ck_business_book_closes_period"),
        sa.CheckConstraint("(status = 'reopened') = (reopened_at is not null and reopened_by is not null and length(trim(reopen_reason)) >= 5)",
                           name="ck_business_book_closes_reopened"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_business_book_closes_snapshot"),
    )
    op.execute("create unique index uq_business_book_closes_live on business_book_closes (tenant_id, workspace_id, period_end) where status = 'closed'")

    op.create_table(
        "business_book_accountants",
        *_base("business_book_accountants"),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("added_by", sa.String(160), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "user_id", name="uq_business_book_accountants_user"),
    )

    op.add_column("business_book_settings", sa.Column("policy_note", sa.String(1000), nullable=False, server_default=""))
    op.drop_constraint("ck_business_book_adjustments_kind", "business_book_adjustments", type_="check")
    op.create_check_constraint("ck_business_book_adjustments_kind", "business_book_adjustments", _in("kind", NEW_KINDS))

    # The last closed day of one business (the RLS context decides which rows are visible: an invoker function).
    op.execute("""
        create function business_book_locked_through(t text, w text) returns date language sql stable as $$
            select max(period_end) from business_book_closes where tenant_id = t and workspace_id = w and status = 'closed'
        $$
    """)

    # Every entry balances and has at least two lines; an entry with none is refused too (defect B7 of the personal trigger).
    op.execute("""
        create function business_book_journal_balanced() returns trigger language plpgsql as $$
        declare
            target text;
        begin
            if current_user = 'anum_tenant_purger' then
                return null;
            end if;
            if tg_table_name = 'business_book_journal_entries' then
                target := new.id;
            else
                target := coalesce(new.entry_id, old.entry_id);
            end if;
            if not exists (select 1 from business_book_journal_entries e where e.id = target) then
                return null;
            end if;
            if (select count(*) from business_book_journal_lines l where l.entry_id = target) < 2 then
                raise exception 'business journal entry % needs at least two lines', target using errcode = '23514';
            end if;
            if (select sum(l.debit) - sum(l.credit) from business_book_journal_lines l where l.entry_id = target) <> 0 then
                raise exception 'business journal entry % does not balance', target using errcode = '23514';
            end if;
            return null;
        end $$
    """)
    op.execute("""
        create constraint trigger business_book_lines_balanced after insert or delete on business_book_journal_lines
        deferrable initially deferred for each row execute function business_book_journal_balanced()
    """)
    op.execute("""
        create constraint trigger business_book_entries_balanced after insert on business_book_journal_entries
        deferrable initially deferred for each row execute function business_book_journal_balanced()
    """)

    # Lines never change; an entry changes only from posted to reversed. Only the tenant purge deletes either.
    op.execute("""
        create function business_book_lines_frozen() returns trigger language plpgsql as $$
        begin
            if tg_op = 'DELETE' and current_user = 'anum_tenant_purger' then
                return old;
            end if;
            raise exception 'business journal lines never change: post a reversing entry' using errcode = '55000';
        end $$
    """)
    op.execute("create trigger business_book_lines_frozen before update or delete on business_book_journal_lines "
               "for each row execute function business_book_lines_frozen()")
    op.execute("""
        create function business_book_entries_frozen() returns trigger language plpgsql as $$
        begin
            if tg_op = 'DELETE' then
                if current_user = 'anum_tenant_purger' then
                    return old;
                end if;
                raise exception 'business journal entries are never deleted: post a reversing entry' using errcode = '55000';
            end if;
            if old.status = 'posted' and new.status = 'reversed' and old.reversed_by_id is null and new.reversed_by_id is not null
               and (new.id, new.tenant_id, new.workspace_id, new.entry_date, new.event_date, new.source_kind, new.source_id, new.event_key,
                    new.fingerprint, new.description, new.reverses_id, new.late, new.created_by, new.created_at)
                   is not distinct from
                   (old.id, old.tenant_id, old.workspace_id, old.entry_date, old.event_date, old.source_kind, old.source_id, old.event_key,
                    old.fingerprint, old.description, old.reverses_id, old.late, old.created_by, old.created_at) then
                return new;
            end if;
            raise exception 'a business journal entry only changes from posted to reversed' using errcode = '55000';
        end $$
    """)
    op.execute("create trigger business_book_entries_frozen before update or delete on business_book_journal_entries "
               "for each row execute function business_book_entries_frozen()")

    # A close row changes only from closed to reopened (who, when and why); the trail stays.
    op.execute("""
        create function business_book_closes_frozen() returns trigger language plpgsql as $$
        begin
            if tg_op = 'DELETE' then
                if current_user = 'anum_tenant_purger' then
                    return old;
                end if;
                raise exception 'a closed period is reopened with a reason, never deleted' using errcode = '55000';
            end if;
            if old.status = 'closed' and new.status = 'reopened'
               and (new.id, new.tenant_id, new.workspace_id, new.period_start, new.period_end, new.kind, new.closed_by, new.closing_entry_id,
                    new.snapshot, new.created_at)
                   is not distinct from
                   (old.id, old.tenant_id, old.workspace_id, old.period_start, old.period_end, old.kind, old.closed_by, old.closing_entry_id,
                    old.snapshot, old.created_at) then
                return new;
            end if;
            raise exception 'a closed period only changes to reopened' using errcode = '55000';
        end $$
    """)
    op.execute("create trigger business_book_closes_frozen before update or delete on business_book_closes "
               "for each row execute function business_book_closes_frozen()")

    # Nothing is posted or changed on a day of a closed period (defect B6): journal lines, and the source documents.
    op.execute("""
        create function business_book_period_open() returns trigger language plpgsql as $$
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
                                new.useful_life_months, new.project_id, new.cost_centre_id, new.vendor_id)
                               is distinct from
                               (old.bill_date, old.due_date, old.currency, old.subtotal, old.tax, old.total, old.category, old.section, old.status,
                                old.useful_life_months, old.project_id, old.cost_centre_id, old.vendor_id);
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
    """)
    op.execute("create trigger business_book_lines_open before insert on business_book_journal_lines "
               "for each row execute function business_book_period_open()")
    for table in ("business_book_bills", "business_book_bill_payments", "business_book_adjustments", "business_book_intercompany"):
        op.execute(f"create trigger {table}_open before insert or update on {table} for each row execute function business_book_period_open()")

    for table in TABLES:
        _rls(table)

    # Discovery for the daily posting run: the scope columns of book settings, nothing else, no write.
    op.execute(f"grant select (tenant_id, workspace_id) on business_book_settings to {MAINT}")
    op.execute(f"create policy maintenance_business_book_settings_all on business_book_settings for select to {MAINT} using (true)")


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute("drop policy if exists maintenance_business_book_settings_all on business_book_settings")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on business_book_settings from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in ("business_book_bills", "business_book_bill_payments", "business_book_adjustments", "business_book_intercompany"):
        op.execute(f"drop trigger if exists {table}_open on {table}")
    op.execute("delete from business_book_adjustments where kind in ('loan_interest_accrued', 'loan_interest_paid')")
    op.drop_constraint("ck_business_book_adjustments_kind", "business_book_adjustments", type_="check")
    op.create_check_constraint("ck_business_book_adjustments_kind", "business_book_adjustments", _in("kind", OLD_KINDS))
    op.drop_column("business_book_settings", "policy_note")
    for table in reversed(TABLES):
        op.drop_table(table)
    for function in ("business_book_period_open()", "business_book_closes_frozen()", "business_book_entries_frozen()", "business_book_lines_frozen()",
                     "business_book_journal_balanced()", "business_book_locked_through(text, text)"):
        op.execute(f"drop function if exists {function}")
