"""JSON views of billing rows (docs/billing.md#api)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .usage import METERS, Usage, limit_of, period_key

INVOICE_FIELDS = ("id", "number", "status", "kind", "currency", "period_start", "period_end", "issued_at", "due_at",
                  "subtotal_minor", "tax_rate_bp", "tax_country", "tax_minor", "total_minor", "amount_paid_minor",
                  "amount_credited_minor", "amount_due_minor", "reminders_sent")
SUBSCRIPTION_FIELDS = ("id", "plan_id", "plan_code", "plan_name", "price_minor", "currency", "interval", "per_seat", "limits",
                       "features", "seats", "status", "trial_end", "current_period_start", "current_period_end",
                       "cancel_at_period_end", "cancelled_at", "pending_plan_id", "pending_seats", "paused_at",
                       "past_due_since", "grace_until")
ACCOUNT_FIELDS = ("display_name", "contact_name", "contact_email", "company_name", "address_line1", "address_line2", "city",
                  "postal_code", "country", "vat_number", "credit_balance_minor", "state")


def invoice_summary(invoice: dict[str, Any], now: datetime) -> dict[str, Any]:
    out = {key: invoice.get(key) for key in INVOICE_FIELDS}
    out["overdue"] = invoice["status"] == "open" and invoice.get("due_at") is not None and invoice["due_at"] <= now
    out["last_email"] = invoice.get("last_email")
    return out


def payment_view(payment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payment["id"], "amount_minor": payment["amount_minor"], "currency": payment["currency"], "method": payment["method"],
        "reference": payment.get("reference") or "", "received_at": payment["received_at"],
        "has_proof": bool(payment.get("proof_size")), "proof_name": payment.get("proof_name"),
        "provider": payment.get("provider"), "recorded_by": payment.get("recorded_by"),
    }


def credit_note_view(note: dict[str, Any]) -> dict[str, Any]:
    return {key: note.get(key) for key in ("id", "number", "reason", "subtotal_minor", "tax_minor", "total_minor", "issued_at")}


def bank_details(issuer: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(issuer.get("bank_iban")),
        "bank_name": issuer.get("bank_name") or "", "account_name": issuer.get("bank_account_name") or issuer.get("issuer_name") or "",
        "iban": issuer.get("bank_iban") or "", "swift": issuer.get("bank_swift") or "",
    }


def invoice_detail_view(detail: dict[str, Any], now: datetime) -> dict[str, Any]:
    invoice = detail["invoice"]
    return {
        **invoice_summary(invoice, now),
        "bill_to": invoice.get("bill_to") or {},
        "issuer": {k: v for k, v in (invoice.get("issuer") or {}).items() if not k.startswith("bank_")},
        "bank_details": bank_details(invoice.get("issuer") or {}),
        "lines": [
            {key: line.get(key) for key in ("id", "description", "quantity", "unit_minor", "amount_minor", "kind", "period_start", "period_end")}
            for line in detail["lines"]
        ],
        "payments": [payment_view(p) for p in detail["payments"]],
        "credit_notes": [credit_note_view(n) for n in detail["credit_notes"]],
    }


def subscription_view(sub: dict[str, Any] | None) -> dict[str, Any] | None:
    if sub is None:
        return None
    return {key: sub.get(key) for key in SUBSCRIPTION_FIELDS}


def account_view(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if account is None:
        return None
    return {key: account.get(key) for key in ACCOUNT_FIELDS}


def usage_view(sub: dict[str, Any] | None, usage: Usage, now: datetime) -> dict[str, Any]:
    limits = dict((sub or {}).get("limits") or {})
    per_seat = bool((sub or {}).get("per_seat"))
    seats = int((sub or {}).get("seats") or 1)
    meters = []
    for key, (_, unit) in METERS.items():
        meters.append({"key": key, "used": usage.get(key), "limit": limit_of(limits, key, per_seat, seats) if sub else None, "unit": unit})
    return {"period": period_key(now), "meters": meters}


def plan_view(plan: dict[str, Any], current_plan_id: str | None = None) -> dict[str, Any]:
    out = {key: plan.get(key) for key in ("id", "code", "name", "description", "price_minor", "currency", "interval", "trial_days",
                                           "per_seat", "limits", "features")}
    if current_plan_id is not None:
        out["current"] = plan["id"] == current_plan_id
    return out


def admin_plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    return {**plan_view(plan), "published": plan["published"], "active": plan["active"], "sort_order": plan["sort_order"],
            "created_at": plan["created_at"], "updated_at": plan["updated_at"]}


def settings_view(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("issuer_name", "issuer_address", "issuer_vat_number", "issuer_email", "bank_name", "bank_account_name", "bank_iban",
            "bank_swift", "invoice_prefix", "credit_note_prefix", "payment_terms_days", "tax_rules", "default_tax_bp",
            "reminder_days", "grace_days", "sending_workspace_id", "stripe_enabled")
    out = {key: row.get(key) for key in keys}
    out["has_stripe_secret_key"] = bool(row.get("stripe_secret_ciphertext"))
    out["has_stripe_webhook_secret"] = bool(row.get("stripe_webhook_secret_ciphertext"))
    out["stripe_test_mode"] = row.get("stripe_key_mode") == "test" if row.get("stripe_key_mode") else None
    return out
