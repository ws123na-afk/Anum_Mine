"""Cross-organisation billing reads through ``anum_platform_reader`` (docs/billing.md).

The platform console's subscription list and MRR summary, and the plan catalog behind the
public pricing page and a customer's upgrade options. Each read is one rolled-back
transaction as the NOLOGIN reader with ``anum.billing_issuer`` set by the API (the
caller's own home tenant after the ``platform:operate`` check, the configured issuer for
the public page, or a customer's own account's issuer). The role's column grants admit
amounts, statuses and dates only. In memory the same answers come from the in-process
rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..settings import settings
from .rows import memory_db, reader_rows
from .service import monthly_value

PLAN_FIELDS = ("id", "code", "name", "description", "price_minor", "currency", "interval", "trial_days", "per_seat",
               "limits", "features", "sort_order")


def _reader(issuer: str, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return reader_rows("anum_platform_reader", {"anum.billing_issuer": issuer}, statement, params)


def catalog(issuer: str | None) -> list[dict[str, Any]]:
    """Published, active plans of ``issuer`` (public columns only, no tenant id)."""
    if not issuer:
        return []
    if settings.repository_backend == "postgresql":
        rows = _reader(issuer, "select " + ", ".join(PLAN_FIELDS) + " from billing_plans "  # nosec B608 - fixed column list
                       "where published and active order by sort_order, price_minor, name")
    else:
        rows = [r for r in memory_db.all_rows("billing_plans") if r["tenant_id"] == issuer and r["published"] and r["active"]]
        rows.sort(key=lambda r: (r["sort_order"], r["price_minor"], r["name"]))
    return [{key: row[key] for key in PLAN_FIELDS} for row in rows]


def _issuer_rows(issuer: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if settings.repository_backend == "postgresql":
        accounts = _reader(issuer, "select tenant_id, display_name, state, credit_balance_minor from billing_accounts")
        subs = _reader(issuer, "select tenant_id, plan_name, status, price_minor, currency, interval, seats, per_seat, "
                               "trial_end, current_period_end, cancel_at_period_end from billing_subscriptions")
        invoices = _reader(issuer, "select tenant_id, status, currency, total_minor, amount_due_minor, due_at "
                                   "from billing_invoices where status = 'open'")
        payments = _reader(issuer, "select tenant_id, amount_minor, currency, received_at from billing_payments")
        return accounts, subs, invoices, payments

    def mine(table: str) -> list[dict[str, Any]]:
        return [r for r in memory_db.all_rows(table) if r.get("issuer_tenant_id") == issuer]

    return mine("billing_accounts"), mine("billing_subscriptions"), [r for r in mine("billing_invoices") if r["status"] == "open"], mine("billing_payments")


def subscriptions(issuer: str, now: datetime) -> list[dict[str, Any]]:
    accounts, subs, invoices, _ = _issuer_rows(issuer)
    current: dict[str, dict[str, Any]] = {}
    for sub in subs:
        held = current.get(sub["tenant_id"])
        if held is None or (held["status"] == "cancelled" and sub["status"] != "cancelled") or (
            (held["status"] == "cancelled") == (sub["status"] == "cancelled") and sub["current_period_end"] > held["current_period_end"]
        ):
            current[sub["tenant_id"]] = sub
    out = []
    for account in accounts:
        sub = current.get(account["tenant_id"])
        open_items = [i for i in invoices if i["tenant_id"] == account["tenant_id"]]
        out.append({
            "tenant_id": account["tenant_id"], "display_name": account["display_name"], "state": account["state"],
            "plan_name": sub["plan_name"] if sub else None, "status": sub["status"] if sub else "none",
            "price_minor": int(sub["price_minor"]) * (int(sub["seats"]) if sub["per_seat"] else 1) if sub else 0,
            "currency": sub["currency"] if sub else "SAR", "interval": sub["interval"] if sub else None,
            "mrr_minor": monthly_value(sub) if sub else 0,
            "trial_end": sub["trial_end"] if sub else None,
            "current_period_end": sub["current_period_end"] if sub else None,
            "cancel_at_period_end": bool(sub["cancel_at_period_end"]) if sub else False,
            "open_balance_minor": sum(int(i["amount_due_minor"]) for i in open_items),
            "overdue": any(i["due_at"] is not None and i["due_at"] <= now for i in open_items),
        })
    out.sort(key=lambda r: (str(r["display_name"]).casefold(), r["tenant_id"]))
    return out


def summary(issuer: str, now: datetime) -> dict[str, Any]:
    rows = subscriptions(issuer, now)
    _, _, invoices, payments = _issuer_rows(issuer)
    counts = {status: 0 for status in ("trialing", "active", "past_due", "paused", "cancelled")}
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] += 1
    mrr = sum(row["mrr_minor"] for row in rows)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "currency": "SAR",
        "mrr_minor": mrr,
        "arr_minor": mrr * 12,
        "counts": counts,
        "organisations": len(rows),
        "read_only": sum(1 for row in rows if row["state"] == "read_only"),
        "open_invoices": len(invoices),
        "open_minor": sum(int(i["amount_due_minor"]) for i in invoices),
        "overdue_minor": sum(int(i["amount_due_minor"]) for i in invoices if i["due_at"] is not None and i["due_at"] <= now),
        "collected_this_month_minor": sum(int(p["amount_minor"]) for p in payments if p["received_at"] >= month),
    }
