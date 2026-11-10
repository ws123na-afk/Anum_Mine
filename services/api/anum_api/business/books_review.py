"""Accountant review: one list of everything in a business's books that waits for an owner or an accountant
(docs/business-books.md#accountant-review).

Each item says what waits, why, the money and date it is about, the Books section where it is acted on and, for a
draft bill, whether this person may approve it (maker and checker: never the one who made it). Items come from
**producers**, like the personal Finance suggestion queue (``finance/suggestions.py``), but this list is the
business's, not a person's, and it is not that queue:

* ``draft_bill``: a draft made by a recurring bill set to drafts, waiting for approval (``POST /bills/{id}/approve``).
* ``posting_issue``: an event the posting run could not post, with its reason (``too_small`` and any other reason
  but a missing rate).
* ``unconverted``: events in another currency with no usable rate for their day (posting issues ``missing_rate``),
  one item per currency with the count and the earliest day: recording the rate posts them.
* ``allocation_review``: business allocations waiting for review, which block the close.
* ``adjustment_approval``: an adjustment an accountant recorded at or above the approval threshold, waiting for another
  owner or accountant (``POST /adjustments/{id}/approve``); never its maker (``why_not: own_adjustment``). It is not
  posted until approved, and it blocks the close of its month.
* ``vat``: the tax module's VAT return warnings (``tax_review.py``, registered with :func:`register_producer`).
* ``receipt_match``: a receipt in the business's Document vault that matches a bill without evidence by amount, date
  and vendor (``books_receipts.py``); Accept attaches it, nothing is attached on its own.
* ``external``: another module's producer.

The period-close checklist of the previous and the current month comes with the list (``close_months``). Producers
only read; the route answers owners and accountants only (``api_ledger.require_accounting``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import books_ledger as L
from . import books_report as R
from .books_store import open_books_store

logger = logging.getLogger("anum_api.business.books_review")

Kind = Literal["draft_bill", "adjustment_approval", "posting_issue", "unconverted", "allocation_review", "vat", "receipt_match", "external"]
KIND_ORDER: tuple[str, ...] = ("draft_bill", "adjustment_approval", "unconverted", "posting_issue", "allocation_review", "vat", "receipt_match", "external")
MAX_SCAN = 500


class ReviewItem(BaseModel):
    key: str = Field(max_length=200)
    producer: str
    kind: Kind
    detected: str  # English, for the assistant; the web words it from ``kind`` and ``params``
    params: dict[str, str] = Field(default_factory=dict)
    severity: Literal["blocking", "warning"] = "warning"  # blocking: the month cannot close until it is resolved
    occurred_on: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    ref_id: str | None = None
    section: str  # the Books section where it is acted on
    action: Literal["approve", "link", "open"] = "open"
    can_act: bool = True
    why_not: str | None = None  # a code when this person may not act (``own_draft``, ``own_adjustment``)


class CloseMonth(BaseModel):
    month: str
    start: date
    end: date
    status: Literal["closed", "open", "not_ended"]
    issues: int
    drafts: int
    allocations_in_review: int
    adjustments_pending: int = 0
    ready: bool  # ended, nothing waiting and nothing left to post


class Review(BaseModel):
    items: list[ReviewItem]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]
    blocking: int
    close_months: list[CloseMonth]
    closed_through: date | None
    to_post: int
    producers: list[str]
    unavailable: list[str] = Field(default_factory=list)


@dataclass
class ReviewContext:
    context: TenantContext
    today: date
    drafts: list[Any] = field(default_factory=list)
    issues: list[Any] = field(default_factory=list)
    vendors: dict[str, str] = field(default_factory=dict)
    makers: dict[str, set[str]] = field(default_factory=dict)  # draft id: who made it (and its recurring bill)
    allocations: list[Any] = field(default_factory=list)
    pending_adjustments: list[Any] = field(default_factory=list)


Producer = Callable[[ReviewContext], list[ReviewItem]]
_PRODUCERS: dict[str, Producer] = {}


def register_producer(name: str, producer: Producer) -> None:
    """Add a source of review items (for example the tax module's VAT return warnings, kind ``external``). It gets
    the review context, may open its own short unit of work, never writes, and returns items with keys unique to it."""
    if not name or len(name) > 40:
        raise ValueError("A producer name is 1 to 40 characters.")
    _PRODUCERS[name] = producer


def unregister_producer(name: str) -> None:
    _PRODUCERS.pop(name, None)


def producers() -> list[str]:
    return list(_PRODUCERS)


# -- built-in producers ------------------------------------------------------------------------------------


def draft_bills(ctx: ReviewContext) -> list[ReviewItem]:
    out = []
    for bill in ctx.drafts:
        own = ctx.context.user_id in ctx.makers.get(bill.id, set())
        out.append(ReviewItem(
            key=f"draft_bill:{bill.id}", producer="bills", kind="draft_bill", detected="A draft bill waits for approval.",
            params={"vendor": ctx.vendors.get(bill.vendor_id, "")[:80], "reference": bill.reference[:60], "version": str(bill.version),
                    "due": bill.due_date.isoformat()},
            severity="blocking", occurred_on=bill.bill_date, amount=bill.total, currency=bill.currency, ref_id=bill.id, section="bills",
            action="approve", can_act=not own, why_not="own_draft" if own else None,
        ))
    return out


def posting_issues(ctx: ReviewContext) -> list[ReviewItem]:
    out = []
    for issue in ctx.issues:
        if issue.reason == "missing_rate":
            continue
        out.append(ReviewItem(
            key=f"posting_issue:{issue.event_key}"[:200], producer="ledger", kind="posting_issue",
            detected=f"The posting run could not post this {issue.source_kind.replace('_', ' ')} ({issue.reason}).",
            params={"reason": issue.reason, "source_kind": issue.source_kind, "description": issue.description[:80]},
            severity="blocking", occurred_on=issue.event_date, currency=issue.currency, ref_id=issue.source_id, section="ledger",
        ))
    return out


def unconverted(ctx: ReviewContext) -> list[ReviewItem]:
    by_currency: dict[str, list[Any]] = {}
    for issue in ctx.issues:
        if issue.reason == "missing_rate" and issue.currency:
            by_currency.setdefault(issue.currency, []).append(issue)
    out = []
    for currency, rows in sorted(by_currency.items()):
        first = min(row.event_date for row in rows)
        out.append(ReviewItem(
            key=f"unconverted:{currency}:{first.isoformat()}:{len(rows)}", producer="rates", kind="unconverted",
            detected=f"{len(rows)} record(s) in {currency} have no exchange rate for their day, so they are not in the journal.",
            params={"count": str(len(rows)), "first": first.isoformat()}, severity="blocking", occurred_on=first, currency=currency, section="settings",
        ))
    return out


def allocation_reviews(ctx: ReviewContext) -> list[ReviewItem]:
    out = []
    for row in ctx.allocations:
        out.append(ReviewItem(
            key=f"allocation_review:{row.id}", producer="allocations", kind="allocation_review",
            detected="A member's allocation to the business waits for review.", params={},
            severity="blocking", occurred_on=row.posted_on, amount=getattr(row, "amount", None), currency=getattr(row, "currency", None),
            ref_id=row.id, section="allocations",
        ))
    return out


def adjustment_approvals(ctx: ReviewContext) -> list[ReviewItem]:
    out = []
    for row in ctx.pending_adjustments:
        own = row.created_by == ctx.context.user_id
        out.append(ReviewItem(
            key=f"adjustment_approval:{row.id}", producer="adjustments", kind="adjustment_approval",
            detected="An adjustment above the approval threshold waits for another owner or accountant.",
            params={"adjustment_kind": row.kind.value, "description": row.description[:80]},
            severity="blocking", occurred_on=row.entry_date, amount=row.amount, currency=row.currency, ref_id=row.id, section="journal",
            action="approve", can_act=not own, why_not="own_adjustment" if own else None,
        ))
    return out


for _name, _fn in (("bills", draft_bills), ("adjustments", adjustment_approvals), ("ledger", posting_issues), ("rates", unconverted),
                   ("allocations", allocation_reviews)):
    register_producer(_name, _fn)


# -- the list -------------------------------------------------------------------------------------------------


def _months(today: date) -> list[tuple[date, date]]:
    current = R.month_start(today)
    previous = R.month_start(current - timedelta(days=1))
    return [(previous, current - timedelta(days=1)), (current, L.month_end(current))]


def load(context: TenantContext, today: date) -> tuple[ReviewContext, list[CloseMonth], date | None]:
    from .books_service import allocations_by_id

    with open_books_store(context) as store:
        if store.get_settings() is None:
            raise L.LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
        locked = L.locked_through(store)
        drafts, _ = store.find("bills", where={"status": "draft"}, order_by="bill_date", limit=MAX_SCAN)
        pending, _ = store.find("adjustments", where={"status": "pending"}, order_by="entry_date", limit=MAX_SCAN)
        issues, _ = store.find("posting_issues", order_by="event_date", limit=MAX_SCAN)
        vendors = {}
        makers: dict[str, set[str]] = {}
        for bill in drafts:
            if bill.vendor_id not in vendors:
                vendor = store.get("vendors", bill.vendor_id)
                vendors[bill.vendor_id] = vendor.name if vendor is not None else ""
            who = {bill.created_by}
            if bill.template_id and (template := store.get("templates", bill.template_id)) is not None:
                who |= {template.created_by, template.updated_by}
            makers[bill.id] = who
        months: list[CloseMonth] = []
        for start, end in _months(today):
            checks = L._checklist(context, store, start, end)
            ended = end < today
            closed = locked is not None and locked >= end
            months.append(CloseMonth(
                month=f"{start.year:04d}-{start.month:02d}", start=start, end=end,
                status="closed" if closed else "open" if ended else "not_ended",
                issues=checks["issues"], drafts=checks["drafts"], allocations_in_review=checks["allocations_in_review"],
                adjustments_pending=checks["adjustments_pending"],
                ready=ended and not closed and checks["issues"] == 0 and checks["drafts"] == 0 and checks["allocations_in_review"] == 0
                and checks["adjustments_pending"] == 0,
            ))
    allocations = [row for row in allocations_by_id(context).values() if row.status == "needs_review"]
    allocations.sort(key=lambda row: (row.posted_on, row.id))
    return ReviewContext(context=context, today=today, drafts=drafts, issues=issues, vendors=vendors, makers=makers,
                         allocations=allocations[:MAX_SCAN], pending_adjustments=pending), months, locked


def build(context: TenantContext, today: date, *, kind: str | None = None, limit: int = 50, offset: int = 0) -> Review:
    ctx, months, locked = load(context, today)
    found: list[ReviewItem] = []
    unavailable: list[str] = []
    for name, producer in list(_PRODUCERS.items()):
        try:
            found.extend(producer(ctx)[:MAX_SCAN])
        except Exception as exc:  # noqa: BLE001 - one failing producer never hides the others
            logger.warning("books_review_producer_failed producer=%s error_class=%s", name, type(exc).__name__)
            unavailable.append(name)
    unique: dict[str, ReviewItem] = {}
    for item in found:
        unique.setdefault(item.key, item)
    items = list(unique.values())
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    items.sort(key=lambda i: (order.get(i.kind, 99), i.occurred_on or date.min, i.key))
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    chosen = [i for i in items if kind is None or i.kind == kind]
    pending = L.status(context, today)
    # Previous month's close readiness also needs nothing left to post.
    for month in months:
        if pending["to_post"] or pending["to_reverse"]:
            month.ready = False
    return Review(items=chosen[offset:offset + limit], total=len(chosen), limit=limit, offset=offset, counts=counts,
                  blocking=sum(1 for i in items if i.severity == "blocking"), close_months=months, closed_through=locked,
                  to_post=pending["to_post"] + pending["to_reverse"], producers=producers(), unavailable=unavailable)


__all__ = ["CloseMonth", "KIND_ORDER", "Review", "ReviewContext", "ReviewItem", "build", "producers", "register_producer", "unregister_producer"]
