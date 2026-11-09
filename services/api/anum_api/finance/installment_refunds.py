"""Refunds on provider installment plans (Tamara): evidence, arithmetic and what is no longer due.

docs/finance.md#tamara-refunds. Pure functions over records already read; nothing here reads the database,
moves money or changes a bill. The routes (``installment_refunds_api``) save what the owner confirms.

**Evidence** for one order comes from three places, each linked to the order it names:

* ``email``: a Tamara refund or order-updated email kept when the owner approved a Gmail import
  (``InstallmentRefundNotice``). With an amount, or wording that the whole order was refunded, it is strong.
  Without either it only asks for review.
* ``statement``: the owner's imported Tamara statement itself. A row the provider marks refunded or cancelled,
  or a lower amount a later import reported for the same installment and due date, is Tamara's own new
  schedule and is strong. A review row whose notes mention a refund only asks for review.
* ``ledger``: a money-in transaction that names Tamara. Naming the order number it is strong; matching the
  amount of a refund email it confirms money came back; naming only the merchant, or only matching an
  amount, it is weak and asks for review.

**The arithmetic** (Tamara's rule is not the same for every merchant and region, so ANUM never guesses one):

1. Tamara's own data wins: when the statement shows the new schedule (refunded rows or lower amounts),
   exactly that is applied.
2. A full refund, or refunds that add up to at least what was still unpaid, cancel every unpaid installment.
   The rest goes back to the card: shown as money back expected, or received when a ledger credit matches.
3. A partial refund without a new schedule from Tamara is shown as an **estimate** (Tamara's Key Fact
   Statement: spread over the upcoming installments in proportion to their amounts) and stays in review.
   An estimate is never applied, and never marks one installment cancelled.

Amounts are ``Decimal`` in the currency's minor unit; nothing is converted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from .models import Bill, InstallmentRefundNotice, InstallmentStatementEntry, Transaction
from .money import quantize

EvidenceKind = Literal["email", "statement", "ledger"]
Strength = Literal["strong", "weak"]
# counted: part of the refund total; schedule: Tamara's own new schedule; money_back: a credit that came back;
# review: weak, shown with its reason and never applied.
Role = Literal["counted", "schedule", "money_back", "review"]
EvidenceCode = Literal[
    "email_amount", "email_full", "email_duplicate", "email_no_amount",
    "statement_refunded", "statement_lower_amount", "statement_review",
    "ledger_order", "ledger_matches_email", "ledger_merchant", "ledger_amount_only",
]
OrderState = Literal["active", "partly_refunded", "refunded", "needs_review"]
Mode = Literal["none", "cancel_all", "schedule", "estimate"]
ReasonCode = Literal[
    "estimate_only", "weak_only", "more_than_order", "applied_more_than_evidence", "statement_differs", "email_without_amount",
]
InstallmentState = Literal["paid", "due", "partly_refunded", "refunded"]

# A credit posted this long before or after a refund email is the same refund coming back.
LEDGER_BEFORE_DAYS = 3
LEDGER_AFTER_DAYS = 45
# Two refund emails with the same amount this close together describe one refund, not two.
DUPLICATE_DAYS = 3
_REFUND_NOTE = re.compile(r"refund|cancel", re.I)
_TAMARA = "tamara"


class RefundEvidence(BaseModel):
    id: str
    kind: EvidenceKind
    strength: Strength
    role: Role
    code: EvidenceCode
    amount: Decimal | None
    currency: str
    on: date | None
    installment: int | None = None
    link: str | None = None  # a Gmail link (owner only) or a transaction id
    transaction_id: str | None = None


class RefundInstallment(BaseModel):
    installment: int
    total_installments: int
    due_on: date
    amount: Decimal  # the original scheduled amount
    source_status: str
    refunded_before: Decimal  # applied today
    refunded_after: Decimal  # after applying this plan (the same as before when nothing can be applied)
    due_before: Decimal
    due_after: Decimal
    estimate_due: Decimal | None  # only for an estimate: what might be due, never applied
    state_before: InstallmentState
    state_after: InstallmentState


class OrderRefundPlan(BaseModel):
    provider: str
    order_id: str
    merchant: str
    currency: str
    state: OrderState
    applied_state: OrderState  # the state of what is saved now, before this plan
    mode: Mode
    applicable: bool  # strong evidence and a change to make: the owner can confirm it
    pending: bool
    order_total: Decimal
    paid_total: Decimal
    refund_total: Decimal
    credit_back: Decimal  # money back to the card expected for paid installments
    credit_received: Decimal  # of which a matching ledger credit shows it came back
    outstanding_before: Decimal
    outstanding_after: Decimal
    next_due_before: date | None
    next_due_after: date | None
    evidence: list[RefundEvidence]
    installments: list[RefundInstallment]
    reasons: list[ReasonCode]
    plan_hash: str


@dataclass
class _OrderInput:
    provider: str
    order_id: str
    rows: list[InstallmentStatementEntry]
    notices: list[InstallmentRefundNotice] = field(default_factory=list)
    credits: list[tuple[Transaction, EvidenceCode]] = field(default_factory=list)


def order_key(provider: str, order_id: str) -> tuple[str, str]:
    return provider.strip().casefold(), order_id.strip().casefold()


def _is_paid(row: InstallmentStatementEntry) -> bool:
    return row.source_status == "paid"


def _state_of(row: InstallmentStatementEntry, refunded: Decimal) -> InstallmentState:
    if _is_paid(row):
        return "paid"
    if refunded >= row.amount:
        return "refunded"
    return "partly_refunded" if refunded > 0 else "due"


def _order_state(rows: list[InstallmentStatementEntry], refunded: dict[int, Decimal]) -> OrderState:
    """Refunded once nothing is left to pay; partly refunded while some of it still is."""
    if not any(v > 0 for v in refunded.values()):
        return "active"
    unpaid = [r for r in rows if not _is_paid(r)]
    if all(refunded.get(r.installment, Decimal(0)) >= r.amount for r in unpaid):
        return "refunded"
    return "partly_refunded"


def proportional_estimate(unpaid: list[InstallmentStatementEntry], refund: Decimal, currency: str) -> dict[int, Decimal]:
    """Tamara's Key Fact Statement rule for a partial refund: spread over the unpaid installments by amount.

    Each share is rounded to the minor unit and the last installment takes the rounding remainder, so the
    shares add up to the refund exactly."""
    total = sum((r.amount for r in unpaid), Decimal(0))
    if total <= 0 or refund <= 0:
        return {}
    refund = min(refund, total)
    ordered = sorted(unpaid, key=lambda r: r.installment)
    shares: dict[int, Decimal] = {}
    given = Decimal(0)
    for row in ordered[:-1]:
        share = min(quantize(refund * row.amount / total, currency), row.amount)
        shares[row.installment] = share
        given += share
    last = ordered[-1]
    shares[last.installment] = min(refund - given, last.amount)
    return shares


def assign_ledger_credits(orders: list[_OrderInput], credits: list[Transaction]) -> None:
    """Link each Tamara money-in transaction to at most one order, and say how it was linked."""
    for credit in credits:
        text = (credit.description + " " + (credit.notes or "")).casefold()
        if credit.amount <= 0 or _TAMARA not in text:
            continue
        same_currency = [o for o in orders if o.rows and o.rows[0].currency == credit.currency]
        by_order = [o for o in same_currency if len(o.order_id) >= 5 and o.order_id.casefold() in text]
        if len(by_order) == 1:
            # Naming the order and matching a refund email (its amount, or the paid share coming back) is money
            # back for that refund, not a refund of its own.
            matches = _matches_email(by_order[0], credit)
            by_order[0].credits.append((credit, "ledger_matches_email" if matches else "ledger_order"))
            continue
        by_email = [o for o in same_currency if any(n.amount == credit.amount and _near(n.received_on, credit.posted_on) for n in o.notices)]
        if not by_email:
            by_email = [o for o in same_currency if _matches_email(o, credit)]
        if len(by_email) == 1:
            by_email[0].credits.append((credit, "ledger_matches_email"))
            continue
        total = lambda o: sum((r.amount for r in o.rows), Decimal(0))  # noqa: E731
        by_merchant = [o for o in same_currency if len(o.rows[0].merchant.strip()) >= 3 and o.rows[0].merchant.strip().casefold() in text
                       and credit.amount <= total(o)]
        if len(by_merchant) == 1:
            by_merchant[0].credits.append((credit, "ledger_merchant"))
            continue
        by_amount = [o for o in same_currency if credit.amount == total(o) or any(credit.amount == r.amount for r in o.rows)]
        if len(by_amount) == 1:
            by_amount[0].credits.append((credit, "ledger_amount_only"))


def _paid(order: _OrderInput) -> Decimal:
    return sum((r.amount for r in order.rows if _is_paid(r)), Decimal(0))


def _matches_email(order: _OrderInput, credit: Transaction) -> bool:
    """The credit is a refund email of this order coming back: its exact amount, or (up to what was paid) the
    paid share of a refund that Tamara returns to the card, soon after the email."""
    near = [n for n in order.notices if (n.full or n.amount is not None) and _near(n.received_on, credit.posted_on)]
    return any(n.amount == credit.amount for n in near) or (bool(near) and 0 < credit.amount <= _paid(order))


def _near(notice_on: date, posted_on: date) -> bool:
    return notice_on - timedelta(days=LEDGER_BEFORE_DAYS) <= posted_on <= notice_on + timedelta(days=LEDGER_AFTER_DAYS)


def _plan_hash(provider: str, order_id: str, target: dict[int, Decimal], credit_back: Decimal, refund_total: Decimal, evidence: list[RefundEvidence]) -> str:
    payload = json.dumps({
        "order": [provider.casefold(), order_id.casefold()],
        "target": {str(k): str(v) for k, v in sorted(target.items())},
        "credit_back": str(credit_back), "refund_total": str(refund_total),
        "evidence": sorted(e.id for e in evidence),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def plan_order(item: _OrderInput) -> OrderRefundPlan:
    rows = sorted(item.rows, key=lambda r: r.installment)
    first = rows[0]
    currency = first.currency
    q = lambda v: quantize(v, currency)  # noqa: E731
    unpaid = [r for r in rows if not _is_paid(r)]
    order_total = q(sum((r.amount for r in rows), Decimal(0)))
    paid_total = q(sum((r.amount for r in rows if _is_paid(r)), Decimal(0)))
    outstanding_original = q(sum((r.amount for r in unpaid), Decimal(0)))
    applied = {r.installment: r.refunded_amount for r in rows}
    evidence: list[RefundEvidence] = []
    reasons: list[ReasonCode] = []

    # -- email -------------------------------------------------------------------------------------
    counted_notices: list[InstallmentRefundNotice] = []
    full = False
    for notice in sorted(item.notices, key=lambda n: (n.received_on, n.message_ref)):
        base = dict(id="email:" + notice.message_ref, kind="email", amount=notice.amount, currency=notice.currency, on=notice.received_on,
                    link=notice.source_email)
        if notice.currency != currency:
            continue
        if notice.full:
            full = True
            evidence.append(RefundEvidence(**base, strength="strong", role="counted", code="email_full"))
        elif notice.amount is None:
            evidence.append(RefundEvidence(**base, strength="weak", role="review", code="email_no_amount"))
            reasons.append("email_without_amount")
        elif any(c.amount == notice.amount and abs((c.received_on - notice.received_on).days) <= DUPLICATE_DAYS for c in counted_notices):
            evidence.append(RefundEvidence(**base, strength="strong", role="review", code="email_duplicate"))
        else:
            counted_notices.append(notice)
            evidence.append(RefundEvidence(**base, strength="strong", role="counted", code="email_amount"))
    event_total = sum((n.amount for n in counted_notices if n.amount is not None), Decimal(0))

    # -- statement ---------------------------------------------------------------------------------
    schedule: dict[int, Decimal] = {}
    for row in unpaid:
        ref = f"statement:{row.installment}"
        if row.source_status == "refunded":
            schedule[row.installment] = row.amount
            evidence.append(RefundEvidence(id=ref, kind="statement", strength="strong", role="schedule", code="statement_refunded",
                                           amount=row.amount, currency=currency, on=row.due_on, installment=row.installment, link=row.source_email))
        elif row.reported_amount is not None and row.reported_amount < row.amount:
            schedule[row.installment] = q(row.amount - row.reported_amount)
            evidence.append(RefundEvidence(id=ref, kind="statement", strength="strong", role="schedule", code="statement_lower_amount",
                                           amount=schedule[row.installment], currency=currency, on=row.due_on, installment=row.installment,
                                           link=row.source_email))
        elif row.source_status == "review" and _REFUND_NOTE.search(row.review_notes or ""):
            evidence.append(RefundEvidence(id=ref, kind="statement", strength="weak", role="review", code="statement_review",
                                           amount=None, currency=currency, on=row.due_on, installment=row.installment, link=row.source_email))
    schedule_total = sum(schedule.values(), Decimal(0))

    # -- ledger ------------------------------------------------------------------------------------
    credit_received = Decimal(0)
    unmatched_notices = list(counted_notices)
    for credit, code in sorted(item.credits, key=lambda c: (c[0].posted_on, c[0].id)):
        base = dict(id="ledger:" + credit.id, kind="ledger", amount=credit.amount, currency=credit.currency, on=credit.posted_on,
                    link=None, transaction_id=credit.id)
        match = next((n for n in unmatched_notices if n.amount == credit.amount and _near(n.received_on, credit.posted_on)), None)
        if code == "ledger_matches_email" or (code == "ledger_order" and match is not None):
            # (the paid share fallback is decided in assign_ledger_credits, with the order's notices)
            # The refund an email already counted, coming back as money: confirmation, not a second refund.
            if match is not None:
                unmatched_notices.remove(match)
            credit_received += credit.amount
            evidence.append(RefundEvidence(**base, strength="strong", role="money_back", code="ledger_matches_email"))
        elif code == "ledger_order":
            event_total += credit.amount
            credit_received += credit.amount
            evidence.append(RefundEvidence(**base, strength="strong", role="counted", code="ledger_order"))
        else:
            evidence.append(RefundEvidence(**base, strength="weak", role="review", code="ledger_merchant" if code == "ledger_merchant" else "ledger_amount_only"))
    if any(e.strength == "weak" and e.kind == "ledger" for e in evidence):
        reasons.append("weak_only")

    # -- arithmetic --------------------------------------------------------------------------------
    target: dict[int, Decimal] = {}
    mode: Mode = "none"
    estimate: dict[int, Decimal] = {}
    statement_all = bool(unpaid) and all(schedule.get(r.installment, Decimal(0)) >= r.amount for r in unpaid)
    refund_total = Decimal(0)
    # Tamara's own revised schedule is evaluated first: it wins over any amount-based rule.
    if schedule and not statement_all:
        mode = "schedule"
        target = dict(schedule)
        refund_total = max(event_total, schedule_total)
        if full or event_total > schedule_total:
            reasons.append("statement_differs")
    elif full or (event_total > 0 and event_total >= outstanding_original) or statement_all:
        mode = "cancel_all"
        target = {r.installment: r.amount for r in unpaid}
        refund_total = order_total if full else max(event_total, schedule_total)
    elif event_total > 0:
        mode = "estimate"
        refund_total = event_total
        estimate = proportional_estimate(unpaid, event_total, currency)
        reasons.append("estimate_only")
    refund_total = q(refund_total)
    credit_back = q(min(max(refund_total - outstanding_original, Decimal(0)), paid_total)) if mode == "cancel_all" else q(Decimal(0))
    if refund_total > order_total:
        reasons.append("more_than_order")
    if mode in ("cancel_all", "schedule") and any(applied.get(i, Decimal(0)) > target.get(i, Decimal(0)) for i in applied if i in {r.installment for r in unpaid}):
        reasons.append("applied_more_than_evidence")
    blocking = {"estimate_only", "more_than_order", "applied_more_than_evidence"}
    can_apply = mode in ("cancel_all", "schedule") and not (blocking & set(reasons))
    after = {r.installment: (target.get(r.installment, Decimal(0)) if can_apply else applied[r.installment]) for r in rows}
    for r in rows:
        if _is_paid(r):
            after[r.installment] = applied[r.installment]
    pending = can_apply and any(after[i] != applied[i] for i in applied)

    installments = []
    for r in rows:
        due_before = Decimal(0) if _is_paid(r) else q(r.amount - applied[r.installment])
        due_after = Decimal(0) if _is_paid(r) else q(r.amount - after[r.installment])
        est = q(r.amount - applied[r.installment] - estimate[r.installment]) if r.installment in estimate else None
        installments.append(RefundInstallment(
            installment=r.installment, total_installments=r.total_installments, due_on=r.due_on, amount=r.amount, source_status=r.source_status,
            refunded_before=q(applied[r.installment]), refunded_after=q(after[r.installment]), due_before=due_before, due_after=due_after,
            estimate_due=est, state_before=_state_of(r, applied[r.installment]), state_after=_state_of(r, after[r.installment]),
        ))

    applied_state = _order_state(rows, applied)
    if can_apply:
        state = _order_state(rows, after)
    elif reasons or (mode == "none" and any(e.strength == "weak" for e in evidence)):
        state = "needs_review"
    else:
        state = applied_state
    next_before = min((i.due_on for i in installments if i.due_before > 0), default=None)
    next_after = min((i.due_on for i in installments if i.due_after > 0), default=None)
    return OrderRefundPlan(
        provider=first.provider, order_id=first.order_id, merchant=first.merchant, currency=currency, state=state, applied_state=applied_state,
        mode=mode, applicable=pending, pending=pending, order_total=order_total, paid_total=paid_total, refund_total=refund_total,
        credit_back=credit_back, credit_received=q(min(credit_received, credit_back)),
        outstanding_before=q(sum((i.due_before for i in installments), Decimal(0))),
        outstanding_after=q(sum((i.due_after for i in installments), Decimal(0))),
        next_due_before=next_before, next_due_after=next_after, evidence=evidence, installments=installments,
        reasons=list(dict.fromkeys(reasons)), plan_hash=_plan_hash(first.provider, first.order_id, after, credit_back, refund_total, evidence),
    )


def plan_refunds(entries: list[InstallmentStatementEntry], notices: list[InstallmentRefundNotice], credits: list[Transaction]) -> list[OrderRefundPlan]:
    """One plan per order of the statement: its evidence, the new schedule and whether it can be applied."""
    grouped: dict[tuple[str, str], _OrderInput] = {}
    for row in entries:
        key = order_key(row.provider, row.order_id)
        grouped.setdefault(key, _OrderInput(provider=row.provider, order_id=row.order_id, rows=[])).rows.append(row)
    for notice in notices:
        found = grouped.get(order_key(notice.provider, notice.order_id))
        if found is not None:
            found.notices.append(notice)
    items = list(grouped.values())
    assign_ledger_credits(items, credits)
    plans = [plan_order(item) for item in items]
    rank = {"needs_review": 0, "partly_refunded": 1, "refunded": 2, "active": 3}
    return sorted(plans, key=lambda p: (not p.pending, rank[p.state], p.merchant.casefold(), p.order_id))


# -- bills made from statement rows -----------------------------------------------------------------

_BILL_ORDER = re.compile(r"(?:^|\|\s*)Order:\s*([^|]+)", re.I)
_BILL_PART = re.compile(r"(?:^|\|\s*)Installment:\s*([^|]+)", re.I)


def bill_installment(bill: Bill) -> tuple[str, int] | None:
    """The order and installment number a bill imported from a statement is for (from its notes)."""
    notes = bill.notes or ""
    order, part = _BILL_ORDER.search(notes), _BILL_PART.search(notes)
    if not order or not part:
        return None
    try:
        return order.group(1).strip().casefold(), int(part.group(1).strip())
    except ValueError:
        return None


__all__ = [
    "OrderRefundPlan",
    "RefundEvidence",
    "RefundInstallment",
    "bill_installment",
    "order_key",
    "plan_order",
    "plan_refunds",
    "proportional_estimate",
]
