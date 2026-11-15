"""Recurring payments and subscriptions (docs/finance.md#recurring-payments).

The finder is ``summary.detect_recurring`` (money out to one payee at a steady interval and amount, three times or
more, not yet a bill). This module says **what kind** of payment it is (rent, phone, internet, streaming, insurance,
school fees, a loan, a remittance, a membership, a utility, an installment plan), keeps **ordinary purchases** out
(a weekly supermarket visit of a similar amount is not a subscription), and, for the bills the owner confirmed, reports
**price changes**, subscriptions **still billing while rarely used**, and links a later payment to its due date when
the owner allowed it (``Bill.auto_link``) and the payment is the **one strong match**:

* the same currency and exactly the bill's amount (within 1 percent, at least the minor unit),
* posted from 7 days before to 10 days after the due date,
* its merchant is the bill's merchant (``Bill.merchant``, the merchant key it was confirmed for) or, without one, it is
  paid from the bill's own account,
* not linked to a bill yet, and no other due date of any bill the owner allowed wants the same payment.

Anything less (two payments that fit, a different amount, no merchant agreement) is left for the owner on the Bills page.
Each automatic link goes through ``links.create`` like a hand match (the owner's lock, idempotency, never more than the
payment), carries the policy ``recurring_bill_v1``, the reason code, the confidence and the evidence in the audit
record, and is undone with ``DELETE /bills/{id}/matches/{link_id}`` (the payment stays in the ledger).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext
from . import txn_types as TT
from .categories import built_in_subcategory
from .models import Bill, Category, Transaction, TransactionType
from .money import quantize
from .rules import fold, has_phrase, merchant_key

logger = logging.getLogger(__name__)

POLICY = "recurring_bill_v1"
KINDS = ("rent", "phone", "internet", "streaming", "subscription", "insurance", "school_fees", "loan", "remittance", "membership", "utility",
         "installment", "other")
# Repeated ordinary purchases are not subscriptions unless something names them as one (a gym, an installment plan).
ORDINARY = frozenset({
    Category.GROCERIES, Category.DINING, Category.FOOD, Category.FUEL, Category.TRANSPORT, Category.SHOPPING, Category.CASH,
    Category.CLOTHING, Category.PERSONAL_CARE, Category.ENTERTAINMENT, Category.HEALTH, Category.TRAVEL, Category.CHARITY,
})
LOAN_WORDS = ("loan", "emi", "car finance", "auto finance", "personal finance", "قرض", "قسط")
WINDOW_BEFORE, WINDOW_AFTER = 7, 10
AMOUNT_TOLERANCE = Decimal("0.01")
PRICE_CHANGE = Decimal("0.05")
STILL_BILLING_DAYS = 45


def kind_of(t: Transaction) -> str:
    """What a recurring payment is, from its type, category, subcategory and words."""
    text = fold(t.description)
    sub = t.subcategory_id or built_in_subcategory(t.category, t.description)
    if t.transaction_type == TransactionType.LOAN_PRINCIPAL or sub == "sys:loan_repayment" or any(has_phrase(text, w) for w in LOAN_WORDS):
        return "loan"
    if t.category in (Category.HOUSING, Category.ROOM_RENT):
        return "rent"
    if t.category == Category.TELECOM:
        return "internet" if sub in ("sys:internet", "sys:home_wifi") else "phone"
    if t.category == Category.SUBSCRIPTIONS:
        if sub in ("sys:streaming", "sys:music"):
            return "streaming"
        return "membership" if sub == "sys:memberships" else "subscription"
    if t.category == Category.INSURANCE:
        return "insurance"
    if t.category == Category.EDUCATION:
        return "school_fees"
    if t.category == Category.FAMILY_SUPPORT:
        return "remittance"
    if t.category == Category.UTILITIES:
        return "utility"
    if sub == "sys:fitness" or has_phrase(text, "membership"):
        return "membership"
    if sub == "sys:installment_purchase":
        return "installment"
    return "other"


def ordinary_purchase(t: Transaction, kind: str) -> bool:
    """A repeated everyday purchase (groceries, eating out, fuel, taxis, shopping): never a subscription by itself."""
    return t.category in ORDINARY and kind == "other"


def counts_for_recurring(t: Transaction) -> bool:
    """Money out that may recur: spending, an uncategorized transfer out, or a loan repayment (a transfer of principal)."""
    if t.amount >= 0:
        return False
    kind = TT.effective(t)
    return kind in TT.SPEND_TYPES or kind in (TransactionType.UNCATEGORIZED, TransactionType.LOAN_PRINCIPAL)


# -- confirmed bills ------------------------------------------------------------------------------------


@dataclass
class BillHealth:
    bill_id: str
    last_paid_on: date | None = None
    last_amount: Decimal | None = None
    price_change: Decimal | None = None  # the newest payment less the bill's amount, when they differ by 5 percent or more
    still_billing_rarely_used: bool = False
    payments: list[str] = field(default_factory=list)  # the newest payments found for it (ids)


def bill_payments(bill: Bill, rows: list[Transaction]) -> list[Transaction]:
    """The owner's payments that are this bill's: the bill's merchant key, else its name's words, in its currency."""
    wanted = bill.merchant or merchant_key(bill.name)
    if not wanted:
        return []
    out = [t for t in rows if t.amount < 0 and t.currency == bill.currency and merchant_key(t.description) == wanted]
    return sorted(out, key=lambda t: (t.posted_on, t.id), reverse=True)


def bill_health(bill: Bill, rows: list[Transaction], today: date) -> BillHealth:
    paid = bill_payments(bill, rows)
    out = BillHealth(bill_id=bill.id, payments=[t.id for t in paid[:6]])
    if not paid:
        return out
    newest = paid[0]
    out.last_paid_on, out.last_amount = newest.posted_on, quantize(-newest.amount, bill.currency)
    difference = out.last_amount - bill.amount
    if bill.amount > 0 and abs(difference) >= bill.amount * PRICE_CHANGE:
        out.price_change = quantize(difference, bill.currency)
    out.still_billing_rarely_used = bill.usage == "rarely" and (today - newest.posted_on).days <= STILL_BILLING_DAYS
    return out


# -- linking a confirmed bill's later payment ------------------------------------------------------------


@dataclass(frozen=True)
class AutoLink:
    bill_id: str
    due_on: date
    canonical_id: str
    amount: Decimal
    currency: str
    reason: str
    confidence: Decimal
    evidence: dict[str, str]


def _amount_fits(expected: Decimal, found: Decimal, currency: str) -> bool:
    tolerance = max(expected * AMOUNT_TOLERANCE, Decimal(1).scaleb(-2))
    return abs(found - expected) <= tolerance


def strong_candidates(bill: Bill, due_on: date, candidates: list[Any]) -> list[Any]:
    """The candidates (``commitments_api.Candidate``) that are strong for this due date."""
    out = []
    for c in candidates:
        if c.currency != bill.currency or c.remaining < bill.amount * (1 - AMOUNT_TOLERANCE):
            continue
        if not (due_on - timedelta(days=WINDOW_BEFORE) <= c.posted_on <= due_on + timedelta(days=WINDOW_AFTER)):
            continue
        if not _amount_fits(bill.amount, c.amount, bill.currency):
            continue
        if bill.merchant:
            if merchant_key(c.merchant) != bill.merchant:
                continue
        elif not (bill.account_id and c.account_id == bill.account_id):
            continue
        out.append(c)
    return out


def plan_auto_links(wanted: list[tuple[Bill, date, list[Any]]]) -> tuple[list[AutoLink], list[tuple[str, date, str]]]:
    """Which due dates get a link: those with exactly one strong payment that no other due date wants too.

    ``wanted``: (bill, due date, the matcher's candidates). Returns the links and the skipped ones with the reason
    (``none``, ``ambiguous`` or ``contested``)."""
    strong = {(bill.id, due): (bill, strong_candidates(bill, due, cands)) for bill, due, cands in wanted}
    claims: dict[str, int] = {}
    for _bill, found in strong.values():
        for c in found:
            claims[c.canonical_id] = claims.get(c.canonical_id, 0) + 1
    links: list[AutoLink] = []
    skipped: list[tuple[str, date, str]] = []
    for (bill_id, due), (bill, found) in sorted(strong.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not found:
            skipped.append((bill_id, due, "none"))
            continue
        if len(found) > 1:
            skipped.append((bill_id, due, "ambiguous"))
            continue
        chosen = found[0]
        if claims.get(chosen.canonical_id, 0) > 1:
            skipped.append((bill_id, due, "contested"))
            continue
        reason = "merchant_amount_window" if bill.merchant else "account_amount_window"
        links.append(AutoLink(
            bill_id=bill_id, due_on=due, canonical_id=chosen.canonical_id, amount=quantize(min(bill.amount, chosen.remaining), bill.currency),
            currency=bill.currency, reason=reason, confidence=Decimal("0.95") if bill.merchant else Decimal("0.9"),
            # Never an amount, a description or a name in the audit record: finance stays private.
            evidence={"posted_on": chosen.posted_on.isoformat(), "days_from_due": str((chosen.posted_on - due).days),
                      "merchant_agrees": "yes" if bill.merchant else "no", "account": "same" if chosen.account_id == bill.account_id else "other",
                      "amount_agrees": "yes", "unique": "yes"},
        ))
    return links, skipped


def run_auto_links(context: TenantContext, *, now: datetime, dry_run: bool = False) -> tuple[int, int]:
    """Link the strong, unique payments of the bills the owner allowed. Returns (linked, left for the owner).

    Reads in short units of work and links through ``links.create`` (each its own unit, under the owner's lock); a
    refused link (taken meanwhile, over-allocated) is left, never forced."""
    from . import links as K
    from .commitments import BILL_DIMENSION, BILL_TARGET, bill_schedule, bill_target_id
    from .commitments_api import find_candidates
    from .links_store import open_link_stores
    from .rules import require_automatable
    from .store import open_finance_store
    from .summary import settings_for, today_in

    require_automatable("link_recurring_bill")
    with open_finance_store(context) as store:
        today = today_in(settings_for(store, context), now)
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill) and b.active and b.auto_link]
    if not bills:
        return 0, 0
    with open_link_stores(context) as (_finance, _sms, links):
        bill_links, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None,
                                         status=None, limit=10_000, offset=0)
    wanted: list[tuple[Bill, date, list[Any]]] = []
    for row in bill_schedule(bills, [l for l in bill_links if getattr(l.status, "value", l.status) != "void"], today,
                             today - timedelta(days=45), today + timedelta(days=WINDOW_BEFORE)):
        if row.status in ("paid", "review"):
            continue
        bill = next(b for b in bills if b.id == row.bill_id)
        wanted.append((bill, row.due_on, find_candidates(context, "bill", around=row.due_on, amount=bill.amount, currency=bill.currency, limit=20)))
    planned, skipped = plan_auto_links(wanted)
    if dry_run:
        return len(planned), len(skipped)
    linked = 0
    for item in planned:
        try:
            made = K.create(context, K.LinkRequest(
                canonical_id=item.canonical_id, dimension=BILL_DIMENSION, role="payment", target_type=BILL_TARGET,
                target_id=bill_target_id(item.bill_id, item.due_on), amount=item.amount, currency=item.currency,
                idempotency_key=f"recurring-auto:{item.bill_id}:{item.due_on.isoformat()}",
            ))
        except K.LinkError as exc:
            logger.info("finance_recurring_link_refused code=%s", exc.code)
            skipped.append((item.bill_id, item.due_on, "refused"))
            continue
        if made.replayed:
            continue
        linked += 1
        from .suggestion_jobs import _audit

        _audit(context, "finance.bill.auto_match", made.link.id, {
            "bill": item.bill_id, "policy": POLICY, "reason": item.reason, "confidence": str(item.confidence), **item.evidence})
    return linked, len(skipped)


__all__ = ["KINDS", "ORDINARY", "POLICY", "AutoLink", "BillHealth", "bill_health", "bill_payments", "counts_for_recurring", "kind_of",
           "ordinary_purchase", "plan_auto_links", "run_auto_links", "strong_candidates"]
