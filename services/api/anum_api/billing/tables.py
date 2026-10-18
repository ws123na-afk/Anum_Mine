"""SQLAlchemy Core tables of customer billing (migration ``0081_customer_billing``).

Kept apart from the ORM ``Base`` metadata: the billing store builds its statements from
these tables and plain dicts, and the in-memory store mirrors the same rows.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, LargeBinary, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
TS = DateTime(timezone=True)


def _s(name: str, length: int = 200, **kw: object) -> Column:
    return Column(name, String(length), **kw)  # type: ignore[arg-type]


def _i(name: str, **kw: object) -> Column:
    return Column(name, Integer(), **kw)  # type: ignore[arg-type]


def _m(name: str, **kw: object) -> Column:
    return Column(name, BigInteger(), **kw)  # type: ignore[arg-type]


def _t(name: str, **kw: object) -> Column:
    return Column(name, TS, **kw)  # type: ignore[arg-type]


def _b(name: str) -> Column:
    return Column(name, Boolean())


def _j(name: str) -> Column:
    return Column(name, JSONB())


plans = Table(
    "billing_plans", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("code", 60), _s("name", 120), _s("description", 500),
    _m("price_minor"), _s("currency", 3), _s("interval", 10), _i("trial_days"), _b("per_seat"), _j("limits"),
    _j("features"), _b("published"), _b("active"), _i("sort_order"), _s("created_by", 160), _t("created_at"),
    _t("updated_at"),
)
settings_table = Table(
    "billing_settings", metadata,
    _s("tenant_id", 80, primary_key=True), _s("issuer_name"), _s("issuer_address", 600), _s("issuer_vat_number", 40),
    _s("issuer_email", 254), _s("bank_name", 120), _s("bank_account_name", 160), _s("bank_iban", 40),
    _s("bank_swift", 20), _s("invoice_prefix", 12), _s("credit_note_prefix", 12), _i("payment_terms_days"),
    _j("tax_rules"), _i("default_tax_bp"), _j("reminder_days"), _i("grace_days"), _s("sending_workspace_id", 80),
    _b("stripe_enabled"), Column("stripe_secret_ciphertext", String()), Column("stripe_webhook_secret_ciphertext", String()),
    _s("stripe_key_mode", 10), _s("updated_by", 160), _t("updated_at"),
)
accounts = Table(
    "billing_accounts", metadata,
    _s("tenant_id", 80, primary_key=True), _s("issuer_tenant_id", 80), _s("display_name"), _s("contact_name", 160),
    _s("contact_email", 254), _s("company_name"), _s("address_line1"), _s("address_line2"), _s("city", 120),
    _s("postal_code", 20), _s("country", 2), _s("vat_number", 40), _m("credit_balance_minor"), _s("state", 20),
    _b("onboarding_dismissed"), _b("plan_confirmed"), _t("created_at"), _t("updated_at"),
)
subscriptions = Table(
    "billing_subscriptions", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("issuer_tenant_id", 80), _s("plan_id", 80),
    _s("plan_code", 60), _s("plan_name", 120), _m("price_minor"), _s("currency", 3), _s("interval", 10),
    _b("per_seat"), _j("limits"), _j("features"), _i("seats"), _s("status", 20), _t("trial_end"),
    _t("current_period_start"), _t("current_period_end"), _b("cancel_at_period_end"), _t("cancelled_at"),
    _s("pending_plan_id", 80), _i("pending_seats"), _t("paused_at"), _t("past_due_since"), _t("grace_until"), _s("created_by", 160),
    _t("created_at"), _t("updated_at"),
)
seats = Table(
    "billing_seats", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("subscription_id", 80), _i("from_seats"), _i("to_seats"),
    _m("prorated_minor"), _s("changed_by", 160), _t("created_at"),
)
usage = Table(
    "billing_usage", metadata,
    _s("tenant_id", 80, primary_key=True), _s("period", 7, primary_key=True), _s("meter", 30, primary_key=True),
    _m("quantity"), _t("updated_at"),
)
invoices = Table(
    "billing_invoices", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("issuer_tenant_id", 80), _s("subscription_id", 80),
    _s("number", 40), _s("status", 20), _s("kind", 20), _s("currency", 3), _t("period_start"), _t("period_end"),
    _t("issued_at"), _t("due_at"), _m("subtotal_minor"), _i("tax_rate_bp"), _s("tax_country", 2), _m("tax_minor"),
    _m("total_minor"), _m("amount_paid_minor"), _m("amount_credited_minor"), _m("amount_due_minor"), _j("bill_to"),
    _j("issuer"), _s("notes", 1000), _i("reminders_sent"), _t("last_reminder_at"), _j("last_email"), _t("paid_at"),
    _t("voided_at"), _s("created_by", 160), _t("created_at"), _t("updated_at"),
)
invoice_lines = Table(
    "billing_invoice_lines", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("invoice_id", 80), _i("position"), _s("description", 300),
    _i("quantity"), _m("unit_minor"), _m("amount_minor"), _s("kind", 20), _t("period_start"), _t("period_end"),
)
credit_notes = Table(
    "billing_credit_notes", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("issuer_tenant_id", 80), _s("invoice_id", 80),
    _s("number", 40), _s("reason", 500), _m("subtotal_minor"), _m("tax_minor"), _m("total_minor"), _t("issued_at"),
    _s("created_by", 160),
)
payments = Table(
    "billing_payments", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("issuer_tenant_id", 80), _s("invoice_id", 80),
    _m("amount_minor"), _s("currency", 3), _s("method", 20), _s("reference"), _t("received_at"),
    _s("proof_name", 255), _s("proof_content_type", 60), _i("proof_size"), Column("proof_content", LargeBinary()),
    _s("provider", 20), _s("provider_payment_id"), _s("recorded_by", 160), _t("created_at"),
)
sequences = Table(
    "billing_number_sequences", metadata,
    _s("tenant_id", 80, primary_key=True), _s("kind", 20, primary_key=True), _m("next_value"),
)
webhook_events = Table(
    "billing_webhook_events", metadata,
    _s("tenant_id", 80, primary_key=True), _s("provider", 20, primary_key=True), _s("event_id", 200, primary_key=True),
    _s("event_type", 100), _t("received_at"),
)
signups = Table(
    "billing_signups", metadata,
    _s("id", 80, primary_key=True), _s("tenant_id", 80), _s("user_id", 160), _s("email_domain", 254),
    _s("ip_hash", 64), _s("plan_id", 80), _t("created_at"),
)

TABLES: dict[str, Table] = {table.name: table for table in metadata.sorted_tables}
# Columns never read by list queries (proof bytes are fetched on their own).
HEAVY_COLUMNS = {"billing_payments": ("proof_content",)}
