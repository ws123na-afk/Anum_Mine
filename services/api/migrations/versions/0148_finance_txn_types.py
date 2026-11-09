"""Transaction types on the canonical transaction (expand only).

docs/finance.md#transaction-types; the November 2026 audit (docs/audits/2026-11-09-finance-accounting-audit.md),
findings C1 to C4.

``finance_transactions`` gains five nullable columns:

* ``transaction_type``: what the row is (``income``, ``expense``, ``internal_transfer``, ``card_payment``,
  ``loan_received``, ``loan_principal``, ``interest``, ``refund``, ``cashback``, ``bank_fee``,
  ``investment_contribution``, ``investment_withdrawal``, ``owner_contribution``, ``owner_drawing``,
  ``reimbursement``, ``receivable_collection``, ``payable_settlement``, ``fx_exchange``, ``tax``, ``reversal``
  or ``uncategorized``). It decides how the row counts (``flows.py``).
* ``type_source`` (``rule``, ``owner``, ``system``, ``sms``, ``match``), ``type_confidence`` (0 to 1) and
  ``type_reason`` (a short machine code).
* ``related_transaction_id``: the person's own purchase a refund gives back (a match or a suggestion).

Checks keep the values to those lists. Row level security is unchanged: the table already has its forced
tenant and owner policies (``0056_finance_ledger``), so no migration may update its rows (it has no tenant
context, and nothing here bypasses RLS). The backfill is therefore the deterministic rule
``txn_types.derive``: a row with no stored type is read as the type its category, sign and account kind
stand for, and gets that type stored the next time it is saved. The previous release ignores the columns.

Rollback: ``downgrade`` drops the columns; the previous release counts by category as before.
Chained after ``0147_finance_personal_money``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0148_finance_txn_types"
down_revision = "0147_finance_personal_money"
branch_labels = None
depends_on = None

TABLE = "finance_transactions"
TYPES = (
    "income", "expense", "internal_transfer", "card_payment", "loan_received", "loan_principal", "interest", "refund", "cashback",
    "bank_fee", "investment_contribution", "investment_withdrawal", "owner_contribution", "owner_drawing", "reimbursement",
    "receivable_collection", "payable_settlement", "fx_exchange", "tax", "reversal", "uncategorized",
)
SOURCES = ("rule", "owner", "system", "sms", "match")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} is null or {column} in (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("transaction_type", sa.String(30), nullable=True))
    op.add_column(TABLE, sa.Column("type_source", sa.String(10), nullable=True))
    op.add_column(TABLE, sa.Column("type_confidence", sa.Numeric(3, 2), nullable=True))
    op.add_column(TABLE, sa.Column("type_reason", sa.String(40), nullable=True))
    op.add_column(TABLE, sa.Column("related_transaction_id", sa.String(80), nullable=True))
    op.create_check_constraint("ck_finance_transactions_type", TABLE, _in("transaction_type", TYPES))
    op.create_check_constraint("ck_finance_transactions_type_source", TABLE, _in("type_source", SOURCES))
    op.create_check_constraint("ck_finance_transactions_type_confidence", TABLE,
                               "type_confidence is null or (type_confidence >= 0 and type_confidence <= 1)")


def downgrade() -> None:
    op.drop_constraint("ck_finance_transactions_type_confidence", TABLE, type_="check")
    op.drop_constraint("ck_finance_transactions_type_source", TABLE, type_="check")
    op.drop_constraint("ck_finance_transactions_type", TABLE, type_="check")
    for column in ("related_transaction_id", "type_reason", "type_confidence", "type_source", "transaction_type"):
        op.drop_column(TABLE, column)
