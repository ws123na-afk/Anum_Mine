"""Finance debts, goals, bills and budgets (expand only). docs/finance.md#debts-and-goals, #bills-and-budgets.

* ``finance_goals.paused`` (default false): a paused goal keeps what is saved but is not set aside
  by safe to spend and is left out of the commitments and the savings outlook.
* ``finance_debts.original_balance`` (nullable): what was owed when the person started tracking the
  debt, so payoff progress is ``1 - balance / original_balance``. Never invented: empty until the
  person enters it (the web fills it with the balance when a debt is added).
* ``finance_plans.debt_plans`` (JSON list, default empty): per currency, the payoff strategy
  (``avalanche`` or ``snowball``) and the planned monthly payment.
* ``finance_plans.budget_alerts`` (JSON list, default empty): per Guardian category cap, the alert
  threshold in percent and whether to alert at it and at 100 percent.
* ``finance_links`` admits two more dimensions: ``bill`` (a recorded payment matched to one due date
  of a bill, target ``finance_bill_due`` / ``<bill id>:<YYYY-MM-DD>``) and ``debt`` (a recorded payment
  towards a debt, target ``finance_debt``). A bill is paid only when such a link exists.

Row level security is unchanged: the tables already have their forced owner policies. Expand only:
new nullable columns, columns with defaults and a widened check. The previous release ignores the
new columns and never writes a ``bill`` or ``debt`` link. Chained after ``0139_finance_pdf_templates``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0142_finance_debts_bills"
down_revision = "0141_attendance_report_submit"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 4)
OLD = "dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project')"
NEW = "dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project', 'bill', 'debt')"


def upgrade() -> None:
    op.add_column("finance_goals", sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("finance_debts", sa.Column("original_balance", MONEY, nullable=True))
    op.create_check_constraint("ck_finance_debts_original_balance", "finance_debts", "original_balance is null or original_balance >= 0")
    op.add_column("finance_plans", sa.Column("debt_plans", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("finance_plans", sa.Column("budget_alerts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", NEW)


def downgrade() -> None:
    op.execute("delete from finance_links where dimension in ('bill', 'debt')")
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", OLD)
    op.drop_column("finance_plans", "budget_alerts")
    op.drop_column("finance_plans", "debt_plans")
    op.drop_constraint("ck_finance_debts_original_balance", "finance_debts", type_="check")
    op.drop_column("finance_debts", "original_balance")
    op.drop_column("finance_goals", "paused")
