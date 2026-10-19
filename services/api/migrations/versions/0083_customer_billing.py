"""Customer billing (docs/billing.md).

ANUM bills its customer organisations. Every table below has ``tenant_id`` and forced RLS
on ``anum.tenant_id`` (``tenant_isolation_<table>``); billing is organisation-wide, so
there is no workspace predicate. Rows of the issuer (the platform owner's organisation:
plans, settings, number sequences, webhook events) live in the issuer's tenant; rows
about a customer live in that customer's tenant and carry ``issuer_tenant_id``.

Cross-tenant reads never bypass RLS:

* ``anum_platform_reader`` (NOLOGIN, granted ``WITH INHERIT FALSE``) gets column grants on
  amounts, statuses and dates of accounts, subscriptions, invoices and payments, and on
  the public columns of plans. Its policies admit only rows whose issuer is
  ``anum.billing_issuer`` (and, for plans, only published, active ones). The API sets that
  value to the caller's own home tenant after the ``platform:operate`` check, or to the
  configured issuer for the public pricing page.
* ``anum_maintenance`` discovers billing work (subscriptions and open invoices, ids,
  statuses and dates only) and reads usage counters of one organisation (ids, sizes,
  timestamps of tasks, files, memberships, workspaces, invitations and chat turns) when
  ``anum.billing_tenant`` names it; sign-up abuse checks read the hashed IP and account
  id of recent sign-ups.

Expand only: new tables, grants and policies. The previous release ignores them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083_customer_billing"
down_revision = "0076_business_support"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
ISSUER = "nullif(current_setting('anum.billing_issuer', true), '')"
BILLED = "nullif(current_setting('anum.billing_tenant', true), '')"
TABLES = (
    "billing_plans",
    "billing_settings",
    "billing_accounts",
    "billing_subscriptions",
    "billing_seats",
    "billing_usage",
    "billing_invoices",
    "billing_invoice_lines",
    "billing_credit_notes",
    "billing_payments",
    "billing_number_sequences",
    "billing_webhook_events",
    "billing_signups",
)
SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused", "cancelled")
INVOICE_STATUSES = ("draft", "open", "paid", "void", "uncollectible")
# Platform reader: what the console may see of a customer's billing (no names of people, no files).
READER_COLUMNS = {
    "billing_accounts": "tenant_id, issuer_tenant_id, display_name, state, credit_balance_minor",
    "billing_subscriptions": (
        "tenant_id, issuer_tenant_id, plan_name, status, price_minor, currency, interval, seats, per_seat, "
        "trial_end, current_period_end, cancel_at_period_end"
    ),
    "billing_invoices": "tenant_id, issuer_tenant_id, status, currency, total_minor, amount_paid_minor, amount_due_minor, issued_at, due_at",
    "billing_payments": "tenant_id, issuer_tenant_id, amount_minor, currency, received_at",
}
PLAN_COLUMNS = (
    "id, tenant_id, code, name, description, price_minor, currency, interval, trial_days, per_seat, limits, features, "
    "published, active, sort_order"
)
# Usage meters of one organisation, read as anum_maintenance with anum.billing_tenant set.
USAGE_COLUMNS = {
    "tasks": "tenant_id, created_at",
    "workspace_files": "tenant_id, size_bytes",
    "workspace_memberships": "tenant_id, user_id, active",
    "workspaces": "tenant_id, id, archived_at",
    "workspace_invitations": "tenant_id, status, expires_at",
    "conversation_turns": "tenant_id, role, created_at",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _id() -> sa.Column:
    return sa.Column("id", sa.String(80), primary_key=True)


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id", name=None), nullable=False)


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")


def _created() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False, server_default=sa.func.now())


def _updated() -> sa.Column:
    return sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now())


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(f"'{default}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        _id(),
        _tenant(),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        _money("price_minor"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("interval", sa.String(10), nullable=False, server_default="month"),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("per_seat", sa.Boolean(), nullable=False, server_default=sa.false()),
        _jsonb("limits", "{}"),
        _jsonb("features", "[]"),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(160), nullable=False),
        _created(),
        _updated(),
        sa.CheckConstraint("price_minor >= 0", name="ck_billing_plans_price"),
        sa.CheckConstraint(_in("interval", ("month", "year")), name="ck_billing_plans_interval"),
        sa.CheckConstraint("trial_days between 0 and 365", name="ck_billing_plans_trial"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_billing_plans_currency"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_billing_plans_code"),
    )
    op.create_table(
        "billing_settings",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("issuer_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("issuer_address", sa.String(600), nullable=False, server_default=""),
        sa.Column("issuer_vat_number", sa.String(40), nullable=False, server_default=""),
        sa.Column("issuer_email", sa.String(254), nullable=False, server_default=""),
        sa.Column("bank_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("bank_account_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("bank_iban", sa.String(40), nullable=False, server_default=""),
        sa.Column("bank_swift", sa.String(20), nullable=False, server_default=""),
        sa.Column("invoice_prefix", sa.String(12), nullable=False, server_default="INV"),
        sa.Column("credit_note_prefix", sa.String(12), nullable=False, server_default="CN"),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False, server_default="14"),
        _jsonb("tax_rules", '{"SA": 1500}'),
        sa.Column("default_tax_bp", sa.Integer(), nullable=False, server_default="0"),
        _jsonb("reminder_days", "[1, 7, 14]"),
        sa.Column("grace_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("sending_workspace_id", sa.String(80), nullable=True),
        sa.Column("stripe_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stripe_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("stripe_webhook_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("stripe_key_mode", sa.String(10), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=False),
        _updated(),
        sa.CheckConstraint("payment_terms_days between 0 and 120", name="ck_billing_settings_terms"),
        sa.CheckConstraint("grace_days between 0 and 90", name="ck_billing_settings_grace"),
        sa.CheckConstraint("default_tax_bp between 0 and 10000", name="ck_billing_settings_tax"),
    )
    op.create_table(
        "billing_accounts",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(254), nullable=False, server_default=""),
        sa.Column("company_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("address_line1", sa.String(200), nullable=False, server_default=""),
        sa.Column("address_line2", sa.String(200), nullable=False, server_default=""),
        sa.Column("city", sa.String(120), nullable=False, server_default=""),
        sa.Column("postal_code", sa.String(20), nullable=False, server_default=""),
        sa.Column("country", sa.String(2), nullable=False, server_default="SA"),
        sa.Column("vat_number", sa.String(40), nullable=False, server_default=""),
        _money("credit_balance_minor"),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("onboarding_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        _created(),
        _updated(),
        sa.CheckConstraint(_in("state", ("active", "read_only")), name="ck_billing_accounts_state"),
        sa.CheckConstraint("credit_balance_minor >= 0", name="ck_billing_accounts_credit"),
    )
    op.create_index("ix_billing_accounts_issuer", "billing_accounts", ["issuer_tenant_id"])
    op.create_table(
        "billing_subscriptions",
        _id(),
        _tenant(),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("plan_id", sa.String(80), nullable=False),
        sa.Column("plan_code", sa.String(60), nullable=False),
        sa.Column("plan_name", sa.String(120), nullable=False),
        _money("price_minor"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("per_seat", sa.Boolean(), nullable=False, server_default=sa.false()),
        _jsonb("limits", "{}"),
        _jsonb("features", "[]"),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trial_end", TS, nullable=True),
        sa.Column("current_period_start", TS, nullable=False),
        sa.Column("current_period_end", TS, nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled_at", TS, nullable=True),
        sa.Column("pending_plan_id", sa.String(80), nullable=True),
        sa.Column("pending_seats", sa.Integer(), nullable=True),
        sa.Column("paused_at", TS, nullable=True),
        sa.Column("past_due_since", TS, nullable=True),
        sa.Column("grace_until", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _created(),
        _updated(),
        sa.CheckConstraint(_in("status", SUBSCRIPTION_STATUSES), name="ck_billing_subscriptions_status"),
        sa.CheckConstraint("seats between 1 and 100000", name="ck_billing_subscriptions_seats"),
        sa.CheckConstraint("current_period_end > current_period_start", name="ck_billing_subscriptions_period"),
    )
    # One subscription that is not cancelled per organisation.
    op.create_index(
        "uq_billing_subscriptions_live", "billing_subscriptions", ["tenant_id"],
        unique=True, postgresql_where=sa.text("status <> 'cancelled'"),
    )
    op.create_index("ix_billing_subscriptions_issuer", "billing_subscriptions", ["issuer_tenant_id", "status"])
    op.create_table(
        "billing_seats",
        _id(),
        _tenant(),
        sa.Column("subscription_id", sa.String(80), sa.ForeignKey("billing_subscriptions.id"), nullable=False),
        sa.Column("from_seats", sa.Integer(), nullable=False),
        sa.Column("to_seats", sa.Integer(), nullable=False),
        sa.Column("prorated_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("changed_by", sa.String(160), nullable=False),
        _created(),
    )
    op.create_table(
        "billing_usage",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("meter", sa.String(30), primary_key=True),
        sa.Column("quantity", sa.BigInteger(), nullable=False, server_default="0"),
        _updated(),
        sa.CheckConstraint("period ~ '^[0-9]{4}-[0-9]{2}$'", name="ck_billing_usage_period"),
    )
    op.create_table(
        "billing_invoices",
        _id(),
        _tenant(),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("subscription_id", sa.String(80), nullable=True),
        sa.Column("number", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period_start", TS, nullable=True),
        sa.Column("period_end", TS, nullable=True),
        sa.Column("issued_at", TS, nullable=True),
        sa.Column("due_at", TS, nullable=True),
        _money("subtotal_minor"),
        sa.Column("tax_rate_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tax_country", sa.String(2), nullable=False, server_default=""),
        _money("tax_minor"),
        _money("total_minor"),
        _money("amount_paid_minor"),
        _money("amount_credited_minor"),
        _money("amount_due_minor"),
        _jsonb("bill_to", "{}"),
        _jsonb("issuer", "{}"),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("reminders_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reminder_at", TS, nullable=True),
        sa.Column("last_email", postgresql.JSONB(), nullable=True),
        sa.Column("paid_at", TS, nullable=True),
        sa.Column("voided_at", TS, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _created(),
        _updated(),
        sa.CheckConstraint(_in("status", INVOICE_STATUSES), name="ck_billing_invoices_status"),
        sa.CheckConstraint(_in("kind", ("subscription", "proration", "manual")), name="ck_billing_invoices_kind"),
        sa.CheckConstraint("number is not null or status = 'draft'", name="ck_billing_invoices_number"),
        sa.CheckConstraint("amount_due_minor >= 0 and total_minor >= 0", name="ck_billing_invoices_amounts"),
    )
    op.create_index(
        "uq_billing_invoices_number", "billing_invoices", ["issuer_tenant_id", "number"],
        unique=True, postgresql_where=sa.text("number is not null"),
    )
    op.create_index("ix_billing_invoices_scope_created", "billing_invoices", ["tenant_id", "created_at"])
    op.create_index("ix_billing_invoices_open_due", "billing_invoices", ["status", "due_at"])
    op.create_table(
        "billing_invoice_lines",
        _id(),
        _tenant(),
        sa.Column("invoice_id", sa.String(80), sa.ForeignKey("billing_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("period_start", TS, nullable=True),
        sa.Column("period_end", TS, nullable=True),
        sa.CheckConstraint(_in("kind", ("plan", "proration", "credit", "adjustment")), name="ck_billing_invoice_lines_kind"),
    )
    op.create_index("ix_billing_invoice_lines_invoice", "billing_invoice_lines", ["tenant_id", "invoice_id", "position"])
    op.create_table(
        "billing_credit_notes",
        _id(),
        _tenant(),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("invoice_id", sa.String(80), sa.ForeignKey("billing_invoices.id"), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        _money("subtotal_minor"),
        _money("tax_minor"),
        _money("total_minor"),
        sa.Column("issued_at", TS, nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.CheckConstraint("total_minor > 0", name="ck_billing_credit_notes_total"),
        sa.UniqueConstraint("issuer_tenant_id", "number", name="uq_billing_credit_notes_number"),
    )
    op.create_table(
        "billing_payments",
        _id(),
        _tenant(),
        sa.Column("issuer_tenant_id", sa.String(80), nullable=False),
        sa.Column("invoice_id", sa.String(80), sa.ForeignKey("billing_invoices.id"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(200), nullable=False, server_default=""),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("proof_name", sa.String(255), nullable=True),
        sa.Column("proof_content_type", sa.String(60), nullable=True),
        sa.Column("proof_size", sa.Integer(), nullable=True),
        sa.Column("proof_content", sa.LargeBinary(), nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("provider_payment_id", sa.String(200), nullable=True),
        sa.Column("recorded_by", sa.String(160), nullable=False),
        _created(),
        sa.CheckConstraint("amount_minor > 0", name="ck_billing_payments_amount"),
        sa.CheckConstraint(_in("method", ("bank_transfer", "cash", "card", "stripe", "other")), name="ck_billing_payments_method"),
    )
    op.create_index(
        "uq_billing_payments_provider", "billing_payments", ["issuer_tenant_id", "provider", "provider_payment_id"],
        unique=True, postgresql_where=sa.text("provider_payment_id is not null"),
    )
    op.create_index("ix_billing_payments_invoice", "billing_payments", ["tenant_id", "invoice_id"])
    op.create_table(
        "billing_number_sequences",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(_in("kind", ("invoice", "credit_note")), name="ck_billing_number_sequences_kind"),
    )
    op.create_table(
        "billing_webhook_events",
        sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("event_id", sa.String(200), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("received_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "billing_signups",
        _id(),
        _tenant(),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("email_domain", sa.String(254), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("plan_id", sa.String(80), nullable=False),
        _created(),
    )
    op.create_index("ix_billing_signups_recent", "billing_signups", ["created_at"])
    op.create_index("uq_billing_signups_user", "billing_signups", ["user_id"], unique=True)

    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(f"create policy tenant_isolation_{table} on {table} using ({TENANT}) with check ({TENANT})")

    # Platform console and plan catalog, through anum_platform_reader (INHERIT FALSE).
    for table, columns in READER_COLUMNS.items():
        op.execute(f"grant select ({columns}) on {table} to anum_platform_reader")
        op.execute(
            f"create policy billing_issuer_reader on {table} for select to anum_platform_reader "
            f"using (issuer_tenant_id = {ISSUER})"
        )
    op.execute(f"grant select ({PLAN_COLUMNS}) on billing_plans to anum_platform_reader")
    op.execute(
        "create policy billing_plan_catalog on billing_plans for select to anum_platform_reader "
        f"using (published and active and tenant_id = {ISSUER})"
    )

    # Billing jobs: discovery of work as anum_maintenance (INHERIT FALSE), ids and dates only.
    op.execute(
        "grant select (tenant_id, id, issuer_tenant_id, status, trial_end, current_period_end, cancel_at_period_end, "
        "past_due_since, grace_until) on billing_subscriptions to anum_maintenance"
    )
    op.execute(
        "create policy billing_jobs_subscriptions on billing_subscriptions for select to anum_maintenance "
        "using (status in ('trialing', 'active', 'past_due', 'paused'))"
    )
    op.execute("grant select (tenant_id, id, issuer_tenant_id, status, due_at) on billing_invoices to anum_maintenance")
    op.execute(
        "create policy billing_jobs_invoices on billing_invoices for select to anum_maintenance using (status = 'open')"
    )
    # Key rotation (python -m anum_api.rotate_secrets): which issuers have settings, the id only.
    op.execute("grant select (tenant_id) on billing_settings to anum_maintenance")
    op.execute("create policy billing_key_rotation on billing_settings for select to anum_maintenance using (true)")
    op.execute("grant select (tenant_id, user_id, ip_hash, created_at) on billing_signups to anum_maintenance")
    op.execute(
        "create policy billing_signup_limits on billing_signups for select to anum_maintenance "
        "using (true)"
    )
    for table, columns in USAGE_COLUMNS.items():
        op.execute(f"grant select ({columns}) on {table} to anum_maintenance")
        op.execute(
            f"create policy billing_usage_meter on {table} for select to anum_maintenance "
            f"using (tenant_id = {BILLED})"
        )


def downgrade() -> None:
    for table in USAGE_COLUMNS:
        op.execute(f"drop policy if exists billing_usage_meter on {table}")
    # Column grants stay, like the other NOLOGIN roles' grants: without these policies the billing
    # settings that select rows are gone, and the columns are ids, sizes and timestamps only.
    for table in reversed(TABLES):
        op.drop_table(table)
