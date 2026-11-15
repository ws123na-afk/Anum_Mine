"""Personal finance: budgets, learned merchant categories, the wider category list and recurring bills (expand only).

docs/finance.md#category-ontology, #learned-merchants, #budgets-and-planning, #recurring-payments.

* ``finance_plans`` gains ``budgets`` and ``category_mappings`` (jsonb, not null, default ``[]``): the owner's budgets
  (spending, savings, emergency reserve, sinking funds, remittances, debts; monthly, weekly or a custom period) and what
  the owner filed each merchant as when correcting a row by hand. The row keeps its forced RLS of ``0057`` (tenant,
  workspace and the restrictive owner-only policy) and is exported and deleted with Finance as before.
* ``finance_bills`` gains ``merchant`` (the merchant key a detected recurring payment was confirmed for), ``recurring_kind``
  (rent, phone, internet, streaming, ...), ``auto_link`` (boolean, not null, default false: the owner allows a later
  unique, strong payment to be linked to the bill's due date) and ``usage`` (often, sometimes, rarely), with checks.
* ``ck_finance_subcategories_parent`` is widened to the five new categories (``clothing``, ``family``, ``personal_care``,
  ``family_support``, ``charity``) so the owner can add subcategories under them. ``finance_transactions.category`` has no
  check (a string), so it needs no change.

Chained after ``0184_pf_matching`` (a parallel branch; renumber at merge if needed).

Expand only. The previous release never reads or writes the new columns (the defaults fill them; its upserts of
``finance_plans`` and ``finance_bills`` set only the columns it knows). One caveat for a rollback: a row filed under one
of the five new categories by this release cannot be read by the previous release's ``Category`` enum, so roll back only
before such rows exist, or refile them first. Rollback: ``alembic downgrade 0184_pf_matching`` drops the columns and puts
the narrower check back (it fails while a subcategory sits under a new category: archive and refile those first).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0185_pf_budgets"
down_revision = "0184_pf_matching"
branch_labels = None
depends_on = None

OLD_CHECK = "parent in ('income', 'cashback', 'refunds', 'transfer', 'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', " \
    "'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment', 'subscriptions', 'travel', 'insurance', 'fees', " \
    "'interest', 'cash', 'other')"
NEW_CHECK = "parent in ('income', 'cashback', 'refunds', 'transfer', 'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', " \
    "'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment', 'subscriptions', 'travel', 'insurance', 'fees', " \
    "'interest', 'cash', 'other', 'clothing', 'family', 'personal_care', 'family_support', 'charity')"


def upgrade() -> None:
    op.add_column("finance_plans", sa.Column("budgets", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("finance_plans", sa.Column("category_mappings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("finance_bills", sa.Column("merchant", sa.String(120), nullable=True))
    op.add_column("finance_bills", sa.Column("recurring_kind", sa.String(30), nullable=True))
    op.add_column("finance_bills", sa.Column("auto_link", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("finance_bills", sa.Column("usage", sa.String(10), nullable=True))
    op.create_check_constraint("ck_finance_bills_usage", "finance_bills", "usage is null or usage in ('often', 'sometimes', 'rarely')")
    op.drop_constraint("ck_finance_subcategories_parent", "finance_subcategories", type_="check")
    op.create_check_constraint("ck_finance_subcategories_parent", "finance_subcategories", NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_finance_subcategories_parent", "finance_subcategories", type_="check")
    op.create_check_constraint("ck_finance_subcategories_parent", "finance_subcategories", OLD_CHECK)
    op.drop_constraint("ck_finance_bills_usage", "finance_bills", type_="check")
    op.drop_column("finance_bills", "usage")
    op.drop_column("finance_bills", "auto_link")
    op.drop_column("finance_bills", "recurring_kind")
    op.drop_column("finance_bills", "merchant")
    op.drop_column("finance_plans", "category_mappings")
    op.drop_column("finance_plans", "budgets")
