"""Recurring payments and subscriptions under ``/api/v1/finance`` (docs/finance.md#recurring-payments).

* ``GET /recurring``: the steady payments found and not saved yet (rent, phone, internet, streaming, insurance, school
  fees, loans, remittances, memberships, ...), each with its kind, the payments it rests on and a price change, and the
  bills the owner keeps with their newest payment, a price change, "still billing while rarely used" and whether a
  later payment links itself.
* ``POST /recurring/confirm``: keep a found payment as a bill (source ``detected``, with its merchant), optionally
  letting later strong, unique payments link to its due dates (``auto_link``).
* ``POST /recurring/reject``: not a bill; it is not suggested again (the same dismissal as the Suggestions queue).
* ``PATCH /recurring/bills/{id}``: how a kept bill links and how much it is used.
* ``POST /recurring/link-now``: link the strong, unique payments of the bills that allow it now (the daily job does
  the same); each link is undone on the Bills page.

Reading needs ``finance:read``; changing ``finance:manage``. Everything is the caller's own.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import recurring as R
from .api import BillInput, _audit, _build_bill, _invalid, _manage, _not_found, _read
from .models import Bill, BillSource, Cadence, Category
from .rules_store import SuggestionDecision, open_rule_stores
from .store import open_finance_store
from .summary import BillSuggestion, detect_recurring, settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

LOOKBACK_DAYS = 400


def suggestion_key(s: BillSuggestion) -> str:
    """The same key the Suggestions queue gives this payment (``suggestions.subscriptions``), so a dismissal holds in both."""
    return f"subscription:{s.account_id}:{s.name[:60]}:{s.currency}:{s.cadence.value}"


class Found(BillSuggestion):
    key: str


class KeptBill(BaseModel):
    id: str
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    next_due_on: date
    category: Category
    account_id: str | None
    active: bool
    source: BillSource
    merchant: str | None
    recurring_kind: str | None
    auto_link: bool
    usage: str | None
    last_paid_on: date | None
    last_amount: Decimal | None
    price_change: Decimal | None
    still_billing_rarely_used: bool


class RecurringView(BaseModel):
    today: date
    found: list[Found]
    kept: list[KeptBill]
    dismissed: int  # found payments the owner said are not bills


@router.get("/recurring", response_model=RecurringView)
def recurring(q: str | None = Query(default=None, max_length=80), context: TenantContext = Depends(tenant_context)) -> RecurringView:
    _read(context)
    from datetime import timedelta

    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context))
        rows = store.transactions_between(context, today - timedelta(days=LOOKBACK_DAYS), today)
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    found = detect_recurring(rows, today, [b.name for b in bills], bills)
    keys = [suggestion_key(s) for s in found]
    with open_rule_stores(context) as (_finance, rules):
        decided = {d.key for d in rules.list_decisions(context, keys)}
    needle = " ".join((q or "").casefold().split())
    out = [Found(**s.model_dump(), key=k) for s, k in zip(found, keys, strict=True) if k not in decided]
    if needle:
        out = [f for f in out if needle in f.name.casefold()]
    kept: list[KeptBill] = []
    for bill in sorted(bills, key=lambda b: (not b.active, b.name.casefold())):
        if bill.cadence == Cadence.ONCE:
            continue
        if needle and needle not in bill.name.casefold():
            continue
        health = R.bill_health(bill, rows, today)
        kept.append(KeptBill(
            id=bill.id, name=bill.name, amount=bill.amount, currency=bill.currency, cadence=bill.cadence, next_due_on=bill.next_due_on,
            category=bill.category, account_id=bill.account_id, active=bill.active, source=bill.source, merchant=bill.merchant,
            recurring_kind=bill.recurring_kind, auto_link=bill.auto_link, usage=bill.usage, last_paid_on=health.last_paid_on,
            last_amount=health.last_amount, price_change=health.price_change, still_billing_rarely_used=health.still_billing_rarely_used,
        ))
    return RecurringView(today=today, found=out, kept=kept, dismissed=sum(1 for k in keys if k in decided))


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, max_length=200)  # the found payment's key: it leaves the found list and the queue
    name: str = Field(min_length=1, max_length=120)
    amount: Any
    currency: str = Field(min_length=3, max_length=3)
    cadence: Cadence
    next_due_on: date
    category: Category = Category.UTILITIES
    account_id: str | None = Field(default=None, max_length=80)
    merchant: str | None = Field(default=None, max_length=120)
    recurring_kind: str | None = Field(default=None, max_length=30)
    auto_link: bool = False
    usage: str | None = Field(default=None, pattern=r"^(often|sometimes|rarely)$")


def _decide(context: TenantContext, key: str, status: str) -> bool:
    with open_rule_stores(context) as (_finance, rules):
        return rules.claim_decision(SuggestionDecision(
            id=new_id("fin_sdec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, key=key,
            producer="recurring", status=status, payload_hash="", decided_by=context.user_id, decided_at=utc_now()))  # type: ignore[arg-type]


@router.post("/recurring/confirm", response_model=KeptBill, status_code=201)
def confirm(body: ConfirmInput, context: TenantContext = Depends(tenant_context),
            repository: AnumRepository = Depends(repository_context)) -> KeptBill:
    """Keep a found payment as a bill. With ``auto_link``, a later payment that is its one strong match links itself."""
    _manage(context)
    if body.cadence == Cadence.ONCE:
        raise _invalid("A recurring payment repeats: choose weekly, monthly, quarterly or yearly.")
    if body.recurring_kind is not None and body.recurring_kind not in R.KINDS:
        raise _invalid("Choose what kind of payment this is from the list.")
    if body.auto_link and not (body.merchant or body.account_id):
        raise _invalid("To link later payments by themselves, keep the merchant or choose the account it is paid from.")
    with open_finance_store(context) as store:
        bill = _build_bill(store, context, BillInput(name=body.name, amount=body.amount, currency=body.currency, cadence=body.cadence,
                                                     next_due_on=body.next_due_on, category=body.category, account_id=body.account_id,
                                                     source=BillSource.DETECTED), None)
        bill.merchant = " ".join((body.merchant or "").split()) or None
        bill.recurring_kind, bill.auto_link, bill.usage = body.recurring_kind, body.auto_link, body.usage
        store.save_item("bill", bill)
    if body.key:
        _decide(context, body.key, "accepted")
    _audit(repository, context, "finance.recurring.confirm", bill.id, {"kind": bill.recurring_kind or "other", "auto_link": bill.auto_link})
    return KeptBill(id=bill.id, name=bill.name, amount=bill.amount, currency=bill.currency, cadence=bill.cadence, next_due_on=bill.next_due_on,
                    category=bill.category, account_id=bill.account_id, active=bill.active, source=bill.source, merchant=bill.merchant,
                    recurring_kind=bill.recurring_kind, auto_link=bill.auto_link, usage=bill.usage, last_paid_on=None, last_amount=None,
                    price_change=None, still_billing_rarely_used=False)


class RejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=3, max_length=200)


class Decided(BaseModel):
    key: str
    status: str


@router.post("/recurring/reject", response_model=Decided)
def reject(body: RejectInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Decided:
    """Not a bill: it is not suggested again here or in Suggestions (Suggestions, Dismissed, restores it)."""
    _manage(context)
    if not body.key.startswith("subscription:"):
        raise _invalid("That is not a found recurring payment.")
    if not _decide(context, body.key, "rejected"):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="This payment was already decided.")
    _audit(repository, context, "finance.recurring.reject", "recurring", {})
    return Decided(key=body.key, status="rejected")


class BillSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_link: bool | None = None
    usage: str | None = Field(default=None, pattern=r"^(often|sometimes|rarely|unknown)$")
    recurring_kind: str | None = Field(default=None, max_length=30)
    merchant: str | None = Field(default=None, max_length=120)


@router.patch("/recurring/bills/{bill_id}", response_model=KeptBill)
def update_settings(bill_id: str, body: BillSettings, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> KeptBill:
    """How a kept bill links its later payments and how much it is used (``unknown`` clears the usage)."""
    _manage(context)
    changes = body.model_dump(exclude_unset=True)
    from datetime import timedelta

    with open_finance_store(context) as store:
        bill = store.get_item(context, "bill", bill_id)
        if not isinstance(bill, Bill):
            raise _not_found("Bill")
        if "recurring_kind" in changes and body.recurring_kind is not None and body.recurring_kind not in R.KINDS:
            raise _invalid("Choose what kind of payment this is from the list.")
        if "merchant" in changes:
            bill.merchant = " ".join((body.merchant or "").split()) or None
        if "recurring_kind" in changes:
            bill.recurring_kind = body.recurring_kind
        if "usage" in changes:
            bill.usage = None if body.usage in (None, "unknown") else body.usage
        if body.auto_link is not None:
            if body.auto_link and not (bill.merchant or bill.account_id):
                raise _invalid("To link later payments by themselves, keep the merchant or choose the account it is paid from.")
            bill.auto_link = body.auto_link
        bill.updated_at = utc_now()
        store.save_item("bill", bill)
        today = today_in(settings_for(store, context))
        rows = store.transactions_between(context, today - timedelta(days=LOOKBACK_DAYS), today)
    health = R.bill_health(bill, rows, today)
    _audit(repository, context, "finance.recurring.settings", bill.id, {"fields": sorted(changes), "auto_link": bill.auto_link})
    return KeptBill(id=bill.id, name=bill.name, amount=bill.amount, currency=bill.currency, cadence=bill.cadence, next_due_on=bill.next_due_on,
                    category=bill.category, account_id=bill.account_id, active=bill.active, source=bill.source, merchant=bill.merchant,
                    recurring_kind=bill.recurring_kind, auto_link=bill.auto_link, usage=bill.usage, last_paid_on=health.last_paid_on,
                    last_amount=health.last_amount, price_change=health.price_change, still_billing_rarely_used=health.still_billing_rarely_used)


class LinkNowResult(BaseModel):
    linked: int
    left: int  # due dates with no payment, or more than one that fits: for the owner on the Bills page


@router.post("/recurring/link-now", response_model=LinkNowResult)
def link_now(context: TenantContext = Depends(tenant_context)) -> LinkNowResult:
    """Link the strong, unique payments of the bills that allow it, now. Each link is undone on the Bills page."""
    _manage(context)
    linked, left = R.run_auto_links(context, now=utc_now())
    return LinkNowResult(linked=linked, left=left)


__all__ = ["router", "suggestion_key"]
