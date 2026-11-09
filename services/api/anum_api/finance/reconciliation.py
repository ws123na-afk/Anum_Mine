"""Bank and card reconciliation: the figures, the exceptions and connection health (docs/finance.md#reconciliation-sessions).

A reconciliation session compares one account's statement for one period with the ledger at the same dates:

    book opening + ledger movements = book closing
    book closing - rows the owner marked "not on this statement" = adjusted book closing
    statement closing - adjusted book closing = unexplained difference

It never forces a balance: there is no plug or balancing entry. A session with an unexplained difference, or with
statement rows the ledger does not hold, stays ``unresolved`` and cannot be completed. Every resolution is the owner's
(``reconciliation_api``); this module only computes. :func:`compute` is pure; :func:`gather` reads the stores in the
caller's unit of work.

Exceptions (each with a stable id, a severity and the actions that resolve it):

* ``not_in_ledger``: a statement row the ledger does not hold, from the import's dispositions: held for review or
  left out by the owner (blocking), left out by the reader (warning), skipped as a duplicate (information);
* ``unmatched_payment`` / ``unmatched_deposit``: a ledger row of the period that no statement confirmed;
* ``pending``: an SMS or Apple Pay record no statement has confirmed yet (a warning once older than
  :data:`PENDING_DAYS`);
* ``possible_duplicate``, ``wrong_direction``, ``reversal_pair`` (a suggestion), ``unknown_currency``,
  ``unrecognized_fee``, ``missing_period``, ``opening_differs`` and, for a card, ``amount_due_differs``.

:func:`list_open_exceptions` is the one reader the unified review queue lists (docs/finance.md#reconciliation-sessions).
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from ..schemas import TenantContext
from . import statement_ledger as SL
from . import txn_types as TT
from .categories import suspected_direction
from .merchant_match import SAME_PAYMENT_DAYS, merchants_agree
from .models import Account, AccountKind, ImportRecord, Transaction, TransactionType
from .money import CURRENCY, quantize
from .reconciliation_store import ReconItem, Reconciliation
from .sms_store import MessageStatus
from .store import MAX_PAGE, MAX_SUMMARY_ROWS, TransactionFilter

PENDING_DAYS = 10  # an SMS or Apple Pay record older than this that no statement confirmed is a warning
REVERSAL_DAYS = 7  # a debit and its equal credit within this many days may be a reversal pair
MAX_MISSING = 24  # months listed as missing, at most
HEALTH_KINDS = frozenset({AccountKind.BANK, AccountKind.CARD})
REVERSAL_WORDS = re.compile(r"(?i)\b(revers(?:al|ed|e)|void(?:ed)?|chargeback|cancel(?:led|lation)?)\b|عكس|إلغاء|الغاء|ملغا")
FEE_WORDS = re.compile(r"(?i)\b(fees?|commission|mark-?up|service charges?|annual charges?|late charges?)\b|رسوم|عمولة|\bرسم\b")

Severity = Literal["blocking", "warning", "info"]
ExceptionKind = Literal["not_in_ledger", "unmatched_payment", "unmatched_deposit", "pending", "possible_duplicate", "wrong_direction",
                        "reversal_pair", "unknown_currency", "unrecognized_fee", "missing_period", "opening_differs", "amount_due_differs"]
SEVERITY_ORDER = {"blocking": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class LedgerRow:
    transaction_id: str
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    confirmed: bool  # a statement confirmed it (imported from one, or attached to one)
    sources: frozenset[str]
    reference: str | None
    kind: TransactionType
    original_currency: str | None = None


@dataclass(frozen=True)
class StatementRow:
    ref: str  # "<import id>#<index in the import's dispositions>"
    import_id: str
    line: int
    posted_on: date | None
    amount: Decimal | None
    description: str
    disposition: str
    reason: str | None


@dataclass(frozen=True)
class Coverage:
    import_id: str
    start: date
    end: date


@dataclass
class Facts:
    """What :func:`compute` needs, read in one unit of work by :func:`gather`."""

    account: Account
    before: Decimal  # the sum of every ledger row before the period
    rows: list[LedgerRow]  # the period's rows, plus a few days either side for reversal pairs
    statement_rows: list[StatementRow]
    coverage: list[Coverage]
    activity: list[date]  # every day the account has ledger activity
    items: list[ReconItem]
    today: date
    paid_since: Decimal = Decimal(0)  # money into a card after the period (a payment towards the amount due)
    fingerprint: str = ""


@dataclass
class Issue:
    id: str
    kind: ExceptionKind
    severity: Severity
    actions: list[str]
    posted_on: date | None = None
    amount: Decimal | None = None
    description: str | None = None
    transaction_id: str | None = None
    other_transaction_id: str | None = None
    statement_ref: str | None = None
    reason: str | None = None
    month: str | None = None
    age_days: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Figures:
    currency: str
    book_opening: Decimal
    deposits: Decimal
    payments: Decimal
    book_closing: Decimal
    statement_opening: Decimal | None
    statement_closing: Decimal
    difference: Decimal  # statement closing - book closing
    pending_total: Decimal  # the rows marked "not on this statement"
    adjusted_book_closing: Decimal
    unexplained: Decimal  # statement closing - adjusted book closing
    opening_difference: Decimal | None


@dataclass
class CardCheck:
    amount_due: Decimal | None
    minimum_due: Decimal | None
    due_on: date | None
    owed_in_ledger: Decimal  # the card's liability at the period's end, positive when owed
    due_difference: Decimal | None  # amount due - owed in the ledger
    paid_since: Decimal
    minimum_covered: bool | None


@dataclass
class Outcome:
    figures: Figures
    card: CardCheck | None
    issues: list[Issue]
    state: Literal["balanced", "unresolved"]
    blockers: list[str]
    can_complete: bool
    changed: bool
    counts: dict[str, int]
    resolved: dict[str, str]  # exception id -> the resolution that settled it


def months_between(first: date, last: date) -> list[str]:
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def month_bounds(month: str) -> tuple[date, date]:
    year, number = (int(x) for x in month.split("-"))
    start = date(year, number, 1)
    end = (date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)) - timedelta(days=1)
    return start, end


def covered(day: date, coverage: list[Coverage]) -> bool:
    return any(c.start <= day <= c.end for c in coverage)


def missing_months(activity: list[date], coverage: list[Coverage], *, before: date, start: date | None = None, end: date | None = None) -> list[str]:
    """Months (before ``before``, the current month) with ledger activity on a day no imported statement covers, and the
    months inside a gap between two statement periods. Oldest first."""
    found: set[str] = set()
    for day in activity:
        if day >= before or (start and day < start) or (end and day > end):
            continue
        if not covered(day, coverage):
            found.add(day.strftime("%Y-%m"))
    spans = sorted(coverage, key=lambda c: (c.start, c.end))
    reach: date | None = None
    for span in spans:
        if reach is not None and span.start > reach + timedelta(days=1):
            gap_start, gap_end = reach + timedelta(days=1), span.start - timedelta(days=1)
            for month in months_between(gap_start, gap_end):
                first, last = month_bounds(month)
                # A month counts as missing only when the gap holds a whole calendar month or activity is in it (above).
                if first >= gap_start and last <= gap_end and (not start or last >= start) and (not end or first <= end) and first < before:
                    found.add(month)
        reach = span.end if reach is None else max(reach, span.end)
    return sorted(found)[:MAX_MISSING]


def coverage_of(record: ImportRecord, rows_by_import: dict[str, list[date]]) -> Coverage | None:
    """The statement period an import covers: the printed period when it was stored, else its rows' dates."""
    check = record.reconciliation or {}
    try:
        if check.get("period_start") and check.get("period_end"):
            return Coverage(record.id, date.fromisoformat(str(check["period_start"])), date.fromisoformat(str(check["period_end"])))
    except ValueError:
        pass
    days = [date.fromisoformat(str(d["posted_on"])) for d in record.dispositions or []
            if d.get("posted_on") and d.get("disposition") != "dropped_by_reader"]
    days = days or rows_by_import.get(record.id, [])
    return Coverage(record.id, min(days), max(days)) if days else None


def fingerprint(account: Account, rows: list[Transaction], end: date) -> str:
    """A hash of every ledger row of the account up to the period's end (id, date, amount) and its opening balance:
    any later change that moves the book closing changes it."""
    digest = hashlib.sha256(f"{account.id}|{account.opening_balance}".encode())
    for row in sorted((r for r in rows if r.posted_on <= end), key=lambda r: r.id):
        digest.update(f"|{row.id}:{row.posted_on.isoformat()}:{row.amount}".encode())
    return digest.hexdigest()


def statement_rows(records: list[ImportRecord], statement_messages: dict[tuple[str, str, str], str], start: date, end: date,
                   own_import: str | None) -> list[StatementRow]:
    """Every row of the account's imports dated inside the period (a row the reader left out without a date only from
    the session's own import). A held row that Review has since settled (confirmed, attached or dismissed) is left out."""
    out: list[StatementRow] = []
    for record in records:
        for index, item in enumerate(record.dispositions or []):
            posted = date.fromisoformat(str(item["posted_on"])) if item.get("posted_on") else None
            if posted is None and record.id != own_import:
                continue
            if posted is not None and not start <= posted <= end:
                continue
            amount = Decimal(str(item["amount"])) if item.get("amount") not in (None, "") else None
            disposition = str(item.get("disposition") or "")
            if disposition == "held_for_review" and posted is not None and amount is not None:
                state = statement_messages.get((record.id, posted.isoformat(), str(abs(amount))))
                if state is not None and state != MessageStatus.REVIEW.value:
                    continue
            out.append(StatementRow(f"{record.id}#{index}", record.id, int(item.get("line") or 0), posted, amount,
                                    str(item.get("description") or ""), disposition, item.get("reason")))
    return out


def _duplicates(rows: list[LedgerRow]) -> dict[str, str]:
    """The register's possible-duplicate signal on the period's rows (same amount within ``SAME_PAYMENT_DAYS`` and a
    shared meaningful merchant word); two rows a statement confirmed separately are two payments."""
    by_amount: dict[Decimal, list[LedgerRow]] = defaultdict(list)
    for row in rows:
        by_amount[row.amount].append(row)
    found: dict[str, str] = {}
    for group in by_amount.values():
        group.sort(key=lambda r: (r.posted_on, r.transaction_id))
        for i, newer in enumerate(group):
            for older in group[:i]:
                if (newer.posted_on - older.posted_on).days > SAME_PAYMENT_DAYS or older.transaction_id in found:
                    continue
                if newer.confirmed and older.confirmed:
                    continue
                if merchants_agree(newer.description, older.description) is True:
                    found[newer.transaction_id] = older.transaction_id
                    break
    return found


def reversal_pairs(rows: list[LedgerRow]) -> list[tuple[LedgerRow, LedgerRow]]:
    """A debit and an equal credit within :data:`REVERSAL_DAYS`, with reversal words in either text or the same bank
    reference. One to one, oldest first. A suggestion only: the owner accepts or ignores it."""
    used: set[str] = set()
    out: list[tuple[LedgerRow, LedgerRow]] = []
    ordered = sorted(rows, key=lambda r: (r.posted_on, r.transaction_id))
    for first in ordered:
        if first.transaction_id in used:
            continue
        for second in ordered:
            if second.transaction_id in used or second.transaction_id == first.transaction_id or second.amount != -first.amount:
                continue
            if abs((second.posted_on - first.posted_on).days) > REVERSAL_DAYS:
                continue
            words = bool(REVERSAL_WORDS.search(first.description) or REVERSAL_WORDS.search(second.description))
            same_ref = bool(first.reference and first.reference == second.reference)
            if words or same_ref:
                debit, credit = (first, second) if first.amount < 0 else (second, first)
                out.append((debit, credit))
                used.update({first.transaction_id, second.transaction_id})
                break
    return out


def wrong_direction(row: LedgerRow) -> str | None:
    if suspected_direction(row.description, row.amount):
        return "rent_shown_as_incoming"
    if row.amount < 0 and row.kind in (TransactionType.INCOME, TransactionType.REFUND, TransactionType.CASHBACK):
        return "money_in_type_paid_out"
    if row.amount > 0 and row.kind in TT.SPEND_TYPES:
        return "spending_type_paid_in"
    return None


def compute(rec: Reconciliation, facts: Facts) -> Outcome:
    """The equation, the card check and every exception of one session. Pure."""
    account, currency = facts.account, rec.currency
    period = [r for r in facts.rows if rec.period_start <= r.posted_on <= rec.period_end]
    live = [i for i in facts.items if i.status == "active"]
    by_txn: dict[str, ReconItem] = {}
    by_ref: dict[str, ReconItem] = {}
    for item in live:
        if item.transaction_id:
            by_txn[item.transaction_id] = item
        if item.other_transaction_id:
            by_txn[item.other_transaction_id] = item
        if item.statement_ref:
            by_ref[item.statement_ref] = item
    opening = quantize(account.opening_balance + facts.before, currency)
    deposits = quantize(sum((r.amount for r in period if r.amount > 0), Decimal(0)), currency)
    payments = quantize(sum((r.amount for r in period if r.amount < 0), Decimal(0)), currency)
    closing = quantize(opening + deposits + payments, currency)
    pending_rows = {i.transaction_id for i in live if i.kind == "pending" and i.transaction_id}
    pending_total = quantize(sum((r.amount for r in period if r.transaction_id in pending_rows), Decimal(0)), currency)
    adjusted = quantize(closing - pending_total, currency)
    figures = Figures(
        currency=currency, book_opening=opening, deposits=deposits, payments=payments, book_closing=closing,
        statement_opening=rec.statement_opening, statement_closing=rec.statement_closing, difference=quantize(rec.statement_closing - closing, currency),
        pending_total=pending_total, adjusted_book_closing=adjusted, unexplained=quantize(rec.statement_closing - adjusted, currency),
        opening_difference=quantize(rec.statement_opening - opening, currency) if rec.statement_opening is not None else None,
    )
    issues: list[Issue] = []
    resolved: dict[str, str] = {}

    for row in facts.statement_rows:
        key = f"not_in_ledger:{row.ref}"
        if row.disposition in ("imported", "attached_to_existing"):
            continue
        if row.ref in by_ref:
            resolved[key] = by_ref[row.ref].kind
            continue
        severity: Severity = ("blocking" if row.disposition in ("held_for_review", "unticked")
                              else "warning" if row.disposition == "dropped_by_reader" else "info")
        actions = ["match", "correct"] if row.amount is not None and row.posted_on is not None else ["match"]
        if row.disposition == "duplicate_skipped":
            actions = ["match"]
        issues.append(Issue(key, "not_in_ledger", severity, actions, posted_on=row.posted_on, amount=row.amount, description=row.description,
                            statement_ref=row.ref, reason=row.disposition, details={"import_id": row.import_id, "line": row.line, "why": row.reason}))

    for row in period:
        if row.transaction_id in by_txn:
            item = by_txn[row.transaction_id]
            for kind in ("unmatched_payment", "unmatched_deposit", "pending", "reversal_pair"):
                resolved[f"{kind}:{row.transaction_id}"] = item.kind
            continue
        if row.confirmed:
            continue
        age = (facts.today - row.posted_on).days
        if row.sources and row.sources <= {"sms", "wallet"}:
            issues.append(Issue(f"pending:{row.transaction_id}", "pending", "warning" if age > PENDING_DAYS else "info", ["attach", "pending", "match"],
                                posted_on=row.posted_on, amount=row.amount, description=row.description, transaction_id=row.transaction_id,
                                age_days=age, reason="sms" if "sms" in row.sources else "wallet"))
            continue
        kind: ExceptionKind = "unmatched_payment" if row.amount < 0 else "unmatched_deposit"
        issues.append(Issue(f"{kind}:{row.transaction_id}", kind, "info" if figures.unexplained == 0 else "warning", ["attach", "pending", "match"],
                            posted_on=row.posted_on, amount=row.amount, description=row.description, transaction_id=row.transaction_id,
                            reason=sorted(row.sources)[0] if row.sources else "manual"))

    for newer, older in _duplicates(period).items():
        row = next(r for r in period if r.transaction_id == newer)
        issues.append(Issue(f"possible_duplicate:{newer}", "possible_duplicate", "warning", ["open"], posted_on=row.posted_on, amount=row.amount,
                            description=row.description, transaction_id=newer, other_transaction_id=older))
    for row in period:
        why = wrong_direction(row)
        if why:
            issues.append(Issue(f"wrong_direction:{row.transaction_id}", "wrong_direction", "warning", ["open"], posted_on=row.posted_on,
                                amount=row.amount, description=row.description, transaction_id=row.transaction_id, reason=why))
        if row.currency != account.currency or (row.original_currency is not None and not CURRENCY.match(row.original_currency)):
            issues.append(Issue(f"unknown_currency:{row.transaction_id}", "unknown_currency", "warning", ["open"], posted_on=row.posted_on,
                                amount=row.amount, description=row.description, transaction_id=row.transaction_id,
                                reason=row.original_currency or row.currency))
        if row.amount < 0 and FEE_WORDS.search(row.description) and row.kind not in (TransactionType.BANK_FEE, TransactionType.INTEREST, TransactionType.TAX):
            issues.append(Issue(f"unrecognized_fee:{row.transaction_id}", "unrecognized_fee", "warning", ["open"], posted_on=row.posted_on,
                                amount=row.amount, description=row.description, transaction_id=row.transaction_id, reason=row.kind.value))
    accepted = {(i.transaction_id, i.other_transaction_id) for i in live if i.kind == "reversal"}
    for debit, credit in reversal_pairs([r for r in facts.rows if r.transaction_id not in by_txn or by_txn[r.transaction_id].kind == "reversal"]):
        if not (rec.period_start <= debit.posted_on <= rec.period_end or rec.period_start <= credit.posted_on <= rec.period_end):
            continue
        key = f"reversal_pair:{debit.transaction_id}"
        if (debit.transaction_id, credit.transaction_id) in accepted:
            resolved[key] = "reversal"
            continue
        issues.append(Issue(key, "reversal_pair", "info", ["accept_reversal"], posted_on=debit.posted_on, amount=debit.amount,
                            description=debit.description, transaction_id=debit.transaction_id, other_transaction_id=credit.transaction_id,
                            details={"credit_on": credit.posted_on.isoformat(), "credit_description": credit.description}))
    if account.kind in HEALTH_KINDS:
        before = date(facts.today.year, facts.today.month, 1)
        for month in missing_months(facts.activity, facts.coverage, before=before, start=rec.period_start, end=rec.period_end):
            issues.append(Issue(f"missing_period:{month}", "missing_period", "warning", ["import"], month=month))
    if figures.opening_difference is not None and figures.opening_difference != 0:
        issues.append(Issue("opening_differs", "opening_differs", "warning", [], amount=figures.opening_difference))

    card: CardCheck | None = None
    if account.kind == AccountKind.CARD:
        owed = quantize(-closing, currency)
        due_difference = quantize(rec.amount_due - owed, currency) if rec.amount_due is not None else None
        minimum_covered = (facts.paid_since >= rec.minimum_due) if rec.minimum_due is not None else None
        card = CardCheck(rec.amount_due, rec.minimum_due, rec.due_on, owed, due_difference, quantize(facts.paid_since, currency), minimum_covered)
        if due_difference is not None and due_difference != 0:
            issues.append(Issue("amount_due_differs", "amount_due_differs", "warning", [], amount=due_difference))

    issues.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.posted_on or date.min, i.id))
    blockers: list[str] = []
    if figures.unexplained != 0:
        blockers.append("difference")
    if any(i.severity == "blocking" for i in issues):
        blockers.append("statement_rows_not_in_ledger")
    counts: dict[str, int] = defaultdict(int)
    for issue in issues:
        counts[issue.kind] += 1
        counts[issue.severity] += 1
    changed = rec.status == "completed" and bool(rec.fingerprint) and rec.fingerprint != facts.fingerprint
    return Outcome(figures=figures, card=card, issues=issues, state="balanced" if not blockers else "unresolved", blockers=blockers,
                   can_complete=rec.status == "open" and not blockers, changed=changed, counts=dict(counts), resolved=resolved)


# -- reading the stores ------------------------------------------------------------------------------------


def account_imports(finance: Any, context: TenantContext, account_id: str) -> list[ImportRecord]:
    out: list[ImportRecord] = []
    offset = 0
    while True:
        page, total = finance.list_imports(context, limit=MAX_PAGE, offset=offset)
        out.extend(r for r in page if r.account_id == account_id)
        offset += len(page)
        if not page or offset >= total or offset >= 5_000:
            return out


def account_rows(finance: Any, context: TenantContext, account_id: str) -> list[Transaction]:
    rows, _ = finance.list_transactions(context, TransactionFilter(account_id=account_id), limit=MAX_SUMMARY_ROWS, offset=0)
    return rows


def coverage_for(records: list[ImportRecord], rows: list[Transaction]) -> list[Coverage]:
    by_import: dict[str, list[date]] = defaultdict(list)
    for row in rows:
        if row.import_id:
            by_import[row.import_id].append(row.posted_on)
    return [c for c in (coverage_of(r, by_import) for r in records) if c is not None]


def statement_message_states(sms: Any, context: TenantContext) -> dict[tuple[str, str, str], str]:
    """(import id, date, amount) of statement rows held in Review and their status now."""
    out: dict[tuple[str, str, str], str] = {}
    offset = 0
    while offset < 5_000:
        page, total = sms.list_messages(context, status=None, limit=200, offset=offset)
        for message in page:
            parsed = message.parsed or {}
            if message.source == "statement" and parsed.get("import_id"):
                key = (str(parsed["import_id"]), str(parsed.get("occurred_on")), str(parsed.get("amount")))
                # The newest outcome wins: a held row later confirmed has an attached or posted twin.
                status = getattr(message.status, "value", str(message.status))
                if out.get(key) in (None, MessageStatus.REVIEW.value):
                    out[key] = status
        offset += len(page)
        if not page or offset >= total:
            break
    return out


def gather(finance: Any, sms: Any, links: Any, context: TenantContext, account: Account, rec: Reconciliation, items: list[ReconItem],
           today: date, *, all_rows: list[Transaction] | None = None, imports: list[ImportRecord] | None = None,
           messages: dict[tuple[str, str, str], str] | None = None) -> Facts:
    rows = all_rows if all_rows is not None else account_rows(finance, context, account.id)
    records = imports if imports is not None else account_imports(finance, context, account.id)
    window_start, window_end = rec.period_start - timedelta(days=REVERSAL_DAYS), rec.period_end + timedelta(days=REVERSAL_DAYS)
    recorded = {r.transaction_id: r for r in SL.records(finance, sms, context, window_start, window_end, account_ids={account.id}) if r.transaction_id}
    entries = {r.transaction_id: r for r in recorded.values()}
    ledger: list[LedgerRow] = []
    for txn in rows:
        if not window_start <= txn.posted_on <= window_end:
            continue
        found = entries.get(txn.id)
        sources = found.sources if found is not None else frozenset({"statement" if txn.import_id else "manual"})
        confirmed = bool(txn.import_id) or (found is not None and found.reconciled)
        ledger.append(LedgerRow(txn.id, txn.posted_on, txn.amount, txn.currency, txn.description, confirmed, frozenset(sources),
                                found.reference if found is not None else None, TT.effective(txn, account.kind),
                                found.original_currency if found is not None else None))
    before = sum((t.amount for t in rows if t.posted_on < rec.period_start), Decimal(0))
    paid = sum((t.amount for t in rows if t.posted_on > rec.period_end and t.amount > 0 and (rec.due_on is None or t.posted_on <= rec.due_on)), Decimal(0))
    states = messages if messages is not None else statement_message_states(sms, context)
    return Facts(account=account, before=before, rows=ledger,
                 statement_rows=statement_rows(records, states, rec.period_start, rec.period_end, rec.import_id),
                 coverage=coverage_for(records, rows), activity=sorted({t.posted_on for t in rows}), items=items, today=today,
                 paid_since=paid, fingerprint=fingerprint(account, rows, rec.period_end))


# -- connection health (Phase 5) ---------------------------------------------------------------------------


@dataclass
class Health:
    account_id: str
    kind: str
    currency: str
    last_statement_end: date | None
    last_import_at: Any
    last_sms_on: date | None
    last_apple_pay_on: date | None
    missing_months: list[str]
    gaps: int
    reconciled_through: date | None
    open_reconciliation_id: str | None
    changed_reconciliation_id: str | None
    next_step: str
    next_month: str | None
    bank_feed: str = "not_connected"  # ANUM never claims a live bank connection: statements, SMS and Apple Pay only


def health_for(account: Account, rows: list[Transaction], records: list[ImportRecord], recorded: list[Any], sessions: list[Reconciliation],
               changed: set[str], today: date) -> Health:
    coverage = coverage_for(records, rows)
    this_month = date(today.year, today.month, 1)
    activity = sorted({t.posted_on for t in rows})
    missing = missing_months(activity, coverage, before=this_month) if account.kind in HEALTH_KINDS else []
    last_end = max((c.end for c in coverage), default=None)
    sms_days = [r.posted_on for r in recorded if "sms" in r.sources]
    wallet_days = [r.posted_on for r in recorded if "wallet" in r.sources]
    done = [s for s in sessions if s.status == "completed" and s.id not in changed]
    through = max((s.period_end for s in done), default=None)
    open_one = next((s for s in sorted(sessions, key=lambda s: s.period_end, reverse=True) if s.status == "open"), None)
    changed_one = next((s for s in sessions if s.id in changed), None)
    if account.kind not in HEALTH_KINDS:
        step, month = "none", None
    elif missing:
        step, month = "import_statement", missing[0]
    elif changed_one is not None:
        step, month = "review_changes", changed_one.period_end.strftime("%Y-%m")
    elif open_one is not None:
        step, month = "finish_reconciliation", open_one.period_end.strftime("%Y-%m")
    elif last_end is not None and (through is None or last_end > through):
        step, month = "reconcile", last_end.strftime("%Y-%m")
    elif not records and not activity:
        step, month = "import_statement", None
    else:
        previous = this_month - timedelta(days=1)
        step, month = ("import_statement", previous.strftime("%Y-%m")) if last_end is None or last_end < previous else ("up_to_date", None)
    gaps = sum(1 for a, b in zip(sorted(coverage, key=lambda c: c.start), sorted(coverage, key=lambda c: c.start)[1:], strict=False)
               if b.start > a.end + timedelta(days=1))
    return Health(account_id=account.id, kind=account.kind.value, currency=account.currency, last_statement_end=last_end,
                  last_import_at=max((r.created_at for r in records), default=None), last_sms_on=max(sms_days, default=None),
                  last_apple_pay_on=max(wallet_days, default=None), missing_months=missing, gaps=gaps, reconciled_through=through,
                  open_reconciliation_id=open_one.id if open_one else None, changed_reconciliation_id=changed_one.id if changed_one else None,
                  next_step=step, next_month=month)


__all__ = ["CardCheck", "Coverage", "Facts", "Figures", "Health", "Issue", "LedgerRow", "Outcome", "PENDING_DAYS", "REVERSAL_DAYS", "StatementRow",
           "compute", "coverage_of", "fingerprint", "gather", "health_for", "missing_months", "reversal_pairs", "statement_rows"]
