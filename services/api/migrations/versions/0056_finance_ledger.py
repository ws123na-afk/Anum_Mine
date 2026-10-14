"""Personal finance ledger, Stage 2 (docs/finance.md).

Eight new tables hold one person's ledger and Financial Twin: per-person finance
settings (base currency AED and time zone Asia/Dubai by default), accounts (bank, card,
cash, loan, investment; at most the last four digits of a number), signed transactions
with a dedupe hash unique per account, statement imports, bills, debts, goals and manual
holdings (quantity and cost, no prices). Money is ``numeric(20, 4)`` next to a
three-letter ``currency``; quantities are ``numeric(28, 8)``.

Every table belongs to one tenant and workspace (foreign key to ``workspaces``) and is
under forced RLS with two policies:

* ``tenant_isolation_<table>``: the usual ``anum.tenant_id`` and ``anum.workspace_id``.
* ``owner_only_<table>``, ``as restrictive``: ``owner_user_id`` must equal
  ``anum.user_id``. Restrictive policies are AND-ed with the permissive one, so a row is
  visible and writable only to its owner inside its own tenant and workspace, workspace
  owners included. With no ``anum.user_id`` set, nothing is visible.

No role is granted anything new, nothing bypasses RLS and nothing reads finance across
tenants or people: there is no background job in Stage 2.

Expand only: new tables. The previous release ignores them.

Chained after ``0055_plain_dashes``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056_finance_ledger"
down_revision = "0055_plain_dashes"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
# Created in this order, dropped in reverse.
TABLES = (
    "finance_settings",
    "finance_accounts",
    "finance_imports",
    "finance_transactions",
    "finance_bills",
    "finance_debts",
    "finance_goals",
    "finance_holdings",
)
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(20, 4)
CURRENCY_CHECK = "currency ~ '^[A-Z]{3}$'"


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _account_fk(table: str, *, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["account_id"], ["finance_accounts.id"], name=f"fk_{table}_account", ondelete=ondelete
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    ]


def _owner_index(table: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_owner", table, ["tenant_id", "workspace_id", "owner_user_id", *columns])


def upgrade() -> None:
    op.create_table(
        "finance_settings",
        *_owned(),
        sa.Column("base_currency", sa.String(3), nullable=False, server_default="AED"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Dubai"),
        sa.Column("category_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_finance_settings"),
        _workspace_fk("finance_settings"),
        sa.CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="ck_finance_settings_currency"),
    )

    op.create_table(
        "finance_accounts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("institution", sa.String(120), nullable=True),
        sa.Column("last4", sa.String(4), nullable=True),
        sa.Column("opening_balance", MONEY, nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_stamps(),
        _workspace_fk("finance_accounts"),
        sa.CheckConstraint("kind in ('bank', 'card', 'cash', 'loan', 'investment')", name="ck_finance_accounts_kind"),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_accounts_currency"),
        # Never a full card or account number: four digits at most.
        sa.CheckConstraint("last4 is null or last4 ~ '^[0-9]{4}$'", name="ck_finance_accounts_last4"),
    )
    _owner_index("finance_accounts", "created_at")

    op.create_table(
        "finance_imports",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("preset", sa.String(40), nullable=False),
        sa.Column("date_format", sa.String(20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("finance_imports"),
        _account_fk("finance_imports", ondelete="CASCADE"),
    )
    _owner_index("finance_imports", "created_at")

    op.create_table(
        "finance_transactions",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("category_source", sa.String(20), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("import_id", sa.String(80), nullable=True),
        sa.Column("dedupe_hash", sa.String(64), nullable=False),
        *_stamps(),
        _workspace_fk("finance_transactions"),
        _account_fk("finance_transactions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["import_id"], ["finance_imports.id"], name="fk_finance_transactions_import", ondelete="CASCADE"
        ),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_transactions_currency"),
        # Importing the same statement twice adds nothing (docs/finance.md#csv-import).
        sa.UniqueConstraint("account_id", "dedupe_hash", name="uq_finance_transactions_dedupe"),
    )
    _owner_index("finance_transactions", "posted_on")
    op.create_index("ix_finance_transactions_account", "finance_transactions", ["account_id", "posted_on"])
    op.create_index("ix_finance_transactions_import", "finance_transactions", ["import_id"])

    op.create_table(
        "finance_bills",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cadence", sa.String(20), nullable=False),
        sa.Column("next_due_on", sa.Date(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(1000), nullable=True),
        *_stamps(),
        _workspace_fk("finance_bills"),
        _account_fk("finance_bills", ondelete="SET NULL"),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_bills_currency"),
        sa.CheckConstraint(
            "cadence in ('weekly', 'monthly', 'quarterly', 'yearly', 'once')", name="ck_finance_bills_cadence"
        ),
        sa.CheckConstraint("source in ('manual', 'detected')", name="ck_finance_bills_source"),
    )
    _owner_index("finance_bills", "next_due_on")

    op.create_table(
        "finance_debts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("balance", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("apr_percent", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("minimum_payment", MONEY, nullable=False, server_default="0"),
        sa.Column("due_day", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        *_stamps(),
        _workspace_fk("finance_debts"),
        _account_fk("finance_debts", ondelete="SET NULL"),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_debts_currency"),
        sa.CheckConstraint("kind in ('loan', 'card', 'mortgage', 'other')", name="ck_finance_debts_kind"),
        sa.CheckConstraint("due_day is null or due_day between 1 and 31", name="ck_finance_debts_due_day"),
    )
    _owner_index("finance_debts", "created_at")

    op.create_table(
        "finance_goals",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("target_amount", MONEY, nullable=False),
        sa.Column("saved_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_on", sa.Date(), nullable=True),
        sa.Column("account_id", sa.String(80), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        *_stamps(),
        _workspace_fk("finance_goals"),
        _account_fk("finance_goals", ondelete="SET NULL"),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_goals_currency"),
    )
    _owner_index("finance_goals", "created_at")

    op.create_table(
        "finance_holdings",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("account_id", sa.String(80), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("quantity", sa.Numeric(28, 8), nullable=False),
        sa.Column("cost_basis", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=True),
        *_stamps(),
        _workspace_fk("finance_holdings"),
        _account_fk("finance_holdings", ondelete="CASCADE"),
        sa.CheckConstraint(CURRENCY_CHECK, name="ck_finance_holdings_currency"),
    )
    _owner_index("finance_holdings", "symbol")

    for table in TABLES:
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
        op.execute(
            f"""
            create policy owner_only_{table} on {table}
            as restrictive
            using (
              {OWNER_PREDICATE}
            )
            with check (
              {OWNER_PREDICATE}
            )
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
