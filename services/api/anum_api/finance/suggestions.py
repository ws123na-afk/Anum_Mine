"""One suggestion and review queue for Finance (docs/finance.md#suggestions).

Every finding that waits for the owner is one ``Suggestion``: what was detected, why, the evidence, a confidence,
the money it is about, the proposed action and whether Accept changes anything. Findings come from **producers**:
the built-in ones below read what the existing pages already work out (uncategorized and low-confidence rows,
statement rows held for review, SMS messages waiting, possible duplicates, refunds and their purchases, payments that
may be a bill, bills due, recurring payments not yet saved as bills, unusual spending, missing statement months and a
possible cash shortage), and other modules add theirs with :func:`register_producer` (the reconciliation workspace's
exceptions come in through ``reconciliation_suggestions.py``). A producer only reads: it returns suggestions and never changes a record.

The owner's decision on a suggestion is stored once per suggestion key (``rules_store.SuggestionDecision``): Accept
and Reject are each claimed with an insert that only one request can win, and a rejected suggestion stays dismissed
(its key never shows again; a different proposal for the same row has a different key). Accept on a ``classify``
action changes the category, subcategory and type of the listed rows as one batch that can be undone; every other
Accept only records that the owner saw it (or opens the page where the real work happens). Existing review pages
keep working: each suggestion links into the page that owns it.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import txn_types as TT
from .models import Account, Category, CategorySource, Transaction, TransactionType
from .rules import AliasBook, canonical_json, history_index, history_suggestion

logger = logging.getLogger("anum_api.finance.suggestions")

Kind = Literal[
    "classification", "conflict", "uncategorized", "statement_hold", "sms_review", "duplicate", "refund_match", "payment_match", "bill_due",
    "subscription", "unusual_spend", "missing_statement", "cash_shortage", "reconciliation", "attribution", "fx_fee", "loan_overdue", "loan_repayment",
    "external",
]
ActionKind = Literal["classify", "confirm_refund", "acknowledge", "open", "link"]
# The order the queue lists kinds in: money at risk first, then what blocks figures, then tidying.
KIND_ORDER: tuple[str, ...] = (
    "cash_shortage", "bill_due", "loan_overdue", "unusual_spend", "reconciliation", "duplicate", "conflict", "classification", "uncategorized", "statement_hold", "sms_review",
    "refund_match", "payment_match", "loan_repayment", "attribution", "fx_fee", "missing_statement", "subscription", "external",
)
RECENT_DAYS = 120  # rows a classification suggestion looks at
HISTORY_DAYS = 400  # what historical reuse learns from
MAX_ITEMS = 2_000  # suggestions built per request at most


class Evidence(BaseModel):
    label: str = Field(max_length=120)
    ref_type: Literal["transaction", "import", "sms_message", "bill", "account", "rule", "debt", "vehicle", "purchase", "business", "other"] = "other"
    ref_id: str | None = None
    value: str | None = Field(default=None, max_length=200)


class Effect(BaseModel):
    """The money a suggestion is about (never added across currencies)."""

    amount: Decimal
    currency: str
    direction: Literal["in", "out", "none"]
    note: str | None = Field(default=None, max_length=200)


class LinkProposal(BaseModel):
    """What Accept links, through the finance links API (so every link check applies): a part of one canonical
    transaction to a bill's due date, a debt, a vehicle cost, a purchase, a loan with a person (a repayment), or a business
    (an allocation)."""

    canonical_id: str
    dimension: Literal["bill", "debt", "vehicle", "purchase", "business", "person_loan"]
    target_type: str
    target_id: str
    amount: Decimal
    currency: str
    role: Literal["payment", "deposit"] | None = None  # the link role; None is ``payment`` (money out)
    # business allocations only
    treatment: Literal["owner_contribution", "reimbursable"] | None = None
    category: str | None = None
    project_id: str | None = None


class ProposedAction(BaseModel):
    kind: ActionKind
    transaction_ids: list[str] = Field(default_factory=list, max_length=200)
    category: Category | None = None
    subcategory_id: str | None = None
    transaction_type: TransactionType | None = None
    link: LinkProposal | None = None


class Suggestion(BaseModel):
    key: str = Field(max_length=200)
    producer: str
    kind: Kind
    detected: str  # what was found (English; the web shows its own words from ``kind`` and ``params``)
    why: str
    params: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    confidence: Decimal | None = None
    effect: list[Effect] = Field(default_factory=list)
    action: ProposedAction
    href: str | None = None  # the existing page that owns it
    occurred_on: date | None = None
    payload_hash: str = ""


@dataclass
class Snapshot:
    """What the built-in producers read, loaded once in one short unit of work."""

    context: TenantContext
    now: datetime
    today: date
    accounts: dict[str, Account]
    rows: list[Transaction]  # the last HISTORY_DAYS days
    aliases: AliasBook
    imports: list[Any] = field(default_factory=list)
    review_messages: list[Any] = field(default_factory=list)
    canon: dict[str, str | None] = field(default_factory=dict)
    bills: list[Any] = field(default_factory=list)
    bill_links: list[Any] = field(default_factory=list)
    timezone: str = "Asia/Dubai"


Producer = Callable[[Snapshot], list[Suggestion]]
_PRODUCERS: dict[str, Producer] = {}


def register_producer(name: str, producer: Producer) -> None:
    """Add a producer of suggestions (for example the reconciliation workspace's exceptions). It gets the snapshot
    (``snapshot.context`` is the owner's scope), opens its own short unit of work if it needs more, never writes, and
    returns ``Suggestion`` objects with ``kind="external"`` (or a built-in kind) and a key unique to it."""
    if not name or len(name) > 40:
        raise ValueError("A producer name is 1 to 40 characters.")
    _PRODUCERS[name] = producer


def unregister_producer(name: str) -> None:
    _PRODUCERS.pop(name, None)


def producers() -> list[str]:
    return list(_PRODUCERS)


def seal(item: Suggestion) -> Suggestion:
    """The payload hash: what the owner sees and accepts (the key and the proposed action)."""
    document = {"key": item.key, "action": item.action.model_dump(mode="json")}
    item.payload_hash = hashlib.sha256(canonical_json(document)).hexdigest()
    return item


def _out(t: Transaction) -> list[Effect]:
    return [Effect(amount=abs(t.amount), currency=t.currency, direction="in" if t.amount > 0 else "out")]


def _txn(t: Transaction) -> Evidence:
    return Evidence(label=t.description[:120], ref_type="transaction", ref_id=t.id, value=f"{t.posted_on.isoformat()} {t.currency} {t.amount}")


# -- built-in producers ------------------------------------------------------------------------------


def classification(snap: Snapshot) -> list[Suggestion]:
    """Uncategorized, low-confidence and conflicting rows; the owner's earlier choice for the same merchant when there is one."""
    since = snap.today - timedelta(days=RECENT_DAYS)
    index = history_index(snap.rows, snap.aliases)  # merchant keys worked out once, not once per row
    memo: dict[tuple[str, bool], Any] = {}  # one answer per merchant and side (rows the owner set are skipped, so none excludes itself)
    out: list[Suggestion] = []
    for t in snap.rows:
        if t.posted_on < since or TT.owner_locked(t):
            continue
        kind_of = snap.accounts.get(t.account_id)
        typed = TT.typed(t, kind_of.kind if kind_of else None)
        uncategorized = typed.type == TransactionType.UNCATEGORIZED
        weak = typed.confidence < TT.REVIEW_BELOW or t.category_source == CategorySource.NONE
        memo_key = (snap.aliases.key(t.description), t.amount < 0)
        if memo_key not in memo:
            memo[memo_key] = history_suggestion(t, [], snap.aliases, index) if memo_key[0] else None
        hint = memo[memo_key]
        if hint is not None and (hint.category, hint.subcategory_id) != (t.category, t.subcategory_id) or hint is not None and uncategorized:
            conflict = not uncategorized and not weak
            out.append(Suggestion(
                key=f"history:{t.id}:{hint.category.value}:{hint.subcategory_id or '-'}", producer="classification",
                kind="conflict" if conflict else "classification",
                detected=f"You filed {hint.merchant} under {hint.category.value} {hint.agree} of {hint.seen} times.",
                why=("A rule filed this row differently from your earlier choices for the same merchant." if conflict
                     else "This row is uncategorized or uncertain, and you chose a category for the same merchant before."),
                params={"merchant": hint.merchant, "category": hint.category.value, "agree": str(hint.agree), "seen": str(hint.seen),
                        "current": t.category.value},
                evidence=[_txn(t)] + [Evidence(label="Your earlier choice", ref_type="transaction", ref_id=i) for i in hint.evidence[:3]],
                confidence=hint.confidence, effect=_out(t),
                action=ProposedAction(kind="classify", transaction_ids=[t.id], category=hint.category, subcategory_id=hint.subcategory_id,
                                      transaction_type=hint.transaction_type if hint.transaction_type != TransactionType.UNCATEGORIZED else None),
                href="#finance-transactions", occurred_on=t.posted_on,
            ))
        elif uncategorized:
            out.append(Suggestion(
                key=f"uncategorized:{t.id}", producer="classification", kind="uncategorized",
                detected="No rule recognised this row.", why="Uncategorized money is counted as neither income nor spending until you file it.",
                params={"description": t.description[:80]}, evidence=[_txn(t)], effect=_out(t),
                action=ProposedAction(kind="open", transaction_ids=[t.id]), href="#finance-transactions", occurred_on=t.posted_on,
            ))
    return out


def statement_holds(snap: Snapshot) -> list[Suggestion]:
    out = []
    for record in snap.imports:
        held = int(getattr(record, "held_count", 0) or 0)
        if held <= 0:
            continue
        out.append(Suggestion(
            key=f"statement_hold:{record.id}:{held}", producer="statement", kind="statement_hold",
            detected=f"{held} row(s) of {record.filename} are held for review.",
            why="They could be more than one recorded payment, so they were not added on their own.",
            params={"count": str(held), "file": record.filename[:80]},
            evidence=[Evidence(label=record.filename[:120], ref_type="import", ref_id=record.id, value=record.created_at.date().isoformat())],
            action=ProposedAction(kind="open"), href="#finance-import", occurred_on=record.created_at.date(),
        ))
    return out


def sms_review(snap: Snapshot) -> list[Suggestion]:
    out = []
    for message in snap.review_messages:
        parsed = message.parsed or {}
        amount, currency = parsed.get("amount"), parsed.get("currency")
        effect = []
        try:
            if amount and currency:
                effect = [Effect(amount=abs(Decimal(str(amount))), currency=str(currency), direction="none")]
        except ArithmeticError:
            effect = []
        out.append(Suggestion(
            key=f"sms_review:{message.id}", producer="sms", kind="sms_review",
            detected="A bank message waits for review.", why=f"It was not recorded on its own ({message.reason or 'needs a check'}).",
            params={"reason": str(message.reason or ""), "merchant": str(parsed.get("merchant") or "")[:80]},
            evidence=[Evidence(label="Bank message", ref_type="sms_message", ref_id=message.id, value=message.received_at.date().isoformat())],
            effect=effect, action=ProposedAction(kind="open"), href="#finance-sms", occurred_on=message.received_at.date(),
        ))
    return out


def duplicates(snap: Snapshot) -> list[Suggestion]:
    from .register_api import _duplicates

    since = snap.today - timedelta(days=RECENT_DAYS)
    recent = [t for t in snap.rows if t.posted_on >= since]
    by_id = {t.id: t for t in recent}
    out = []
    for newer, older in _duplicates(recent, snap.canon).items():
        a, b = by_id.get(newer), by_id.get(older)
        if a is None or b is None:
            continue
        out.append(Suggestion(
            key=f"duplicate:{newer}:{older}", producer="duplicates", kind="duplicate",
            detected="Two records look like the same payment.", why="Same account, amount and currency within a few days, and the descriptions name the same merchant.",
            params={"days": str(abs((a.posted_on - b.posted_on).days))}, evidence=[_txn(a), _txn(b)], confidence=Decimal("0.7"), effect=_out(a),
            action=ProposedAction(kind="open", transaction_ids=[newer, older]), href="#finance-transactions", occurred_on=a.posted_on,
        ))
    return out


def refund_matches(snap: Snapshot) -> list[Suggestion]:
    by_id = {t.id: t for t in snap.rows}
    out = []
    for t in snap.rows:
        if t.transaction_type != TransactionType.REFUND or not t.related_transaction_id or TT.owner_locked(t):
            continue
        if t.type_confidence is not None and t.type_confidence >= Decimal("0.8"):
            continue  # already a match, not a suggestion
        purchase = by_id.get(t.related_transaction_id)
        out.append(Suggestion(
            key=f"refund:{t.id}:{t.related_transaction_id}", producer="refunds", kind="refund_match",
            detected="This refund may give back an earlier purchase.", why="The amount fits, but the descriptions do not fully agree.",
            evidence=[_txn(t)] + ([_txn(purchase)] if purchase else []), confidence=t.type_confidence, effect=_out(t),
            action=ProposedAction(kind="confirm_refund", transaction_ids=[t.id]), href="#finance-transactions", occurred_on=t.posted_on,
        ))
    return out


def bills(snap: Snapshot) -> list[Suggestion]:
    from .commitments import bill_schedule

    out = []
    for occ in bill_schedule(snap.bills, snap.bill_links, snap.today, snap.today - timedelta(days=30), snap.today + timedelta(days=7)):
        if occ.status == "due_soon":
            out.append(Suggestion(
                key=f"bill_due:{occ.bill_id}:{occ.due_on.isoformat()}", producer="bills", kind="bill_due",
                detected=f"{occ.name} is due in {occ.days_until} day(s).", why="No payment is matched to this due date yet.",
                params={"name": occ.name[:80], "days": str(occ.days_until), "due_on": occ.due_on.isoformat()},
                evidence=[Evidence(label=occ.name[:120], ref_type="bill", ref_id=occ.bill_id, value=occ.due_on.isoformat())],
                effect=[Effect(amount=occ.expected, currency=occ.currency, direction="out")], action=ProposedAction(kind="acknowledge"),
                href="#finance-bills", occurred_on=occ.due_on,
            ))
        elif occ.status in ("overdue", "review"):
            # The one payment matcher (GET /payment-candidates) proposes the payment when one fits: Accept links it.
            from .attribution import bill_link

            proposal = bill_link(snap, occ) if occ.status == "overdue" else None
            evidence = [Evidence(label=occ.name[:120], ref_type="bill", ref_id=occ.bill_id, value=occ.due_on.isoformat())]
            params = {"name": occ.name[:80], "due_on": occ.due_on.isoformat()}
            if proposal is not None:
                params["payment"] = proposal[2][:80]
                evidence.append(Evidence(label=proposal[2][:120] or "Payment", ref_type="other", ref_id=proposal[0].canonical_id,
                                         value=f"{proposal[0].currency} {proposal[0].amount}"))
            out.append(Suggestion(
                key=f"payment_match:{occ.bill_id}:{occ.due_on.isoformat()}" + (f":{proposal[0].canonical_id}" if proposal else ""),
                producer="bills", kind="payment_match",
                detected=f"{occ.name} has no matched payment for {occ.due_on.isoformat()}.",
                why=("A recorded payment fits this due date. Accept to match it; the bill then counts as paid."
                     if proposal else "If you paid it, match the payment on the Bills page so it is not counted as still due."),
                params=params, evidence=evidence, confidence=proposal[1] if proposal else None,
                effect=[Effect(amount=occ.expected, currency=occ.currency, direction="out")],
                action=ProposedAction(kind="link", link=proposal[0]) if proposal else ProposedAction(kind="open"),
                href="#finance-bills", occurred_on=occ.due_on,
            ))
    return out


def subscriptions(snap: Snapshot) -> list[Suggestion]:
    from .summary import detect_recurring

    out = []
    for s in detect_recurring(snap.rows, snap.today, [b.name for b in snap.bills], snap.bills):
        out.append(Suggestion(
            key=f"subscription:{s.account_id}:{s.name[:60]}:{s.currency}:{s.cadence.value}", producer="recurring", kind="subscription",
            detected=f"{s.name} is paid {s.cadence.value}, {s.occurrences} times so far.", why="A steady payment that is not saved as a bill yet.",
            params={"name": s.name[:80], "cadence": s.cadence.value, "count": str(s.occurrences)},
            evidence=[Evidence(label=s.name[:120], ref_type="account", ref_id=s.account_id, value=s.next_due_on.isoformat())],
            confidence=Decimal("0.7") if s.occurrences < 4 else Decimal("0.85"),
            effect=[Effect(amount=s.amount, currency=s.currency, direction="out")], action=ProposedAction(kind="open"), href="#finance-bills",
            occurred_on=s.next_due_on,
        ))
    return out


def unusual(snap: Snapshot) -> list[Suggestion]:
    from .guardian_review import typical

    since = snap.today - timedelta(days=30)
    out = []
    for t in snap.rows:
        if t.posted_on < since or t.amount >= 0:
            continue
        kind_of = snap.accounts.get(t.account_id)
        if TT.typed(t, kind_of.kind if kind_of else None).type not in TT.SPEND_TYPES:
            continue
        usual, count = typical(snap.rows, t)
        if usual is None or count < 3 or usual <= 0 or -t.amount < usual * 3:
            continue
        out.append(Suggestion(
            key=f"unusual:{t.id}", producer="unusual", kind="unusual_spend",
            detected=f"{-t.amount} {t.currency} is about {round(-t.amount / usual, 1)} times what you usually pay here.",
            why=f"Your usual payment to this merchant is {usual} {t.currency} ({count} earlier payments).",
            params={"usual": str(usual), "count": str(count)}, evidence=[_txn(t)], confidence=Decimal("0.6"), effect=_out(t),
            action=ProposedAction(kind="acknowledge", transaction_ids=[t.id]), href="#finance-guardian", occurred_on=t.posted_on,
        ))
    return out


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def missing_statements(snap: Snapshot) -> list[Suggestion]:
    """A month with no statement rows between months that have them, or a statement more than 45 days old."""
    out = []
    covered: dict[str, set[str]] = {}
    for t in snap.rows:
        if t.import_id:
            covered.setdefault(t.account_id, set()).add(_month(t.posted_on))
    for account_id, months in covered.items():
        account = snap.accounts.get(account_id)
        if account is None or account.archived or not months:
            continue
        first, last = min(months), max(months)
        year, month = int(first[:4]), int(first[5:])
        missing = []
        while f"{year:04d}-{month:02d}" < last:
            label = f"{year:04d}-{month:02d}"
            if label not in months:
                missing.append(label)
            month += 1
            if month == 13:
                year, month = year + 1, 1
        newest_day = max(t.posted_on for t in snap.rows if t.account_id == account_id and t.import_id)
        if (snap.today - newest_day).days > 45:
            nxt = date(newest_day.year + (newest_day.month == 12), newest_day.month % 12 + 1, 1)
            if _month(nxt) < _month(snap.today):
                missing.append(_month(nxt))
        for label in missing:
            out.append(Suggestion(
                key=f"missing_statement:{account_id}:{label}", producer="statements", kind="missing_statement",
                detected=f"No statement rows for {account.name} in {label}.",
                why="Months on both sides have statement rows, or the newest statement is more than 45 days old.",
                params={"account": account.name[:80], "month": label},
                evidence=[Evidence(label=account.name[:120], ref_type="account", ref_id=account_id, value=label)],
                action=ProposedAction(kind="open"), href="#finance-import",
            ))
    return out


def cash_shortage(snap: Snapshot) -> list[Suggestion]:
    from . import obligations
    from .safe_to_spend import build_safe_to_spend
    from .store import open_finance_store

    links = obligations.load(snap.context)
    with open_finance_store(snap.context) as store:
        view = build_safe_to_spend(store, snap.context, now=snap.now, links=links)
    out = []
    for row in view.currencies:
        if not row.short:
            continue
        out.append(Suggestion(
            key=f"cash_shortage:{row.currency}:{view.month}", producer="safe_to_spend", kind="cash_shortage",
            detected=f"Bills, minimum payments and goals this month are more than the {row.currency} you have.",
            why="Safe to spend is below zero for this currency.",
            params={"currency": row.currency, "month": view.month},
            evidence=[Evidence(label="Safe to spend", value=f"{row.currency} {row.safe_to_spend}")],
            effect=[Effect(amount=abs(row.safe_to_spend), currency=row.currency, direction="out", note="shortfall")],
            action=ProposedAction(kind="acknowledge"), href="#finance-safe",
        ))
    return out


for _name, _fn in (("classification", classification), ("statement", statement_holds), ("sms", sms_review), ("duplicates", duplicates),
                   ("refunds", refund_matches), ("bills", bills), ("recurring", subscriptions), ("unusual", unusual),
                   ("statements", missing_statements), ("safe_to_spend", cash_shortage)):
    register_producer(_name, _fn)


# -- the queue -------------------------------------------------------------------------------------------


def snapshot(context: TenantContext, now: datetime) -> Snapshot:
    from .commitments import BILL_DIMENSION, BILL_TARGET
    from .links_store import open_link_stores
    from .models import Bill
    from .register_api import _active_entries
    from .rules_store import alias_book
    from .sms_store import MessageStatus
    from .summary import settings_for, today_in

    with open_link_stores(context) as (finance, sms, links):
        settings = settings_for(finance, context)
        today = today_in(settings, now)
        rows = finance.transactions_between(context, today - timedelta(days=HISTORY_DAYS), today + timedelta(days=1))
        imports, _ = finance.list_imports(context, limit=50, offset=0)
        messages, _ = sms.list_messages(context, status=MessageStatus.REVIEW.value, limit=50, offset=0)
        entries = _active_entries(sms.list_entries(context, start=today - timedelta(days=RECENT_DAYS + 5), end=today + timedelta(days=1)))
        bill_items = [b for b in finance.list_items(context, "bill") if isinstance(b, Bill)]
        bill_links, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None,
                                         status=None, limit=10_000, offset=0)
        return Snapshot(context=context, now=now, today=today, accounts={a.id: a for a in finance.list_accounts(context)}, rows=rows,
                        aliases=alias_book(finance, context), imports=imports, review_messages=messages,
                        canon={tid: (e.canonical_id or e.id) for tid, e in entries.items()}, bills=bill_items, bill_links=bill_links,
                        timezone=settings.timezone)


class QueueCount(BaseModel):
    kind: str
    count: int


class Queue(BaseModel):
    items: list[Suggestion]
    total: int
    limit: int
    offset: int
    counts: list[QueueCount]
    unavailable: list[str] = Field(default_factory=list)  # producers that failed this time (the rest still show)
    dismissed: int = 0


def build(context: TenantContext, now: datetime, *, kind: str | None = None, limit: int = 25, offset: int = 0) -> tuple[Queue, list[Suggestion]]:
    """Every open suggestion (decided ones left out), newest first within each kind. Returns the page and every open item."""
    from .rules_store import open_rule_stores

    snap = snapshot(context, now)
    found: list[Suggestion] = []
    unavailable: list[str] = []
    for name, producer in list(_PRODUCERS.items()):
        try:
            found.extend(seal(item) for item in producer(snap)[:MAX_ITEMS])
        except Exception as exc:  # noqa: BLE001 - one failing producer never hides the others
            logger.warning("finance_suggestions_producer_failed producer=%s error_class=%s", name, type(exc).__name__)
            unavailable.append(name)
    unique: dict[str, Suggestion] = {}
    for item in found:
        unique.setdefault(item.key, item)
    with open_rule_stores(context) as (_finance, store):
        decided = {d.key for d in store.list_decisions(context, list(unique))}
    open_items = [s for k, s in unique.items() if k not in decided]
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    open_items.sort(key=lambda s: (order.get(s.kind, 99), -(s.occurred_on or date.min).toordinal(), s.key))
    counts: dict[str, int] = {}
    for s in open_items:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    chosen = [s for s in open_items if kind is None or s.kind == kind]
    queue = Queue(items=chosen[offset:offset + limit], total=len(chosen), limit=limit, offset=offset,
                  counts=[QueueCount(kind=k, count=counts[k]) for k in KIND_ORDER if k in counts], unavailable=unavailable,
                  dismissed=len(decided))
    return queue, open_items


__all__ = [
    "KIND_ORDER",
    "Effect",
    "Evidence",
    "LinkProposal",
    "ProposedAction",
    "Queue",
    "Snapshot",
    "Suggestion",
    "build",
    "producers",
    "register_producer",
    "seal",
    "snapshot",
    "unregister_producer",
]

# The reconciliation workspace's exceptions (its module registers itself; imported last, once this module is complete).
# Attribution (business, vehicle, purchase, debt) and card FX fee producers (docs/finance.md#attribution-suggestions).
from . import attribution as _attribution  # noqa: F401
from . import reconciliation_suggestions as _reconciliation_suggestions  # noqa: F401
from . import loan_suggestions as _loan_suggestions  # noqa: F401
