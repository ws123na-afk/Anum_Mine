"""Ready to sell: company details for the legal pages and invoices, Moyasar card payments, refunds.

Expand only (docs/selling-readiness.md, docs/billing.md). The previous release ignores all of it.

* ``billing_company_profiles`` (issuer tenant, one row): the platform owner's legal name,
  registration, address, contacts, data protection officer, server location, governing law,
  the other facts the legal pages need (``legal_terms``, a map of allowed keys) and which
  legal pages a lawyer reviewed (``published_docs``). Forced RLS on ``anum.tenant_id`` like
  every billing table. The public legal pages read it through ``anum_platform_reader``
  (``INHERIT FALSE``): a column grant without ``updated_by`` and a policy that admits only
  the row of the issuer named by ``anum.billing_issuer`` (the configured issuer, set by the API).
  The same role may read whether that issuer turned Stripe or Moyasar on (two booleans of
  ``billing_settings``), so the public subprocessor list names only the providers in use.
* ``billing_settings`` gains the Moyasar adapter: ``moyasar_enabled`` (off by default), the
  secret key and webhook secret token as Fernet ciphertexts, and the key mode. Nothing is in
  the repository or the environment; the platform owner enters them in the console.
* ``billing_refunds`` (customer tenant): one row per refund of a recorded payment, through the
  provider or recorded by hand, with an idempotency key unique per issuer and the provider's
  refund id unique per issuer and provider.

Both new tables get the tenant purge policy of 0079/0094 (organisation-wide) so an
organisation deletion removes them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0123_selling_readiness"
down_revision = "0122_finance_ibkr_live"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
ISSUER = "nullif(current_setting('anum.billing_issuer', true), '')"
TABLES = ("billing_company_profiles", "billing_refunds")
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
# What the public legal pages may read (everything except who changed it).
PUBLIC_COLUMNS = (
    "tenant_id, legal_name, trading_name, registration_number, registration_country, registration_authority, address, "
    "contact_email, support_contact, phone, vat_number, dpo_name, dpo_email, security_email, server_location, "
    "hosting_provider, governing_law, legal_terms, published_docs, updated_at"
)


def _purge_policy(table: str) -> None:
    op.execute(f"grant select (tenant_id, ctid), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging' and j.kind = 'organisation_deletion'
          )
        )
        with check (false)
        """  # nosec B608 - fixed table names
    )


def _text(name: str, length: int) -> sa.Column:
    return sa.Column(name, sa.String(length), nullable=False, server_default="")


def upgrade() -> None:
    op.create_table(
        "billing_company_profiles",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        _text("legal_name", 200),
        _text("trading_name", 200),
        _text("registration_number", 80),
        _text("registration_country", 2),
        _text("registration_authority", 200),
        _text("address", 600),
        _text("contact_email", 254),
        _text("support_contact", 254),
        _text("phone", 40),
        _text("vat_number", 40),
        _text("dpo_name", 160),
        _text("dpo_email", 254),
        _text("security_email", 254),
        _text("server_location", 200),
        _text("hosting_provider", 20),
        _text("governing_law", 300),
        sa.Column("legal_terms", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("published_docs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_by", sa.String(160), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("hosting_provider in ('', 'oracle', 'render', 'other')", name="ck_billing_company_hosting"),
    )

    op.add_column("billing_settings", sa.Column("moyasar_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("billing_settings", sa.Column("moyasar_secret_ciphertext", sa.Text(), nullable=True))
    op.add_column("billing_settings", sa.Column("moyasar_webhook_secret_ciphertext", sa.Text(), nullable=True))
    op.add_column("billing_settings", sa.Column("moyasar_key_mode", sa.String(10), nullable=True))

    op.create_table(
        "billing_refunds",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("invoice_id", sa.String(80), sa.ForeignKey("billing_invoices.id"), nullable=False),
        sa.Column("payment_id", sa.String(80), sa.ForeignKey("billing_payments.id"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("provider_refund_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("credit_note_id", sa.String(80), nullable=True),
        sa.Column("failure", sa.String(300), nullable=True),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor > 0", name="ck_billing_refunds_amount"),
        sa.CheckConstraint("status in ('pending', 'succeeded', 'failed')", name="ck_billing_refunds_status"),
    )
    op.create_index("ix_billing_refunds_payment", "billing_refunds", ["tenant_id", "payment_id"])
    op.create_index("uq_billing_refunds_key", "billing_refunds", ["issuer_tenant_id", "idempotency_key"], unique=True)
    op.create_index(
        "uq_billing_refunds_provider", "billing_refunds", ["issuer_tenant_id", "provider", "provider_refund_id"], unique=True,
        postgresql_where=sa.text("provider_refund_id is not null"),
    )

    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(f"create policy tenant_isolation_{table} on {table} using ({TENANT}) with check ({TENANT})")
        _purge_policy(table)

    # The public legal pages: the configured issuer's row only, without who changed it.
    op.execute(f"grant select ({PUBLIC_COLUMNS}) on billing_company_profiles to anum_platform_reader")
    op.execute(
        "create policy billing_company_public on billing_company_profiles for select to anum_platform_reader "
        f"using (tenant_id = {ISSUER})"
    )
    # Which payment providers the configured issuer turned on, for the generated subprocessor list.
    op.execute("grant select (tenant_id, stripe_enabled, moyasar_enabled) on billing_settings to anum_platform_reader")
    op.execute(
        "create policy billing_settings_public on billing_settings for select to anum_platform_reader "
        f"using (tenant_id = {ISSUER})"
    )


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute("drop policy if exists billing_settings_public on billing_settings")
    for table in TABLES:
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
    op.drop_table("billing_refunds")
    for column in ("moyasar_key_mode", "moyasar_webhook_secret_ciphertext", "moyasar_secret_ciphertext", "moyasar_enabled"):
        op.drop_column("billing_settings", column)
    op.drop_table("billing_company_profiles")
