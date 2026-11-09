"""The emergency reserve sized in months of essential spending (expand only). docs/finance.md#emergency-reserve.

* ``finance_goals.reserve_months`` (nullable small integer, 1 to 24): an emergency reserve goal (purpose
  ``emergency``) sized in months of essential spending. Empty: a fixed amount (the goal's ``target_amount``), as
  every goal was before. The goal's target stays the figure Safe to spend and the forecast use; the months say
  how the person sized it, so the reserve page can show today's recalculated target next to it.

Row level security is unchanged: ``finance_goals`` already has forced RLS with its tenant, owner-only and purge
policies (``0131``/``0147``), and the column is exported and deleted with the goal. Expand only: a new nullable
column and its check. The previous release ignores it. Rollback: ``alembic downgrade 0155_books_assets_fx``
drops the column (a reserve sized in months then reads as its fixed target, which is what it already stores).
Chained after ``0155_books_assets_fx``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0156_finance_reserve"
down_revision = "0155_books_assets_fx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_goals", sa.Column("reserve_months", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("ck_finance_goals_reserve_months", "finance_goals",
                               "reserve_months is null or (reserve_months between 1 and 24 and purpose = 'emergency')")


def downgrade() -> None:
    op.drop_constraint("ck_finance_goals_reserve_months", "finance_goals", type_="check")
    op.drop_column("finance_goals", "reserve_months")
