"""Statement import history: what happened to every row (expand only).

docs/finance.md#import-history.

* ``finance_imports.attached_count``, ``held_count``, ``skipped_count``, ``dropped_count``, ``corrected_count``
  (integers, default 0): rows attached to a record already in the ledger (an SMS, Apple Pay or hand entry), held
  for review, left out by the person, left out by the reader (unreadable, outside the statement period), and
  records corrected to the bank-billed amount. Before this an import stored only what it added and a duplicate
  count that also counted attached rows.
* ``finance_imports.dispositions`` (JSON array, nullable): one item per statement row: line, date, amount,
  description, the disposition (``imported``, ``held_for_review``, ``attached_to_existing``, ``duplicate_skipped``,
  ``unticked``, ``dropped_by_reader``), the reason and the record it went to. Older imports have none.

Row level security is unchanged: ``finance_imports`` already has its forced owner policies. Expand only: columns
with defaults and a nullable column; the previous release ignores them. Chained after
``0148_finance_txn_types``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0149_finance_import_rows"
down_revision = "0148_finance_txn_types"
branch_labels = None
depends_on = None

COUNTS = ("attached_count", "held_count", "skipped_count", "dropped_count", "corrected_count")


def upgrade() -> None:
    for name in COUNTS:
        op.add_column("finance_imports", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
        op.create_check_constraint(f"ck_finance_imports_{name}", "finance_imports", f"{name} >= 0")
    op.add_column("finance_imports", sa.Column("dispositions", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("finance_imports", "dispositions")
    for name in reversed(COUNTS):
        op.drop_constraint(f"ck_finance_imports_{name}", "finance_imports", type_="check")
        op.drop_column("finance_imports", name)
