"""Business tax: VAT settings, a rate table, tax codes on bills and journal lines, and ZATCA e-invoice data (expand only).

docs/business-tax.md.

* ``business_tax_settings``: one row per business: jurisdiction (``SA``, ``AE`` or ``OTHER``), whether it is
  registered, the registration number (KSA: 15 digits starting and ending with 3; UAE TRN: 15 digits, checked by the
  API), the registration date, the filing frequency, the ZATCA e-invoicing switch (off by default) and the seller
  address fields a ZATCA e-invoice needs. ``zatca_onboarding`` is ``not_started``: this release stores no ZATCA
  credentials, keys or certificates and never calls ZATCA.
* ``business_tax_rates``: the business's rate table, a code's rate from a day (to a day); the return and the invoice
  editor read it, so a rate change is a new row, never a code change.
* ``business_zatca_chains``: one row per business, the last e-invoice counter (ICV) and the hash of the last generated
  document. The API takes it ``for update``, so two generations never get the same counter; the counter only grows.
* ``business_zatca_documents``: UBL 2.1 XML generated for one sent invoice or credit note, its SHA-256 hash, the
  previous document's hash, the counter and the UUID. One per source document and one per counter; a row never
  changes (only the tenant purge role deletes).

New columns (nullable or with a default, so the previous release keeps writing rows): ``business_book_bills.tax_code``
and ``reverse_charge_tax``, ``business_book_journal_lines.tax_code``. The closed-period trigger now also refuses a
change of a bill's tax code or reverse-charge VAT in a closed period (``create or replace`` of
``business_book_period_open``, invoker function as before).

Row level security (forced) on every new table: ``tenant_isolation_<table>`` and ``lifecycle_purge_<table>`` with the
0079 grants, as in ``0151``. Nothing bypasses RLS; the triggers are invoker functions.

Chained after ``0153_finance_reconciliation``. Rollback: ``alembic downgrade 0153_finance_reconciliation`` restores
the trigger function, drops the columns and the tables (only for a test database; production restores a backup:
dropping the columns loses the tax codes). The previous release ignores the new columns and tables.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0154_business_tax"
down_revision = "0153_finance_reconciliation"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
PURGER = "anum_tenant_purger"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_tax_settings", "business_tax_rates", "business_zatca_chains", "business_zatca_documents")
CODES = ("standard", "zero_rated", "export", "exempt", "out_of_scope", "reverse_charge", "import_customs")


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
"""


def upgrade() -> None:
    op.create_table(
        "business_tax_settings",
        *_base("business_tax_settings"),
        sa.Column("jurisdiction", sa.String(5), nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("registration_number", sa.String(30), nullable=False, server_default=""),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("filing_frequency", sa.String(10), nullable=False, server_default="quarterly"),
        sa.Column("period_start_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("zatca_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("zatca_onboarding", sa.String(20), nullable=False, server_default="not_started"),
        sa.Column("seller_crn", sa.String(20), nullable=False, server_default=""),
        sa.Column("seller_street", sa.String(120), nullable=False, server_default=""),
        sa.Column("seller_building", sa.String(10), nullable=False, server_default=""),
        sa.Column("seller_district", sa.String(120), nullable=False, server_default=""),
        sa.Column("seller_city", sa.String(80), nullable=False, server_default=""),
        sa.Column("seller_postal_code", sa.String(10), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_tax_settings_business"),
        sa.CheckConstraint("jurisdiction in ('SA', 'AE', 'OTHER')", name="ck_business_tax_settings_jurisdiction"),
        sa.CheckConstraint("filing_frequency in ('monthly', 'quarterly')", name="ck_business_tax_settings_frequency"),
        sa.CheckConstraint("period_start_month between 1 and 3", name="ck_business_tax_settings_cycle"),
        sa.CheckConstraint("zatca_onboarding = 'not_started'", name="ck_business_tax_settings_onboarding"),
        sa.CheckConstraint("not zatca_enabled or jurisdiction = 'SA'", name="ck_business_tax_settings_zatca"),
        sa.CheckConstraint("not registered or length(registration_number) >= 3", name="ck_business_tax_settings_number"),
    )
    op.create_table(
        "business_tax_rates",
        *_base("business_tax_rates"),
        sa.Column("jurisdiction", sa.String(5), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("rate_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "jurisdiction", "code", "effective_from", name="uq_business_tax_rates_day"),
        sa.CheckConstraint("jurisdiction in ('SA', 'AE', 'OTHER')", name="ck_business_tax_rates_jurisdiction"),
        sa.CheckConstraint(_in("code", CODES), name="ck_business_tax_rates_code"),
        sa.CheckConstraint("rate_pct >= 0 and rate_pct <= 100", name="ck_business_tax_rates_rate"),
        sa.CheckConstraint("effective_to is null or effective_to >= effective_from", name="ck_business_tax_rates_period"),
    )
    op.create_table(
        "business_zatca_chains",
        *_base("business_zatca_chains"),
        sa.Column("last_counter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hash", sa.String(100), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_zatca_chains_business"),
        sa.CheckConstraint("last_counter >= 0", name="ck_business_zatca_chains_counter"),
    )
    op.create_table(
        "business_zatca_documents",
        *_base("business_zatca_documents"),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("document_number", sa.String(60), nullable=False),
        sa.Column("type_code", sa.String(3), nullable=False),
        sa.Column("subtype", sa.String(7), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(36), nullable=False),
        sa.Column("invoice_hash", sa.String(100), nullable=False),
        sa.Column("previous_hash", sa.String(100), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("xml", sa.Text(), nullable=False),
        sa.Column("qr", sa.Text(), nullable=False, server_default=""),
        sa.Column("missing", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="generated"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "source_kind", "source_id", name="uq_business_zatca_documents_source"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "counter", name="uq_business_zatca_documents_counter"),
        sa.CheckConstraint("source_kind in ('invoice', 'credit_note')", name="ck_business_zatca_documents_source"),
        sa.CheckConstraint("type_code in ('388', '381', '383')", name="ck_business_zatca_documents_type"),
        sa.CheckConstraint("subtype in ('0100000', '0200000')", name="ck_business_zatca_documents_subtype"),
        sa.CheckConstraint("counter >= 1", name="ck_business_zatca_documents_counter"),
        sa.CheckConstraint("status = 'generated'", name="ck_business_zatca_documents_status"),
        sa.CheckConstraint("jsonb_typeof(missing) = 'array'", name="ck_business_zatca_documents_missing"),
    )

    # The counter only grows; a generated document never changes. Only the tenant purge deletes either.
    op.execute("""
        create function business_zatca_chain_forward() returns trigger language plpgsql as $$
        begin
            if tg_op = 'DELETE' then
                if current_user = 'anum_tenant_purger' then
                    return old;
                end if;
                raise exception 'the e-invoice counter is never deleted' using errcode = '55000';
            end if;
            if new.last_counter <> old.last_counter + 1 then
                raise exception 'the e-invoice counter only moves forward by one' using errcode = '55000';
            end if;
            return new;
        end $$
    """)
    op.execute("create trigger business_zatca_chain_forward before update or delete on business_zatca_chains "
               "for each row execute function business_zatca_chain_forward()")
    op.execute("""
        create function business_zatca_documents_frozen() returns trigger language plpgsql as $$
        begin
            if tg_op = 'DELETE' and current_user = 'anum_tenant_purger' then
                return old;
            end if;
            raise exception 'generated e-invoice data never changes' using errcode = '55000';
        end $$
    """)
    op.execute("create trigger business_zatca_documents_frozen before update or delete on business_zatca_documents "
               "for each row execute function business_zatca_documents_frozen()")

    op.add_column("business_book_bills", sa.Column("tax_code", sa.String(20), nullable=True))
    op.add_column("business_book_bills", sa.Column("reverse_charge_tax", MONEY, nullable=False, server_default="0"))
    op.create_check_constraint("ck_business_book_bills_tax_code", "business_book_bills", f"tax_code is null or {_in('tax_code', CODES)}")
    op.create_check_constraint("ck_business_book_bills_reverse_charge", "business_book_bills",
                               "reverse_charge_tax >= 0 and (reverse_charge_tax = 0 or tax_code = 'reverse_charge')")
    op.add_column("business_book_journal_lines", sa.Column("tax_code", sa.String(20), nullable=True))
    op.create_check_constraint("ck_business_book_journal_lines_tax_code", "business_book_journal_lines",
                               f"tax_code is null or {_in('tax_code', CODES)}")
    op.execute(NEW_PERIOD_OPEN)

    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    # Only for a test database: production restores a backup (dropping the columns loses the tax codes).
    op.execute(OLD_PERIOD_OPEN)
    op.drop_constraint("ck_business_book_journal_lines_tax_code", "business_book_journal_lines", type_="check")
    op.drop_column("business_book_journal_lines", "tax_code")
    op.drop_constraint("ck_business_book_bills_reverse_charge", "business_book_bills", type_="check")
    op.drop_constraint("ck_business_book_bills_tax_code", "business_book_bills", type_="check")
    op.drop_column("business_book_bills", "reverse_charge_tax")
    op.drop_column("business_book_bills", "tax_code")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists business_zatca_documents_frozen()")
    op.execute("drop function if exists business_zatca_chain_forward()")
