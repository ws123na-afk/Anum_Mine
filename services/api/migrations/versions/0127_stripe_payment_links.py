"""Stripe payment links and payments on Business invoices (docs/business.md#payment-links).

The owner with a UAE company takes cards through Stripe (docs/selling-readiness.md#stripe-uae), so
a Business invoice's payment link can be a Stripe Payment Link and the payment it brings in is a
Stripe payment. Two check constraints from ``0126_business_invoice_send`` widen to allow
``stripe`` next to ``moyasar``:

* ``ck_business_invoice_payment_links_provider``: ``provider in ('moyasar', 'stripe')``;
* ``ck_business_invoice_payments_provider``: a provider payment is Moyasar's or Stripe's, always
  with its provider payment id.

Expand only: every row the previous release writes (Moyasar or none) still passes, and the
previous release never writes ``stripe``. The wider constraints are added ``not valid`` and then
validated, so the table is not locked against writes while existing rows are checked. No table,
column, grant or policy changes; the uniqueness of a provider payment id per workspace and
provider (``uq_business_invoice_payments_provider``) already covers Stripe. Rollback never
reverses it.

Stripe refunds of customer billing payments need no schema change: ``billing_refunds.provider``
is free text (``moyasar`` or ``stripe``) and ``uq_billing_refunds_provider`` keeps Stripe's refund
id unique per issuer and provider.

Numbered 0127 and chained after ``0126_business_invoice_send``, the head of ``main``.
"""

from alembic import op

revision = "0127_stripe_payment_links"
down_revision = "0126_business_invoice_send"
branch_labels = None
depends_on = None

LINKS = "business_invoice_payment_links"
PAYMENTS = "business_invoice_payments"


def _replace(table: str, name: str, condition: str) -> None:
    op.execute(f"alter table {table} drop constraint if exists {name}")
    op.execute(f"alter table {table} add constraint {name} check ({condition}) not valid")
    op.execute(f"alter table {table} validate constraint {name}")


def upgrade() -> None:
    _replace(LINKS, "ck_business_invoice_payment_links_provider", "provider in ('moyasar', 'stripe')")
    _replace(PAYMENTS, "ck_business_invoice_payments_provider",
             "(provider is null and provider_payment_id is null) or (provider in ('moyasar', 'stripe') and provider_payment_id is not null)")


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    _replace(LINKS, "ck_business_invoice_payment_links_provider", "provider in ('moyasar')")
    _replace(PAYMENTS, "ck_business_invoice_payments_provider",
             "(provider is null and provider_payment_id is null) or (provider = 'moyasar' and provider_payment_id is not null)")
