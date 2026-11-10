"""Customer billing: a tax-inclusive credit balance next to the net one, and invoices settled from it (expand only).

docs/billing.md#account-credit.

* ``billing_accounts`` gains ``credit_gross_minor`` (bigint, not null, default 0, never negative): credit that already
  includes tax (a credit note on a paid invoice, money a card provider collected beyond what was due). It settles an issued
  invoice after tax, as a payment. ``credit_balance_minor`` stays what it was and is now the net balance only (a downgrade's
  prorated credit, which lowers the taxable subtotal of the next invoice). Existing rows keep their balance in
  ``credit_balance_minor`` and so keep today's behaviour; nothing is moved or guessed.
* ``ck_billing_payments_method`` also allows ``account_credit``: the payment row that records an invoice settled from the
  tax-inclusive balance. The constraint is replaced by a wider one, so every existing row still passes.
* ``anum_platform_reader`` may also read ``billing_payments.method``: the console's "collected this month" leaves out
  payments made from account credit (no new money arrived).

Chained after ``0161_finance_bank_loans``.

Expand only. The previous release runs against this schema: it never writes the new column (the default applies), never
writes the new method and reads a payment's method as text. Rollback: ``alembic downgrade 0161_finance_bank_loans`` drops
the column and narrows the method check again; it refuses while a payment made from account credit exists (production
restores a backup, because dropping the column loses the tax-inclusive balances).
"""

import sqlalchemy as sa
from alembic import op

revision = "0164_billing_gross_credit"
down_revision = "0161_finance_bank_loans"
branch_labels = None
depends_on = None

ACCOUNTS = "billing_accounts"
PAYMENTS = "billing_payments"
METHODS = ("bank_transfer", "cash", "card", "stripe", "other")
CREDIT = "account_credit"


def _in(values: tuple[str, ...]) -> str:
    return "method in (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column(ACCOUNTS, sa.Column("credit_gross_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_billing_accounts_credit_gross", ACCOUNTS, "credit_gross_minor >= 0")
    op.drop_constraint("ck_billing_payments_method", PAYMENTS, type_="check")
    op.create_check_constraint("ck_billing_payments_method", PAYMENTS, _in((*METHODS, CREDIT)))
    op.execute(f"grant select (method) on {PAYMENTS} to anum_platform_reader")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute(f"revoke select (method) on {PAYMENTS} from anum_platform_reader")
    op.drop_constraint("ck_billing_payments_method", PAYMENTS, type_="check")
    op.create_check_constraint("ck_billing_payments_method", PAYMENTS, _in(METHODS))
    op.drop_constraint("ck_billing_accounts_credit_gross", ACCOUNTS, type_="check")
    op.drop_column(ACCOUNTS, "credit_gross_minor")
