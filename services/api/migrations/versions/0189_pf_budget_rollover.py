"""Personal finance: budget rollover, each budget's terms period by period (expand only).

docs/finance.md#budget-rollover.

* ``finance_plans`` gains ``budget_rollovers`` (jsonb, not null, default ``[]``): for each budget, the terms it used from
  a period on (amount, currency, categories, subcategories, the rollover: ``none``, ``surplus`` or ``capped`` with its cap,
  and the overspend: ``absorb`` or ``carry``). An entry is written only for the period the owner saves the budget in, so
  an earlier period keeps its own terms and its carried figures are never rewritten. The row keeps its forced RLS of
  ``0057`` (tenant, workspace and the restrictive owner-only policy) and is exported and deleted with Finance as before.

Chained after ``0188_pf_fx_matching`` on ``main``. On this branch the newest revision was ``0186_pf_debts``: renumber
``down_revision`` at merge (file name and ids stay ``0189_pf_budget_rollover``).

Expand only. The previous release never reads or writes the column (its upserts of ``finance_plans`` set only the columns
it knows, and the default fills new rows); its budgets then show without carried money. Rollback: ``alembic downgrade
<previous head>`` drops the column and with it the rollover history (test databases only; production restores a backup).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0189_pf_budget_rollover"
down_revision = "0186_pf_debts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_plans", sa.Column("budget_rollovers", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("finance_plans", "budget_rollovers")
