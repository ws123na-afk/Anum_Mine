"""Business books v2: recurring bills, automatic exchange rates and draft bills (expand only). docs/business-books.md.

* ``business_book_bill_templates``: a recurring vendor bill (monthly, quarterly, yearly, or every N
  days, weeks or months; an end date or a number of bills). The daily job makes one normal bill per
  period; it never edits a bill it made. Status ``active``, ``paused`` or ``ended``.
* ``business_book_rate_feeds``: one row per business that opted in to automatic exchange rates from
  the European Central Bank, with the currencies it follows and how the last fetch went (when it
  last succeeded, the rates' date, the last error). A failed fetch keeps the rates already stored.
* ``business_book_bills`` gains ``template_id`` and ``period_date`` (a bill made by a recurring bill;
  a partial unique index makes one bill per template and period, so two runs racing make it once)
  and the status ``draft`` (a recurring bill may make drafts, which count nowhere until approved).
* ``business_book_rates`` gains ``origin``: ``manual`` (typed by an owner, the default for every
  existing row) or ``ecb`` (fetched). A manual rate for the same pair and day replaces a fetched one.

Row level security (forced): ``tenant_isolation_<table>`` and ``lifecycle_purge_<table>`` as in
``0137``. Discovery for the daily job: ``anum_maintenance`` may read the scope, status and next date
columns of active recurring bills and the scope and switch of enabled rate feeds, nothing else, and
cannot write. The job then acts as the application role inside each business's own RLS context.

Expand only: new tables, new nullable columns, a column with a default, a status check widened to
admit ``draft`` and new indexes. The previous release never writes a template, a feed or a draft and
ignores the new columns. Chained after ``0137_business_books``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0138_books_v2"
down_revision = "0137_business_books"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = ("business_book_bill_templates", "business_book_rate_feeds")
SECTIONS = "('cost_of_sales', 'operating_expense', 'capital')"
CURRENCY = "~ '^[A-Z]{3}$'"
MONEY = sa.Numeric(20, 4)


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
        "business_book_bill_templates",
        *_base("business_book_bill_templates"),
        sa.Column("vendor_id", sa.String(80), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False, server_default=""),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("tax", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("cost_centre_id", sa.String(80), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("useful_life_months", sa.Integer(), nullable=True),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("due_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_count", sa.Integer(), nullable=True),
        sa.Column("create_as", sa.String(10), nullable=False, server_default="open"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("next_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_date", sa.Date(), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_period", sa.Date(), nullable=True),
        sa.Column("end_reason", sa.String(20), nullable=False, server_default=""),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _ref("business_book_bill_templates", "vendor_id", "business_book_vendors"),
        _ref("business_book_bill_templates", "cost_centre_id", "business_book_cost_centres"),
        sa.CheckConstraint(f"currency {CURRENCY}", name="ck_business_book_bill_templates_currency"),
        sa.CheckConstraint("subtotal >= 0 and tax >= 0 and total > 0 and total = subtotal + tax", name="ck_business_book_bill_templates_money"),
        sa.CheckConstraint(f"section in {SECTIONS}", name="ck_business_book_bill_templates_section"),
        sa.CheckConstraint("useful_life_months is null or (section = 'capital' and useful_life_months between 1 and 600)",
                           name="ck_business_book_bill_templates_life"),
        sa.CheckConstraint("frequency in ('monthly', 'quarterly', 'yearly', 'days', 'weeks', 'months')", name="ck_business_book_bill_templates_freq"),
        sa.CheckConstraint("interval between 1 and 366 and (frequency in ('days', 'weeks', 'months') or interval = 1)",
                           name="ck_business_book_bill_templates_interval"),
        sa.CheckConstraint("due_days between 0 and 366", name="ck_business_book_bill_templates_due"),
        sa.CheckConstraint("end_date is null or end_date >= start_date", name="ck_business_book_bill_templates_end"),
        sa.CheckConstraint("max_count is null or max_count between 1 and 1000", name="ck_business_book_bill_templates_count"),
        sa.CheckConstraint("create_as in ('open', 'draft')", name="ck_business_book_bill_templates_create_as"),
        sa.CheckConstraint("status in ('active', 'paused', 'ended')", name="ck_business_book_bill_templates_status"),
        sa.CheckConstraint("(status = 'ended') = (next_date is null)", name="ck_business_book_bill_templates_next"),
        sa.CheckConstraint("next_index >= 0 and created_count >= 0", name="ck_business_book_bill_templates_counters"),
        sa.CheckConstraint("length(trim(description)) >= 2", name="ck_business_book_bill_templates_description"),
    )
    op.create_index("ix_business_book_bill_templates_next", "business_book_bill_templates", ["tenant_id", "workspace_id", "status", "next_date"])

    op.create_table(
        "business_book_rate_feeds",
        *_base("business_book_rate_feeds"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("currencies", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_attempt_at", TS, nullable=True),
        sa.Column("last_success_at", TS, nullable=True),
        sa.Column("last_as_of", sa.Date(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=False, server_default=""),
        sa.Column("unavailable", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_book_rate_feeds_workspace"),
        sa.CheckConstraint("jsonb_typeof(currencies) = 'array' and jsonb_array_length(currencies) <= 12", name="ck_business_book_rate_feeds_currencies"),
        sa.CheckConstraint("jsonb_typeof(unavailable) = 'array'", name="ck_business_book_rate_feeds_unavailable"),
    )

    # Bills made by a recurring bill: one per template and period, and drafts that count nowhere until approved.
    op.add_column("business_book_bills", sa.Column("template_id", sa.String(80), nullable=True))
    op.add_column("business_book_bills", sa.Column("period_date", sa.Date(), nullable=True))
    op.create_foreign_key("fk_business_book_bills_template_id", "business_book_bills", "business_book_bill_templates",
                          ["tenant_id", "workspace_id", "template_id"], ["tenant_id", "workspace_id", "id"])
    op.create_check_constraint("ck_business_book_bills_template", "business_book_bills", "(template_id is null) = (period_date is null)")
    op.execute("create unique index uq_business_book_bills_period on business_book_bills (tenant_id, workspace_id, template_id, period_date) "
               "where template_id is not null")
    op.drop_constraint("ck_business_book_bills_status", "business_book_bills", type_="check")
    op.create_check_constraint("ck_business_book_bills_status", "business_book_bills", "status in ('open', 'void', 'draft')")

    # Where a rate came from: typed by an owner (every existing row) or fetched from the ECB.
    op.add_column("business_book_rates", sa.Column("origin", sa.String(10), nullable=False, server_default="manual"))
    op.create_check_constraint("ck_business_book_rates_origin", "business_book_rates", "origin in ('manual', 'ecb')")

    for table in TABLES:
        _rls(table)

    # Discovery for the daily job: scope and schedule columns only, never amounts or descriptions, no write.
    op.execute(f"grant select (tenant_id, workspace_id, status, next_date) on business_book_bill_templates to {MAINT}")
    op.execute(f"""
        create policy maintenance_business_book_templates_due on business_book_bill_templates for select to {MAINT}
        using (status = 'active' and next_date is not null)
    """)
    op.execute(f"grant select (tenant_id, workspace_id, enabled) on business_book_rate_feeds to {MAINT}")
    op.execute(f"""
        create policy maintenance_business_book_rate_feeds_on on business_book_rate_feeds for select to {MAINT}
        using (enabled)
    """)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on business_book_bill_templates, business_book_rate_feeds from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_constraint("ck_business_book_rates_origin", "business_book_rates", type_="check")
    op.drop_column("business_book_rates", "origin")
    op.drop_constraint("ck_business_book_bills_status", "business_book_bills", type_="check")
    op.execute("delete from business_book_bills where status = 'draft'")
    op.create_check_constraint("ck_business_book_bills_status", "business_book_bills", "status in ('open', 'void')")
    op.execute("drop index if exists uq_business_book_bills_period")
    op.drop_constraint("ck_business_book_bills_template", "business_book_bills", type_="check")
    op.drop_constraint("fk_business_book_bills_template_id", "business_book_bills", type_="foreignkey")
    op.drop_column("business_book_bills", "period_date")
    op.drop_column("business_book_bills", "template_id")
    for table in reversed(TABLES):
        op.drop_table(table)
