"""Statement matching: the import's reconciliation result and own-transfer links (expand only).

docs/finance.md#statement-matching and #own-transfers.

* ``finance_imports.reconciliation`` (JSON object, nullable): the statement's balance check when it was
  imported: ``status`` (``tied``, ``differs`` or ``unknown``), the opening and closing balances, the rows'
  total and the difference, as decimal strings. ``accounts/overview`` reads a tied one as the account's
  last reconciliation. Older imports have none.
* ``finance_links`` admits one more dimension, ``transfer``: the money out of one of the person's accounts
  linked to the money into another of them (target ``finance_transfer``, the money-in side's canonical id).

Row level security is unchanged: both tables already have their forced owner policies. Expand only: a
nullable column and a widened check. The previous release ignores the column and never writes a
``transfer`` link. Chained after ``0144_installment_stmt``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0145_finance_statement_match"
down_revision = "0144_installment_stmt"
branch_labels = None
depends_on = None

OLD = "dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project', 'bill', 'debt')"
NEW = "dimension in ('vehicle', 'purchase', 'document', 'business', 'trip', 'project', 'bill', 'debt', 'transfer')"


def upgrade() -> None:
    op.add_column("finance_imports", sa.Column("reconciliation", postgresql.JSONB(), nullable=True))
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", NEW)


def downgrade() -> None:
    op.execute("delete from finance_links where dimension = 'transfer'")
    op.drop_constraint("ck_finance_links_dimension", "finance_links", type_="check")
    op.create_check_constraint("ck_finance_links_dimension", "finance_links", OLD)
    op.drop_column("finance_imports", "reconciliation")
