"""Commerce amounts as exact decimals: ``double precision`` to ``numeric(18,4)`` (docs/commerce.md#data-model).

Prices, order totals, line amounts, refunds and fees were binary floats, so sums and rounding could pick up
float error (``0.1 + 0.2``) and a half cent could round the wrong way. They become ``numeric(18,4)``: four
places keep three-decimal currencies (KWD, BHD, OMR) exact, and the API reads them as ``Decimal``. Existing
values are rounded to four places on the way (a stored ``19.990000000000002`` becomes ``19.99``).

Chained after ``0170_model_usage_daily``.

Compatible with the previous release: it binds floats, which PostgreSQL casts to numeric on write, and its
``Double`` columns read numeric values back as numbers, so it keeps running against the new types. The
change rewrites the six commerce tables once under a short exclusive lock (they hold synced channel data,
not large histories). No column, policy or grant is added or removed. Rollback:
``alembic downgrade 0170_model_usage_daily`` turns the columns back into ``double precision``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0171_commerce_money_numeric"
down_revision = "0170_model_usage_daily"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
# (table, column, upgrade cast, downgrade cast): literal SQL per column, no SQL built from strings.
COLUMNS = (
    ("commerce_products", "cost_price", "round(cost_price::numeric, 4)", "cost_price::double precision"),
    ("commerce_listings", "price", "round(price::numeric, 4)", "price::double precision"),
    ("commerce_listings", "buy_box_price", "round(buy_box_price::numeric, 4)", "buy_box_price::double precision"),
    ("commerce_listings", "lowest_competitor_price", "round(lowest_competitor_price::numeric, 4)",
     "lowest_competitor_price::double precision"),
    ("commerce_orders", "total_amount", "round(total_amount::numeric, 4)", "total_amount::double precision"),
    ("commerce_order_lines", "unit_price", "round(unit_price::numeric, 4)", "unit_price::double precision"),
    ("commerce_order_lines", "total", "round(total::numeric, 4)", "total::double precision"),
    ("commerce_returns", "amount", "round(amount::numeric, 4)", "amount::double precision"),
    ("commerce_fees", "amount", "round(amount::numeric, 4)", "amount::double precision"),
)


def upgrade() -> None:
    for table, column, to_money, _ in COLUMNS:
        op.alter_column(table, column, type_=MONEY, existing_type=sa.Double(), postgresql_using=to_money)


def downgrade() -> None:
    for table, column, _, to_double in COLUMNS:
        op.alter_column(table, column, type_=sa.Double(), existing_type=MONEY, postgresql_using=to_double)
