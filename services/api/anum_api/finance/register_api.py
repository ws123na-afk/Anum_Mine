"""The transaction register and account overview behind Finance, Transactions and Accounts
(docs/finance.md#register-and-account-overview).

Read only. Under ``/api/v1/finance``; ``finance:read``; every row is the caller's own (owner-only RLS).
Nothing here posts, links or moves money: it joins what the ledger already holds so the web can show,
per transaction, where it came from (SMS, Apple Pay, a statement or the person's own entry), whether a
receipt is linked, whether a statement confirmed it and whether it looks like a duplicate; and per
account, which sources feed it, when it was last reconciled and what needs attention.

Figures are never added across currencies. The original SMS text is shown only while the person's
retention setting keeps it (``body_retention_days``); account and card numbers are already masked.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import tenant_context
from ..schemas import TenantContext, utc_now
from .api import TransactionView, _filter, _read, _view
from .categories import categorize
from .ledger import EntrySource, EntryStatus, JournalEntry
from .links_store import open_link_stores
from .models import Account, AccountKind, Category, CategorySource, Transaction
from .money import quantize
from .sms_service import settings_for as sms_settings_for
from .sms_store import MessageStatus, SmsMessage, source_kind
from .store import MAX_PAGE, MAX_SUMMARY_ROWS, TransactionFilter
from .summary import settings_for, today_in, upcoming_bills

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

# The register reads at most this many matching rows (the CSV export's cap); beyond it the answer says so.
SCAN_LIMIT = 10_000
DUPLICATE_DAYS = 2  # the ledger's own window for "the same payment" when no time is known
MAX_LINKS = 50_000

Origin = Literal["sms", "apple_pay", "statement", "manual"]
RowStatus = Literal["linked", "needs_review", "duplicate_candidate", "uncategorized", "recorded"]
Evidence = Literal["linked", "missing", "not_needed"]


class SourceCounts(BaseModel):
    currency: str
    amount: Decimal


class RegisterRow(TransactionView):
    canonical_id: str | None
    origin: Origin
    statement_format: Literal["csv", "pdf"] | None
    sources: list[str]
    reconciled: bool
    receipts: int
    evidence: Evidence
    links_need_review: int
    duplicate_of: str | None
    status: RowStatus
    running_balance: Decimal | None


class RegisterTotals(BaseModel):
    count: int
    categorized: int
    receipts_missing: int
    needs_review: int
    duplicate_candidates: int
    matched: int
    inflow: list[SourceCounts]
    outflow: list[SourceCounts]


class RegisterBalance(BaseModel):
    currency: str
    beginning: Decimal
    ending: Decimal


class RegisterPage(BaseModel):
    items: list[RegisterRow]
    total: int
    limit: int
    offset: int
    truncated: bool
    totals: RegisterTotals
    balances: list[RegisterBalance]
    review_count: int
    ledger_kind: Literal["personal", "business"]


class SourceView(BaseModel):
    kind: str
    status: str
    received_at: datetime
    sender: str | None
    text: str | None  # the masked message, only while it is kept
    kept_until: datetime | None


class RegisterDetail(BaseModel):
    row: RegisterRow
    account_name: str
    kind: str | None
    merchant: str | None
    reference: str | None
    suggested_category: Category
    suggested_by: CategorySource
    sources: list[SourceView]
    receipt_document_ids: list[str]
    duplicate: RegisterRow | None


def _active_entries(entries: list[JournalEntry]) -> dict[str, JournalEntry]:
    """Stage 2 transaction id to the entry that currently stands for it (not reversed, not a reversal)."""
    out: dict[str, JournalEntry] = {}
    for entry in sorted(entries, key=lambda e: (e.created_at, e.id)):
        if entry.status != EntryStatus.POSTED or entry.source == EntrySource.REVERSAL:
            continue
        for posting in entry.postings:
            if posting.transaction_id:
                out[posting.transaction_id] = entry
    return out


def _words(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) >= 3 and not w.isdigit()}


def _duplicates(rows: list[Transaction], canon: dict[str, str | None]) -> dict[str, str]:
    """A row that looks like another record of the same payment: the same account, amount and currency within
    two days, descriptions sharing a word, and not already the same canonical transaction. The older row is the
    original; the newer one is the candidate. A signal for the person, never acted on."""
    by_key: dict[tuple[str, Decimal, str], list[Transaction]] = defaultdict(list)
    for row in rows:
        by_key[(row.account_id, row.amount, row.currency)].append(row)
    found: dict[str, str] = {}
    for group in by_key.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda t: (t.posted_on, t.created_at, t.id))
        for i, newer in enumerate(group):
            if newer.id in found:
                continue
            for older in group[:i]:
                if (newer.posted_on - older.posted_on).days > DUPLICATE_DAYS:
                    continue
                same_canon = canon.get(newer.id) is not None and canon.get(newer.id) == canon.get(older.id)
                if same_canon or not (_words(newer.description) & _words(older.description)):
                    continue
                if newer.import_id is not None and newer.import_id == older.import_id:
                    continue  # one statement listing two identical purchases keeps both (docs/finance.md#csv-import)
                found[newer.id] = older.id
                break
    return found


class _Facts:
    """Everything the register joins, read in one short unit of work."""

    def __init__(self, context: TenantContext, *, start: date | None, end: date | None) -> None:
        with open_link_stores(context) as (finance, sms, links):
            self.accounts = {a.id: a for a in finance.list_accounts(context)}
            self.imports = {}
            offset = 0
            while True:
                page, total = finance.list_imports(context, limit=MAX_PAGE, offset=offset)
                self.imports.update({i.id: i for i in page})
                offset += len(page)
                if not page or offset >= total or offset >= 5_000:
                    break
            window_start = (start - timedelta(days=DUPLICATE_DAYS)) if start else None
            window_end = (end + timedelta(days=DUPLICATE_DAYS)) if end else None
            entries = sms.list_entries(context, start=window_start, end=window_end)
            self.entries = _active_entries(entries)
            self.sources = sms.entry_sources(context, sorted({e.id for e in self.entries.values()}))
            rows, _ = links.list_links(context, canonical_id=None, dimension=None, target_type=None, target_id=None, status=None,
                                       limit=MAX_LINKS, offset=0)
            self.links: dict[str, list[Any]] = defaultdict(list)
            for link in rows:
                self.links[link.canonical_id].append(link)
            self.ledger_kind = sms_settings_for(sms, context).ledger_kind.value
            _messages, self.review_count = sms.list_messages(context, status=MessageStatus.REVIEW.value, limit=1, offset=0)
            self.window = finance.transactions_between(context, window_start or date(1970, 1, 1), window_end or date(9999, 12, 31))
            self.sums = finance.account_sums(context)
            self.settings = settings_for(finance, context)
            self.finance = finance

    def canon(self, txn_id: str) -> str | None:
        entry = self.entries.get(txn_id)
        return entry.canonical_id or entry.id if entry else None


def _row(txn: Transaction, facts: _Facts, duplicates: dict[str, str], running: Decimal | None) -> RegisterRow:
    entry = facts.entries.get(txn.id)
    canonical = facts.canon(txn.id)
    kinds = sorted({source_kind(m.source) for m in facts.sources.get(entry.id, [])}) if entry else []
    record = facts.imports.get(txn.import_id) if txn.import_id else None
    statement_format: Literal["csv", "pdf"] | None = None
    if txn.import_id:
        statement_format = "pdf" if record is not None and record.preset.startswith("pdf") else "csv"
        if "statement" not in kinds:
            kinds.append("statement")
    elif entry is not None and entry.source == EntrySource.STATEMENT and "statement" not in kinds:
        kinds.append("statement")
    if "sms" in kinds:
        origin: Origin = "sms"
    elif "wallet" in kinds:
        origin = "apple_pay"
    elif "statement" in kinds:
        origin = "statement"
    else:
        origin = "manual"
    live = [link for link in facts.links.get(canonical or "", []) if link.status != "void"]
    receipts = sum(1 for link in live if link.dimension == "document" and link.role == "evidence")
    review_links = sum(1 for link in live if link.status == "needs_review")
    reconciled = bool(entry is not None and (entry.reconciled_at is not None or len(set(kinds)) > 1))
    evidence: Evidence = "linked" if receipts else ("missing" if txn.amount < 0 and txn.category not in (Category.TRANSFER,) else "not_needed")
    duplicate_of = duplicates.get(txn.id)
    if duplicate_of:
        row_status: RowStatus = "duplicate_candidate"
    elif review_links:
        row_status = "needs_review"
    elif txn.category_source == CategorySource.NONE:
        row_status = "uncategorized"
    elif receipts:
        row_status = "linked"
    else:
        row_status = "recorded"
    return RegisterRow(**_view(txn), canonical_id=canonical, origin=origin, statement_format=statement_format, sources=sorted(set(kinds)),
                       reconciled=reconciled, receipts=receipts, evidence=evidence, links_need_review=review_links, duplicate_of=duplicate_of,
                       status=row_status, running_balance=running)


def _running(facts: _Facts, context: TenantContext, account: Account) -> dict[str, Decimal]:
    """Each row's balance after it, oldest first in the register's own order."""
    rows, _ = facts.finance.list_transactions(context, TransactionFilter(account_id=account.id), limit=MAX_SUMMARY_ROWS, offset=0)
    balance = account.opening_balance
    out: dict[str, Decimal] = {}
    for txn in sorted(rows, key=lambda t: (t.posted_on, t.created_at, t.id)):
        balance += txn.amount
        out[txn.id] = quantize(balance, account.currency)
    return out


def _balances(facts: _Facts, later: list[Transaction], account_id: str | None, start: date | None, end: date | None,
              today: date) -> list[RegisterBalance]:
    """Beginning (before ``start``) and ending (through ``end``, else today) per currency, for one account or every
    live account. ``later`` holds every row dated on or after ``start`` (or after ``end`` when there is no start)."""
    chosen = [a for a in facts.accounts.values() if (a.id == account_id if account_id else not a.archived)]
    ids = {a.id for a in chosen}
    now: dict[str, Decimal] = defaultdict(Decimal)
    opening: dict[str, Decimal] = defaultdict(Decimal)
    for account in chosen:
        now[account.currency] += account.opening_balance + facts.sums.get(account.id, Decimal(0))
        opening[account.currency] += account.opening_balance
    last = end or today
    begin = dict(now) if start is not None else dict(opening)
    finish = dict(now)
    for txn in later:
        if txn.account_id not in ids:
            continue
        if txn.posted_on > last:
            finish[txn.currency] = finish.get(txn.currency, Decimal(0)) - txn.amount
        if start is not None and txn.posted_on >= start:
            begin[txn.currency] = begin.get(txn.currency, Decimal(0)) - txn.amount
    return [RegisterBalance(currency=c, beginning=quantize(begin.get(c, Decimal(0)), c), ending=quantize(finish.get(c, Decimal(0)), c))
            for c in sorted(now)]


def _per_currency(values: dict[str, Decimal]) -> list[SourceCounts]:
    return [SourceCounts(currency=c, amount=quantize(v, c)) for c, v in sorted(values.items())]


@router.get("/register", response_model=RegisterPage)
def register(
    q: str | None = Query(default=None, max_length=200),
    account_id: str | None = Query(default=None, max_length=80),
    category: Category | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    direction: Literal["in", "out"] | None = None,
    origin: Origin | None = None,
    evidence: Literal["linked", "missing"] | None = None,
    row_status: RowStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> RegisterPage:
    """Transactions with where they came from, their receipt and statement evidence and a duplicate signal,
    filtered and paged on the server. KPI totals cover every matching row, not just the page."""
    _read(context)
    flt = _filter(q, account_id, category, start, end, direction)
    facts = _Facts(context, start=start, end=end)
    with open_link_stores(context) as (finance, _sms, _links):
        matched, total_matched = finance.list_transactions(context, flt, limit=SCAN_LIMIT, offset=0)
        facts.finance = finance
        account = facts.accounts.get(account_id) if account_id else None
        running = _running(facts, context, account) if account is not None else {}
        boundary = start or ((end + timedelta(days=1)) if end else None)
        later = finance.transactions_between(context, boundary, date(9999, 12, 31)) if boundary else []
    canon = {t.id: facts.canon(t.id) for t in facts.window}
    canon.update({t.id: facts.canon(t.id) for t in matched})
    pool = {t.id: t for t in facts.window}
    pool.update({t.id: t for t in matched})
    duplicates = _duplicates(list(pool.values()), canon)
    rows = [_row(t, facts, duplicates, running.get(t.id)) for t in matched]
    if origin:
        rows = [r for r in rows if r.origin == origin]
    if evidence:
        rows = [r for r in rows if r.evidence == evidence]
    if row_status:
        rows = [r for r in rows if r.status == row_status]
    inflow: dict[str, Decimal] = defaultdict(Decimal)
    outflow: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        (inflow if r.amount > 0 else outflow)[r.currency] += r.amount
    totals = RegisterTotals(
        count=len(rows), categorized=sum(1 for r in rows if r.category_source != CategorySource.NONE),
        receipts_missing=sum(1 for r in rows if r.evidence == "missing"), needs_review=sum(1 for r in rows if r.status == "needs_review"),
        duplicate_candidates=sum(1 for r in rows if r.status == "duplicate_candidate"), matched=sum(1 for r in rows if r.reconciled),
        inflow=_per_currency(inflow), outflow=_per_currency({c: -v for c, v in outflow.items()}),
    )
    today = today_in(facts.settings)
    return RegisterPage(items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset, truncated=total_matched > SCAN_LIMIT,
                        totals=totals, balances=_balances(facts, later, account_id, start, end, today), review_count=facts.review_count,
                        ledger_kind=facts.ledger_kind)  # type: ignore[arg-type]


def _source_view(message: SmsMessage, now: datetime) -> SourceView:
    kept = bool(message.body_masked) and (message.body_purge_at is None or message.body_purge_at > now)
    return SourceView(kind=source_kind(message.source), status=message.status.value, received_at=message.received_at,
                      sender=message.sender if source_kind(message.source) == "sms" else None,
                      text=message.body_masked if kept else None, kept_until=message.body_purge_at if kept else None)


@router.get("/register/{transaction_id}", response_model=RegisterDetail)
def register_detail(transaction_id: str, context: TenantContext = Depends(tenant_context)) -> RegisterDetail:
    """One transaction for the details drawer: its sources (with the masked SMS while it is kept), receipts,
    the rule-based category suggestion and the record it may duplicate."""
    _read(context)
    with open_link_stores(context) as (finance, _sms, _links):
        txn = finance.get_transaction(context, transaction_id)
    if txn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found. It may have been deleted.")
    facts = _Facts(context, start=txn.posted_on, end=txn.posted_on)
    canon = {t.id: facts.canon(t.id) for t in facts.window}
    duplicates = _duplicates(list({t.id: t for t in [*facts.window, txn]}.values()), canon)
    # The older record of a pair names the newer one as its match too.
    twins = {older: newer for newer, older in duplicates.items()}
    row = _row(txn, facts, duplicates, None)
    entry = facts.entries.get(txn.id)
    now = utc_now()
    sources = [_source_view(m, now) for m in facts.sources.get(entry.id, [])] if entry else []
    match_id = duplicates.get(txn.id) or twins.get(txn.id)
    match = next((t for t in facts.window if t.id == match_id), None) if match_id else None
    suggested, by = categorize(txn.description, txn.amount, facts.settings.category_rules)
    canonical = row.canonical_id or ""
    receipts = [link.target_id for link in facts.links.get(canonical, [])
                if link.status != "void" and link.dimension == "document" and link.role == "evidence"]
    account = facts.accounts.get(txn.account_id)
    return RegisterDetail(row=row, account_name=account.name if account else "", kind=entry.kind if entry else None,
                          merchant=entry.merchant if entry else None, reference=entry.reference if entry else None,
                          suggested_category=suggested, suggested_by=by, sources=sources, receipt_document_ids=receipts,
                          duplicate=_row(match, facts, duplicates, None) if match else None)


# -- accounts overview ------------------------------------------------------------------------


class PaymentDue(BaseModel):
    name: str
    due_on: date
    amount: Decimal
    currency: str


class AccountOverview(BaseModel):
    account_id: str
    sources: list[Literal["manual", "sms", "apple_wallet", "statement"]]
    credit_limit: Decimal | None
    observed_balance: Decimal | None
    observed_at: datetime | None
    difference: Decimal | None
    last_activity_on: date | None
    last_import_at: datetime | None
    last_reconciled_at: datetime | None
    imported: int
    matched: int
    needs_attention: list[Literal["balance_differs", "links_need_review", "possible_duplicates"]]
    payment_due: PaymentDue | None


class AccountsOverview(BaseModel):
    accounts: list[AccountOverview]
    review_count: int
    last_reconciled_at: datetime | None
    base_currency: str
    ledger_kind: Literal["personal", "business"]


@router.get("/accounts/overview", response_model=AccountsOverview)
def accounts_overview(context: TenantContext = Depends(tenant_context)) -> AccountsOverview:
    """Per account: the sources that feed it, the bank's last stated balance against the ledger, the last
    statement and reconciliation, what needs attention, and a card's limit and next payment due."""
    _read(context)
    facts = _Facts(context, start=None, end=None)
    with open_link_stores(context) as (finance, sms, _links):
        settings = sms_settings_for(sms, context)
        observed = sms.latest_observations(context)
        bills = upcoming_bills([b for b in finance.list_items(context, "bill") if getattr(b, "active", True)], today_in(facts.settings), days=60)
        bill_accounts = {b.id: getattr(b, "account_id", None) for b in finance.list_items(context, "bill")}
    limits = {m.account_id: m.credit_limit for m in settings.account_map if m.credit_limit}
    mapped_sms = {m.account_id for m in settings.account_map} | ({settings.default_account_id} if settings.default_account_id else set())
    mapped_wallet = {c.account_id for c in settings.wallet_cards}
    canon = {t.id: facts.canon(t.id) for t in facts.window}
    duplicates = _duplicates(list(facts.window), canon)
    per: dict[str, dict[str, Any]] = defaultdict(lambda: {"sources": set(), "last": None, "imported": 0, "matched": 0, "review": 0, "dups": 0,
                                                           "reconciled": None})
    for txn in facts.window:
        row = _row(txn, facts, duplicates, None)
        item = per[txn.account_id]
        item["last"] = max(item["last"] or txn.posted_on, txn.posted_on)
        item["sources"].update({"apple_wallet" if s == "wallet" else s for s in row.sources} or {"manual"})
        # A statement line either created this row or was reconciled onto an SMS or Apple Pay record of it
        # (a statement source on the entry, no row of its own): both are imported lines.
        if "statement" in row.sources:
            item["imported"] += 1
            item["matched"] += 1 if row.reconciled else 0
        item["review"] += row.links_need_review
        item["dups"] += 1 if row.duplicate_of else 0
        entry = facts.entries.get(txn.id)
        if entry is not None and entry.reconciled_at is not None:
            item["reconciled"] = max(item["reconciled"] or entry.reconciled_at, entry.reconciled_at)
    last_import: dict[str, datetime] = {}
    for record in facts.imports.values():
        last_import[record.account_id] = max(last_import.get(record.account_id, record.created_at), record.created_at)
    out: list[AccountOverview] = []
    for account in facts.accounts.values():
        item = per[account.id]
        sources = set(item["sources"])
        if account.id in mapped_sms:
            sources.add("sms")
        if account.id in mapped_wallet:
            sources.add("apple_wallet")
        if account.id in last_import:
            sources.add("statement")
        if not sources:
            sources.add("manual")
        balance = quantize(account.opening_balance + facts.sums.get(account.id, Decimal(0)), account.currency)
        seen = observed.get(account.id)
        difference = quantize(seen[0] - balance, account.currency) if seen is not None else None
        attention: list[Any] = []
        if difference is not None and difference != 0:
            attention.append("balance_differs")
        if item["review"]:
            attention.append("links_need_review")
        if item["dups"]:
            attention.append("possible_duplicates")
        due = next((b for b in bills if bill_accounts.get(b.id) == account.id), None) if account.kind == AccountKind.CARD else None
        reconciled = item["reconciled"]
        out.append(AccountOverview(
            account_id=account.id, sources=sorted(sources), credit_limit=limits.get(account.id), observed_balance=seen[0] if seen else None,
            observed_at=seen[1] if seen else None, difference=difference, last_activity_on=item["last"], last_import_at=last_import.get(account.id),
            last_reconciled_at=reconciled, imported=item["imported"], matched=item["matched"], needs_attention=attention,
            payment_due=PaymentDue(name=due.name, due_on=due.due_on, amount=due.amount, currency=due.currency) if due else None,
        ))
    stamps = [a.last_reconciled_at for a in out if a.last_reconciled_at]
    return AccountsOverview(accounts=out, review_count=facts.review_count, last_reconciled_at=max(stamps) if stamps else None,
                            base_currency=facts.settings.base_currency, ledger_kind=facts.ledger_kind)  # type: ignore[arg-type]


class ActivityDay(BaseModel):
    day: date
    inflow: Decimal
    outflow: Decimal


class AccountActivity(BaseModel):
    account_id: str
    currency: str
    days: list[ActivityDay]


@router.get("/accounts/{account_id}/activity", response_model=AccountActivity)
def account_activity(
    account_id: str,
    days: int = Query(default=14, description="7, 14, 30 or 90"),
    context: TenantContext = Depends(tenant_context),
) -> AccountActivity:
    """Money in and out of one account per day over the last ``days`` days (today included), in its currency."""
    _read(context)
    if days not in (7, 14, 30, 90):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose 7, 14, 30 or 90 days.")
    with open_link_stores(context) as (finance, _sms, _links):
        account = finance.get_account(context, account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found. It may have been deleted.")
        today = today_in(settings_for(finance, context))
        first = today - timedelta(days=days - 1)
        rows, _ = finance.list_transactions(context, TransactionFilter(account_id=account_id, start=first, end=today), limit=MAX_SUMMARY_ROWS, offset=0)
    inflow: dict[date, Decimal] = defaultdict(Decimal)
    outflow: dict[date, Decimal] = defaultdict(Decimal)
    for txn in rows:
        (inflow if txn.amount > 0 else outflow)[txn.posted_on] += abs(txn.amount)
    span = [first + timedelta(days=i) for i in range(days)]
    return AccountActivity(account_id=account_id, currency=account.currency,
                           days=[ActivityDay(day=d, inflow=quantize(inflow[d], account.currency), outflow=quantize(outflow[d], account.currency)) for d in span])
