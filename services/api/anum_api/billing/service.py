"""Customer billing rules (docs/billing.md): subscriptions, invoices, payments, dunning.

Every function works on :class:`~anum_api.billing.rows.Rows` opened in the customer's
tenant (``rows.tenant_id``); issuer rows (settings, number sequences) are read and
written through ``rows.as_tenant(issuer)`` inside the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..schemas import new_id
from .money import period_end, plan_amount, prorate, split_gross, tax_for
from .rows import RowConflict, Rows
from .usage import METERS, Usage, limit_of

LIVE = ("trialing", "active", "past_due", "paused")
DEFAULT_SETTINGS: dict[str, Any] = {
    "issuer_name": "", "issuer_address": "", "issuer_vat_number": "", "issuer_email": "",
    "bank_name": "", "bank_account_name": "", "bank_iban": "", "bank_swift": "",
    "invoice_prefix": "INV", "credit_note_prefix": "CN", "payment_terms_days": 14,
    "tax_rules": {"SA": 1500}, "default_tax_bp": 0, "reminder_days": [1, 7, 14], "grace_days": 7,
    "sending_workspace_id": None, "stripe_enabled": False, "stripe_key_mode": None,
}
# The Stripe ciphertext columns start empty (written only through the settings route).
DEFAULT_SETTINGS.update(dict.fromkeys(("stripe_secret_ciphertext", "stripe_webhook_secret_ciphertext")))
MAX_CATCH_UP_PERIODS = 24


class BillingError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str | None = None, blocked_by: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.blocked_by = blocked_by or []


# ----------------------------------------------------------------------------- reads


def issuer_settings(rows: Rows, issuer_tenant_id: str) -> dict[str, Any]:
    with rows.as_tenant(issuer_tenant_id):
        found = rows.get("billing_settings", tenant_id=issuer_tenant_id)
    return {**DEFAULT_SETTINGS, **(found or {}), "tenant_id": issuer_tenant_id}


def get_plan(rows: Rows, issuer_tenant_id: str, plan_id: str) -> dict[str, Any] | None:
    with rows.as_tenant(issuer_tenant_id):
        return rows.get("billing_plans", id=plan_id)


def account_of(rows: Rows) -> dict[str, Any] | None:
    return rows.get("billing_accounts", tenant_id=rows.tenant_id)


def live_subscription(rows: Rows, *, for_update: bool = False) -> dict[str, Any] | None:
    found = rows.select("billing_subscriptions", {"status": ("in", LIVE)}, limit=1)
    if not found:
        return None
    return rows.get("billing_subscriptions", for_update=for_update, id=found[0]["id"]) if for_update else found[0]


def latest_subscription(rows: Rows) -> dict[str, Any] | None:
    live = live_subscription(rows)
    if live:
        return live
    found = rows.select("billing_subscriptions", order=[("created_at", True)], limit=1)
    return found[0] if found else None


def tax_rate(settings_row: dict[str, Any], country: str) -> int:
    rules = settings_row.get("tax_rules") or {}
    value = rules.get((country or "").upper())
    return int(value) if value is not None else int(settings_row.get("default_tax_bp") or 0)


def amount_of(sub: dict[str, Any]) -> int:
    return plan_amount(int(sub["price_minor"]), bool(sub["per_seat"]), int(sub["seats"]))


def monthly_value(sub: dict[str, Any]) -> int:
    """Monthly recurring revenue of one subscription (a yearly price spread over 12 months)."""
    if sub["status"] not in ("active", "past_due"):
        return 0
    amount = amount_of(sub)
    return amount // 12 if sub["interval"] == "year" else amount


def blocked_by(limits: dict[str, Any], per_seat: bool, seats: int, usage: Usage) -> list[dict[str, Any]]:
    """Every meter whose current use does not fit the given limits."""
    out = []
    for key in ("seats", "workspaces", "agent_runs", "storage"):
        limit = limit_of(limits, key, per_seat, seats)
        used = usage.get(key)
        if limit is not None and used > limit:
            out.append({"key": key, "used": used, "limit": limit, "unit": METERS[key][1]})
    return out


# ----------------------------------------------------------------------------- invoices


def _snapshot_bill_to(account: dict[str, Any]) -> dict[str, Any]:
    keys = ("display_name", "company_name", "contact_name", "contact_email", "address_line1", "address_line2",
            "city", "postal_code", "country", "vat_number")
    return {key: account.get(key) or "" for key in keys}


def _snapshot_issuer(settings_row: dict[str, Any]) -> dict[str, Any]:
    keys = ("issuer_name", "issuer_address", "issuer_vat_number", "issuer_email", "bank_name", "bank_account_name",
            "bank_iban", "bank_swift")
    return {key: settings_row.get(key) or "" for key in keys}


@dataclass
class Line:
    description: str
    amount_minor: int
    kind: str = "plan"
    quantity: int = 1
    unit_minor: int | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


@dataclass
class Issued:
    invoice: dict[str, Any]
    lines: list[dict[str, Any]] = field(default_factory=list)


def issue_invoice(
    rows: Rows, account: dict[str, Any], lines: list[Line], *, kind: str, now: datetime, actor: str,
    subscription_id: str | None = None, period: tuple[datetime, datetime] | None = None, due_at: datetime | None = None,
    use_credit: bool = True,
) -> Issued:
    """Issue an invoice now: number from the issuer's gapless sequence, tax, credit balance used up."""
    issuer = account["issuer_tenant_id"]
    settings_row = issuer_settings(rows, issuer)
    lines = list(lines)
    subtotal = sum(line.amount_minor for line in lines)
    credit_used = 0
    if use_credit and subtotal > 0 and int(account.get("credit_balance_minor") or 0) > 0:
        credit_used = min(int(account["credit_balance_minor"]), subtotal)
        lines.append(Line("Credit from your account balance", -credit_used, kind="credit"))
        subtotal -= credit_used
        rows.update("billing_accounts", {"tenant_id": rows.tenant_id},
                    {"credit_balance_minor": int(account["credit_balance_minor"]) - credit_used, "updated_at": now})
        account["credit_balance_minor"] = int(account["credit_balance_minor"]) - credit_used
    subtotal = max(0, subtotal)
    rate = tax_rate(settings_row, account.get("country") or "")
    tax = tax_for(subtotal, rate)
    total = subtotal + tax
    with rows.as_tenant(issuer):
        number = f"{settings_row['invoice_prefix']}-{rows.next_number('invoice'):06d}"
    due = due_at if due_at is not None else now + timedelta(days=int(settings_row["payment_terms_days"]))
    invoice = {
        "id": new_id("invoice"), "tenant_id": rows.tenant_id, "issuer_tenant_id": issuer, "subscription_id": subscription_id,
        "number": number, "status": "paid" if total == 0 else "open", "kind": kind, "currency": _currency(rows, account, subscription_id),
        "period_start": period[0] if period else None, "period_end": period[1] if period else None,
        "issued_at": now, "due_at": due, "subtotal_minor": subtotal, "tax_rate_bp": rate,
        "tax_country": (account.get("country") or "")[:2].upper(), "tax_minor": tax, "total_minor": total,
        "amount_paid_minor": 0, "amount_credited_minor": 0, "amount_due_minor": total,
        "bill_to": _snapshot_bill_to(account), "issuer": _snapshot_issuer(settings_row), "notes": "",
        "reminders_sent": 0, "last_reminder_at": None, "last_email": None, "paid_at": now if total == 0 else None,
        "voided_at": None, "created_by": actor, "created_at": now, "updated_at": now,
    }
    rows.insert("billing_invoices", invoice)
    stored_lines = []
    for position, line in enumerate(lines):
        row = {
            "id": new_id("invline"), "tenant_id": rows.tenant_id, "invoice_id": invoice["id"], "position": position,
            "description": line.description[:300], "quantity": line.quantity,
            "unit_minor": line.unit_minor if line.unit_minor is not None else line.amount_minor,
            "amount_minor": line.amount_minor, "kind": line.kind, "period_start": line.period_start, "period_end": line.period_end,
        }
        rows.insert("billing_invoice_lines", row)
        stored_lines.append(row)
    return Issued(invoice, stored_lines)


def _currency(rows: Rows, account: dict[str, Any], subscription_id: str | None) -> str:
    if subscription_id:
        sub = rows.get("billing_subscriptions", id=subscription_id)
        if sub:
            return str(sub["currency"])
    return "SAR"


def period_lines(sub: dict[str, Any], start: datetime, end: datetime) -> list[Line]:
    seats = int(sub["seats"])
    unit = int(sub["price_minor"])
    if sub["per_seat"]:
        return [Line(f"{sub['plan_name']} plan, {seats} seats", unit * seats, quantity=seats, unit_minor=unit,
                     period_start=start, period_end=end)]
    return [Line(f"{sub['plan_name']} plan", unit, period_start=start, period_end=end)]


def get_invoice(rows: Rows, invoice_id: str) -> dict[str, Any] | None:
    return rows.get("billing_invoices", id=invoice_id)


def invoice_detail(rows: Rows, invoice: dict[str, Any]) -> dict[str, Any]:
    lines = rows.select("billing_invoice_lines", {"invoice_id": invoice["id"]}, order=[("position", False)])
    payments = rows.select("billing_payments", {"invoice_id": invoice["id"]}, order=[("received_at", False)])
    notes = rows.select("billing_credit_notes", {"invoice_id": invoice["id"]}, order=[("issued_at", False)])
    return {"invoice": invoice, "lines": lines, "payments": payments, "credit_notes": notes}


def list_invoices(rows: Rows, *, q: str | None, status: str | None, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    where: dict[str, Any] = {"status": status} if status else {}
    search = (("number",), q) if q else None
    items = rows.select("billing_invoices", where, search=search, order=[("created_at", True), ("id", True)], limit=limit, offset=offset)
    return items, rows.count("billing_invoices", where, search=search)


# ----------------------------------------------------------------------------- payments and credits


def _settle(rows: Rows, now: datetime) -> None:
    """After money arrived: a subscription with nothing overdue is active again and writes are back."""
    overdue = rows.count("billing_invoices", {"status": "open", "due_at": ("<=", now)})
    if overdue:
        return
    sub = live_subscription(rows)
    if sub and sub["status"] == "past_due":
        rows.update("billing_subscriptions", {"id": sub["id"]},
                    {"status": "active", "past_due_since": None, "grace_until": None, "updated_at": now})
    account = account_of(rows)
    if account and account["state"] == "read_only":
        rows.update("billing_accounts", {"tenant_id": rows.tenant_id}, {"state": "active", "updated_at": now})


def record_payment(
    rows: Rows, invoice_id: str, *, amount_minor: int, method: str, reference: str, received_at: datetime, now: datetime,
    actor: str, proof: tuple[str, str, bytes] | None = None, provider: str | None = None, provider_payment_id: str | None = None,
) -> dict[str, Any]:
    invoice = rows.get("billing_invoices", for_update=True, id=invoice_id)
    if invoice is None:
        raise BillingError(404, "Invoice not found.")
    if invoice["status"] not in ("open", "uncollectible"):
        raise BillingError(409, f"Invoice {invoice['number']} is {invoice['status']}; only an open invoice takes a payment.")
    if amount_minor <= 0:
        raise BillingError(422, "The amount must be more than zero.")
    if amount_minor > int(invoice["amount_due_minor"]):
        raise BillingError(422, f"The amount is more than the {invoice['amount_due_minor']} still due on {invoice['number']}; record the amount due or issue a credit note.")
    payment = {
        "id": new_id("payment"), "tenant_id": rows.tenant_id, "issuer_tenant_id": invoice["issuer_tenant_id"],
        "invoice_id": invoice_id, "amount_minor": amount_minor, "currency": invoice["currency"], "method": method,
        "reference": reference[:200], "received_at": received_at,
        "proof_name": proof[0] if proof else None, "proof_content_type": proof[1] if proof else None,
        "proof_size": len(proof[2]) if proof else None, "proof_content": proof[2] if proof else None,
        "provider": provider, "provider_payment_id": provider_payment_id, "recorded_by": actor, "created_at": now,
    }
    rows.insert("billing_payments", payment)
    paid = int(invoice["amount_paid_minor"]) + amount_minor
    due = int(invoice["amount_due_minor"]) - amount_minor
    changes: dict[str, Any] = {"amount_paid_minor": paid, "amount_due_minor": due, "updated_at": now}
    if due == 0:
        changes.update(status="paid", paid_at=now)
    rows.update("billing_invoices", {"id": invoice_id}, changes)
    _settle(rows, now)
    payment.pop("proof_content", None)
    return payment


def issue_credit_note(rows: Rows, invoice_id: str, *, total_minor: int, reason: str, now: datetime, actor: str) -> dict[str, Any]:
    invoice = rows.get("billing_invoices", for_update=True, id=invoice_id)
    if invoice is None:
        raise BillingError(404, "Invoice not found.")
    if invoice["status"] in ("draft", "void"):
        raise BillingError(409, f"Invoice {invoice['number'] or invoice_id} is {invoice['status']}; credit notes need an issued invoice.")
    available = int(invoice["total_minor"]) - int(invoice["amount_credited_minor"])
    if total_minor <= 0 or total_minor > available:
        raise BillingError(422, f"A credit note on {invoice['number']} can be at most {available}.")
    subtotal, tax = split_gross(total_minor, int(invoice["tax_rate_bp"]))
    settings_row = issuer_settings(rows, invoice["issuer_tenant_id"])
    with rows.as_tenant(invoice["issuer_tenant_id"]):
        number = f"{settings_row['credit_note_prefix']}-{rows.next_number('credit_note'):06d}"
    note = {
        "id": new_id("creditnote"), "tenant_id": rows.tenant_id, "issuer_tenant_id": invoice["issuer_tenant_id"],
        "invoice_id": invoice_id, "number": number, "reason": reason[:500], "subtotal_minor": subtotal, "tax_minor": tax,
        "total_minor": total_minor, "issued_at": now, "created_by": actor,
    }
    rows.insert("billing_credit_notes", note)
    due = int(invoice["amount_due_minor"])
    against_due = min(due, total_minor)
    to_balance = total_minor - against_due
    changes: dict[str, Any] = {
        "amount_credited_minor": int(invoice["amount_credited_minor"]) + total_minor,
        "amount_due_minor": due - against_due, "updated_at": now,
    }
    if due - against_due == 0 and invoice["status"] in ("open", "uncollectible"):
        changes.update(status="paid", paid_at=now)
    rows.update("billing_invoices", {"id": invoice_id}, changes)
    if to_balance:
        account = account_of(rows)
        if account:
            rows.update("billing_accounts", {"tenant_id": rows.tenant_id},
                        {"credit_balance_minor": int(account["credit_balance_minor"]) + to_balance, "updated_at": now})
    _settle(rows, now)
    return note


def void_invoice(rows: Rows, invoice_id: str, *, now: datetime) -> dict[str, Any]:
    invoice = rows.get("billing_invoices", for_update=True, id=invoice_id)
    if invoice is None:
        raise BillingError(404, "Invoice not found.")
    if invoice["status"] != "open" or int(invoice["amount_paid_minor"]) or int(invoice["amount_credited_minor"]):
        raise BillingError(409, "Only an open invoice without payments or credit notes can be voided; issue a credit note instead.")
    rows.update("billing_invoices", {"id": invoice_id}, {"status": "void", "amount_due_minor": 0, "voided_at": now, "updated_at": now})
    _settle(rows, now)
    return rows.get("billing_invoices", id=invoice_id) or invoice


# ----------------------------------------------------------------------------- subscriptions


def _snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan["id"], "plan_code": plan["code"], "plan_name": plan["name"], "price_minor": int(plan["price_minor"]),
        "currency": plan["currency"], "interval": plan["interval"], "per_seat": bool(plan["per_seat"]),
        "limits": dict(plan.get("limits") or {}), "features": list(plan.get("features") or []),
    }


def ensure_account(rows: Rows, *, issuer_tenant_id: str, display_name: str, now: datetime, country: str = "SA",
                   contact_email: str = "") -> dict[str, Any]:
    account = account_of(rows)
    if account:
        if account["issuer_tenant_id"] != issuer_tenant_id:
            raise BillingError(409, "This organisation is billed by another issuer.")
        return account
    account = {
        "tenant_id": rows.tenant_id, "issuer_tenant_id": issuer_tenant_id, "display_name": display_name[:200],
        "contact_name": "", "contact_email": contact_email[:254], "company_name": display_name[:200], "address_line1": "",
        "address_line2": "", "city": "", "postal_code": "", "country": (country or "SA").upper()[:2], "vat_number": "",
        "credit_balance_minor": 0, "state": "active", "onboarding_dismissed": False, "plan_confirmed": False,
        "created_at": now, "updated_at": now,
    }
    rows.insert("billing_accounts", account)
    return account


def start_subscription(
    rows: Rows, account: dict[str, Any], plan: dict[str, Any], *, now: datetime, actor: str, trial_days: int | None = None,
    seats: int = 1,
) -> tuple[dict[str, Any], Issued | None]:
    if live_subscription(rows):
        raise BillingError(409, "This organisation already has a subscription; change its plan instead.")
    if not plan.get("active"):
        raise BillingError(422, "That plan is archived; choose an active plan.")
    days = int(plan["trial_days"]) if trial_days is None else trial_days
    trialing = days > 0
    end = now + timedelta(days=days) if trialing else period_end(now, plan["interval"])
    sub = {
        "id": new_id("subscription"), "tenant_id": rows.tenant_id, "issuer_tenant_id": account["issuer_tenant_id"],
        **_snapshot(plan), "seats": max(1, seats), "status": "trialing" if trialing else "active",
        "trial_end": end if trialing else None, "current_period_start": now, "current_period_end": end,
        "cancel_at_period_end": False, "cancelled_at": None, "pending_plan_id": None, "pending_seats": None, "paused_at": None,
        "past_due_since": None, "grace_until": None, "created_by": actor, "created_at": now, "updated_at": now,
    }
    try:
        rows.insert("billing_subscriptions", sub)
    except RowConflict as exc:
        raise BillingError(409, "This organisation already has a subscription; change its plan instead.") from exc
    issued = None
    if not trialing:
        issued = issue_invoice(rows, account, period_lines(sub, now, end), kind="subscription", now=now, actor=actor,
                               subscription_id=sub["id"], period=(now, end))
    return sub, issued


@dataclass
class Preview:
    effective: str
    amount_minor: int
    tax_minor: int
    total_minor: int
    credit_minor: int
    blocked_by: list[dict[str, Any]]


def preview_change(rows: Rows, sub: dict[str, Any], plan: dict[str, Any], seats: int, usage: Usage, now: datetime, when: str | None) -> Preview:
    account = account_of(rows) or {"country": ""}
    old, new = amount_of(sub), plan_amount(int(plan["price_minor"]), bool(plan["per_seat"]), seats)
    blocked = blocked_by(dict(plan.get("limits") or {}), bool(plan["per_seat"]), seats, usage)
    if sub["status"] == "trialing":
        return Preview("now", 0, 0, 0, 0, blocked)
    if new >= old:
        amount = max(0, prorate(old, new, sub["current_period_start"], sub["current_period_end"], now))
        rate = tax_rate(issuer_settings(rows, sub["issuer_tenant_id"]), account.get("country") or "")
        tax = tax_for(amount, rate)
        return Preview("now", amount, tax, amount + tax, 0, blocked)
    if when == "now":
        credit = -prorate(old, new, sub["current_period_start"], sub["current_period_end"], now)
        return Preview("now", 0, 0, 0, max(0, credit), blocked)
    return Preview("period_end", 0, 0, 0, 0, blocked)


def change_plan(
    rows: Rows, plan: dict[str, Any], *, seats: int | None, when: str | None, usage: Usage, now: datetime, actor: str,
) -> tuple[dict[str, Any], Issued | None]:
    sub = live_subscription(rows, for_update=True)
    if sub is None:
        raise BillingError(404, "This organisation has no subscription to change.")
    if sub["status"] == "paused":
        raise BillingError(409, "The subscription is paused; ask the platform owner to resume it first.")
    if not plan.get("active"):
        raise BillingError(422, "That plan is archived; choose an active plan.")
    if plan["currency"] != sub["currency"]:
        raise BillingError(422, f"That plan is priced in {plan['currency']}; this subscription is in {sub['currency']}.")
    seats = max(1, seats if seats is not None else int(sub["seats"]))
    preview = preview_change(rows, sub, plan, seats, usage, now, when)
    if preview.blocked_by:
        parts = ", ".join(f"{item['key']} {item['used']} of {item['limit']}" for item in preview.blocked_by)
        raise BillingError(409, f"The current usage does not fit that plan ({parts}). Remove what is over the limit first; nothing is deleted for you.",
                           blocked_by=preview.blocked_by)
    account = account_of(rows)
    if account is None:
        raise BillingError(404, "This organisation has no billing account.")
    old_seats = int(sub["seats"])
    issued = None
    changes: dict[str, Any] = {"updated_at": now, "pending_plan_id": None, "pending_seats": None}
    if preview.effective == "period_end":
        changes.update(pending_plan_id=plan["id"], pending_seats=seats)
    else:
        changes.update(_snapshot(plan), seats=seats)
        if preview.amount_minor > 0:
            start, end = now, sub["current_period_end"]
            issued = issue_invoice(
                rows, account,
                [Line(f"Change from {sub['plan_name']} to {plan['name']}, prorated for the rest of the period", preview.amount_minor,
                      kind="proration", period_start=start, period_end=end)],
                kind="proration", now=now, actor=actor, subscription_id=sub["id"], period=(start, end),
            )
        if preview.credit_minor > 0:
            rows.update("billing_accounts", {"tenant_id": rows.tenant_id},
                        {"credit_balance_minor": int(account["credit_balance_minor"]) + preview.credit_minor, "updated_at": now})
    rows.update("billing_subscriptions", {"id": sub["id"]}, changes)
    rows.update("billing_accounts", {"tenant_id": rows.tenant_id}, {"plan_confirmed": True, "updated_at": now})
    if seats != old_seats:
        rows.insert("billing_seats", {
            "id": new_id("seatchange"), "tenant_id": rows.tenant_id, "subscription_id": sub["id"], "from_seats": old_seats,
            "to_seats": seats, "prorated_minor": preview.amount_minor - preview.credit_minor, "changed_by": actor, "created_at": now,
        })
    return rows.get("billing_subscriptions", id=sub["id"]) or sub, issued


def cancel(rows: Rows, *, now: datetime) -> dict[str, Any]:
    sub = live_subscription(rows, for_update=True)
    if sub is None:
        raise BillingError(404, "This organisation has no subscription to cancel.")
    rows.update("billing_subscriptions", {"id": sub["id"]}, {"cancel_at_period_end": True, "pending_plan_id": None, "pending_seats": None, "updated_at": now})
    return rows.get("billing_subscriptions", id=sub["id"]) or sub


def reactivate(rows: Rows, *, now: datetime, actor: str) -> tuple[dict[str, Any], Issued | None]:
    sub = live_subscription(rows, for_update=True)
    if sub is not None:
        if not sub["cancel_at_period_end"]:
            raise BillingError(409, "The subscription is not cancelled.")
        rows.update("billing_subscriptions", {"id": sub["id"]}, {"cancel_at_period_end": False, "updated_at": now})
        return rows.get("billing_subscriptions", id=sub["id"]) or sub, None
    last = latest_subscription(rows)
    account = account_of(rows)
    if last is None or account is None:
        raise BillingError(404, "This organisation has no subscription to reactivate; choose a plan.")
    plan = get_plan(rows, account["issuer_tenant_id"], last["plan_id"])
    if plan is None or not plan.get("active"):
        raise BillingError(409, "The previous plan is no longer offered; choose another plan.")
    return start_subscription(rows, account, plan, now=now, actor=actor, trial_days=0, seats=int(last["seats"]))


def pause(rows: Rows, *, now: datetime) -> dict[str, Any]:
    sub = live_subscription(rows, for_update=True)
    if sub is None or sub["status"] in ("paused",):
        raise BillingError(409, "There is no running subscription to pause.")
    rows.update("billing_subscriptions", {"id": sub["id"]}, {"status": "paused", "paused_at": now, "updated_at": now})
    return rows.get("billing_subscriptions", id=sub["id"]) or sub


def resume(rows: Rows, *, now: datetime, actor: str) -> tuple[dict[str, Any], Issued | None]:
    sub = live_subscription(rows, for_update=True)
    if sub is None or sub["status"] != "paused":
        raise BillingError(409, "The subscription is not paused.")
    account = account_of(rows)
    if account is None:
        raise BillingError(404, "This organisation has no billing account.")
    end = period_end(now, sub["interval"])
    rows.update("billing_subscriptions", {"id": sub["id"]}, {
        "status": "active", "paused_at": None, "trial_end": None, "current_period_start": now, "current_period_end": end, "updated_at": now,
    })
    sub = rows.get("billing_subscriptions", id=sub["id"]) or sub
    issued = issue_invoice(rows, account, period_lines(sub, now, end), kind="subscription", now=now, actor=actor,
                           subscription_id=sub["id"], period=(now, end))
    return sub, issued


# ----------------------------------------------------------------------------- the billing job


@dataclass
class Reminder:
    tenant_id: str
    issuer_tenant_id: str
    invoice_id: str
    number: str
    to: str
    reminder: int


@dataclass
class PassResult:
    invoices: list[str] = field(default_factory=list)
    reminders: list[Reminder] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)


def run_tenant(rows: Rows, *, now: datetime, actor: str = "system:billing") -> PassResult:
    """One billing pass for one organisation: trial end, renewals, dunning, read-only.

    Idempotent: running it twice at the same moment changes nothing the second time.
    """
    result = PassResult()
    sub = live_subscription(rows, for_update=True)
    account = account_of(rows)
    if sub is None or account is None:
        return result
    if sub["status"] == "trialing" and sub["trial_end"] and sub["trial_end"] <= now:
        if sub["cancel_at_period_end"]:
            rows.update("billing_subscriptions", {"id": sub["id"]}, {"status": "cancelled", "cancelled_at": sub["trial_end"], "updated_at": now})
            result.transitions.append("trial_cancelled")
            return result
        start = sub["trial_end"]
        _apply_pending(rows, sub, account, now)
        sub = rows.get("billing_subscriptions", id=sub["id"]) or sub
        end = period_end(start, sub["interval"])
        rows.update("billing_subscriptions", {"id": sub["id"]}, {"status": "active", "current_period_start": start, "current_period_end": end, "updated_at": now})
        sub = rows.get("billing_subscriptions", id=sub["id"]) or sub
        issued = issue_invoice(rows, account, period_lines(sub, start, end), kind="subscription", now=now, actor=actor,
                               subscription_id=sub["id"], period=(start, end), due_at=start)
        result.invoices.append(issued.invoice["id"])
        result.transitions.append("trial_ended")
    periods = 0
    while sub["status"] in ("active", "past_due") and sub["current_period_end"] <= now and periods < MAX_CATCH_UP_PERIODS:
        periods += 1
        start = sub["current_period_end"]
        if sub["cancel_at_period_end"]:
            rows.update("billing_subscriptions", {"id": sub["id"]}, {"status": "cancelled", "cancelled_at": start, "updated_at": now})
            result.transitions.append("cancelled")
            return result
        _apply_pending(rows, sub, account, now)
        sub = rows.get("billing_subscriptions", id=sub["id"]) or sub
        end = period_end(start, sub["interval"])
        rows.update("billing_subscriptions", {"id": sub["id"]}, {"current_period_start": start, "current_period_end": end, "updated_at": now})
        sub = rows.get("billing_subscriptions", id=sub["id"]) or sub
        account = account_of(rows) or account
        issued = issue_invoice(rows, account, period_lines(sub, start, end), kind="subscription", now=now, actor=actor,
                               subscription_id=sub["id"], period=(start, end))
        result.invoices.append(issued.invoice["id"])
        result.transitions.append("renewed")
    if sub["status"] != "paused":
        _dunning(rows, sub, account_of(rows) or account, now, result)
    return result


def _apply_pending(rows: Rows, sub: dict[str, Any], account: dict[str, Any], now: datetime) -> None:
    if not sub.get("pending_plan_id"):
        return
    plan = get_plan(rows, account["issuer_tenant_id"], sub["pending_plan_id"])
    changes: dict[str, Any] = {"pending_plan_id": None, "pending_seats": None, "updated_at": now}
    if plan is not None:
        changes.update(_snapshot(plan))
        if sub.get("pending_seats"):
            changes["seats"] = int(sub["pending_seats"])
    rows.update("billing_subscriptions", {"id": sub["id"]}, changes)


def grace_until(settings_row: dict[str, Any], past_due_since: datetime) -> datetime:
    reminder_days = [int(d) for d in settings_row.get("reminder_days") or []]
    return past_due_since + timedelta(days=(max(reminder_days) if reminder_days else 0) + int(settings_row.get("grace_days") or 0))


def _dunning(rows: Rows, sub: dict[str, Any], account: dict[str, Any], now: datetime, result: PassResult) -> None:
    overdue = rows.select("billing_invoices", {"status": "open", "due_at": ("<=", now)}, order=[("due_at", False)])
    if not overdue:
        if sub["status"] == "past_due" or account["state"] == "read_only":
            _settle(rows, now)
            result.transitions.append("settled")
        return
    settings_row = issuer_settings(rows, sub["issuer_tenant_id"])
    since = sub["past_due_since"] or overdue[0]["due_at"]
    grace = grace_until(settings_row, since)
    if sub["status"] != "past_due" or sub["grace_until"] != grace:
        rows.update("billing_subscriptions", {"id": sub["id"]}, {"status": "past_due", "past_due_since": since, "grace_until": grace, "updated_at": now})
        if sub["status"] != "past_due":
            result.transitions.append("past_due")
    schedule = sorted(int(d) for d in settings_row.get("reminder_days") or [])
    for invoice in overdue:
        days_overdue = (now - invoice["due_at"]).days
        due_count = sum(1 for day in schedule if day <= days_overdue)
        if due_count > int(invoice["reminders_sent"]):
            rows.update("billing_invoices", {"id": invoice["id"]}, {"reminders_sent": due_count, "last_reminder_at": now, "updated_at": now})
            to = account.get("contact_email") or ""
            result.reminders.append(Reminder(rows.tenant_id, sub["issuer_tenant_id"], invoice["id"], invoice["number"], to, due_count))
    if now >= grace and account["state"] != "read_only":
        rows.update("billing_accounts", {"tenant_id": rows.tenant_id}, {"state": "read_only", "updated_at": now})
        result.transitions.append("read_only")
