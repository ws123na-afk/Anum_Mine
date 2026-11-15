"""Matching across currencies: a link settles an obligation in its own currency (expand only).

docs/finance-matching.md#across-currencies.

* ``finance_links`` gains three nullable columns. ``amount`` and ``currency`` stay the bank side (the account's currency,
  checked against the payment by ``finance_links_within_amount`` exactly as before); ``settled_amount`` with
  ``settled_currency`` is what the link pays of a bill or debt in that obligation's own currency, as the bank printed it
  (the original amount of a card purchase); ``fx`` (jsonb) keeps the conversion evidence the link was made on (the original
  and billed amounts, the rate with its kind and source, the FX fee kept apart). Checks: both settled columns or neither, a
  three-letter currency, an amount above zero and another currency than the bank side.
* ``finance_match_records`` (``0184``) gains ``fx`` (jsonb, nullable): the same evidence on the decision the owner reviews.

Both tables already have forced RLS with the tenant and owner-only policies; new columns need no new policy.

Chained after ``0187_pf_alert_channels`` on ``main``. Developed on a branch where ``0186_pf_debts`` was the head; renumber or
re-point at merge if the chain moved.

Expand only. The previous release ignores the new columns (every existing row reads null: a link in one currency, as
before). Rollback: ``alembic downgrade`` drops the columns (only for an empty test workspace; production restores a backup,
because dropping them loses which links settled another currency).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0188_pf_fx_matching"
down_revision = "0186_pf_debts"
branch_labels = None
depends_on = None

LINKS = "finance_links"
MATCHES = "finance_match_records"


def upgrade() -> None:
    op.add_column(LINKS, sa.Column("settled_amount", sa.Numeric(20, 4), nullable=True))
    op.add_column(LINKS, sa.Column("settled_currency", sa.String(3), nullable=True))
    op.add_column(LINKS, sa.Column("fx", JSONB, nullable=True))
    op.create_check_constraint("ck_finance_links_settled_pair", LINKS, "(settled_amount is null) = (settled_currency is null)")
    op.create_check_constraint("ck_finance_links_settled_currency", LINKS,
                               "settled_currency is null or (settled_currency ~ '^[A-Z]{3}$' and settled_currency <> currency)")
    op.create_check_constraint("ck_finance_links_settled_amount", LINKS, "settled_amount is null or settled_amount > 0")
    op.add_column(MATCHES, sa.Column("fx", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column(MATCHES, "fx")
    op.drop_constraint("ck_finance_links_settled_amount", LINKS, type_="check")
    op.drop_constraint("ck_finance_links_settled_currency", LINKS, type_="check")
    op.drop_constraint("ck_finance_links_settled_pair", LINKS, type_="check")
    op.drop_column(LINKS, "fx")
    op.drop_column(LINKS, "settled_currency")
    op.drop_column(LINKS, "settled_amount")
