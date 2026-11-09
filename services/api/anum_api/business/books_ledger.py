"""The business journal: posting source document events, manual entries, reversals, period close and the reports read from it
(docs/business-books.md#the-journal).

**Posting run** (``post``): under a lock on the business's book settings (one run at a time per
business), read the source documents (``books_service.gather``), turn them into proposals
(``books_posting.propose``) and compare with the live journal entries by ``event_key``:

* a new event dated on or before today is posted (or, without an exchange rate, recorded as a posting
  issue with its reason: never a guessed rate);
* an event whose facts changed (a bill edited before payment, an allocation reclassified) is reversed
  and posted again; an event whose source went away (a voided bill or payment, an allocation undone in
  Finance) is reversed;
* an event dated in a closed period is posted on the first open day and marked ``late``; a reversal of
  an entry in a closed period is dated the same way. Nothing is ever written on a closed day (the
  database refuses it too).

Running it again changes nothing (idempotent). It runs after every change in the books, from
``POST /ledger/post`` (Post now) and from the daily job for every business with books, which is also the
backfill of existing businesses.

**Close** (``close``): an owner or an accountant closes every month up to a chosen month end, once
everything in it is posted (no posting issues, no draft bills, no allocations waiting for review). At
a financial year end the P&L accounts are closed to retained earnings by a closing entry. **Reopen**
(owners only, with a reason) reverses that closing entry and unlocks the months; both are audited.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any

from ..finance.money import quantize
from ..schemas import TenantContext, new_id, utc_now
from . import books_ledger_report as LR
from . import books_report as R
from .books_chart import accounts as chart_accounts
from .books_chart import ensure_chart
from .books_models import PNL_TYPES, AccountType, JournalEntry, JournalLine, LedgerAccount, PeriodClose, PostingIssue
from .books_posting import Chart, Issue, Line, Proposal, propose, realise
from .books_store import MAX_SCAN, AtMost, Between, BooksStore, DuplicateError, open_books_store

logger = logging.getLogger("anum_api.business.books_ledger")

AUTO_KINDS = frozenset({"invoice", "credit_note", "invoice_payment", "bill", "bill_payment", "allocation", "adjustment", "intercompany"})
SYSTEM_POSTER = "system:books"
_process_locks: dict[tuple[str, str], RLock] = {}
_guard = RLock()


class LedgerError(Exception):
    def __init__(self, status_code: int, message: str, code: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.extra = extra


@contextmanager
def _one_run(context: TenantContext) -> Iterator[None]:
    """One posting run or close per business in this process (the database lock covers other processes)."""
    with _guard:
        lock = _process_locks.setdefault((context.tenant_id, context.workspace_id), RLock())
    with lock:
        yield


def locked_through(store: BooksStore) -> date | None:
    rows, _ = store.find("closes", where={"status": "closed"}, order_by="period_end", limit=1)
    return rows[0].period_end if rows else None


def month_end(day: date) -> date:
    return R.add_months(R.month_start(day), 1) - timedelta(days=1)


# -- posting -------------------------------------------------------------------------------------------------


@dataclass
class PostResult:
    posted: int = 0
    reversed: int = 0
    late: int = 0
    issues: int = 0
    scheduled: int = 0
    unchanged: int = 0
    truncated: bool = False
    skipped: str | None = None
    issue_rows: list[dict[str, Any]] = field(default_factory=list)


def _live_entries(store: BooksStore) -> list[JournalEntry]:
    rows, _ = store.find("journal_entries", where={"status": "posted", "reverses_id": None, "source_kind": set(AUTO_KINDS)}, order_by="entry_date",
                         descending=False, limit=MAX_SCAN * 20)
    return rows


def _entry_rows(context: TenantContext, *, entry_date: date, event_date: date, source_kind: str, source_id: str | None, event_key: str,
                fingerprint: str, description: str, lines: list[Line], actor: str, now: datetime,
                reverses_id: str | None = None) -> tuple[JournalEntry, list[JournalLine]]:
    entry = JournalEntry(id=new_id("bje"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, entry_date=entry_date,
                         event_date=event_date, source_kind=source_kind, source_id=source_id, event_key=event_key, fingerprint=fingerprint,
                         description=description[:300], reverses_id=reverses_id, late=entry_date > event_date, created_by=actor)
    rows = [JournalLine(id=new_id("bjl"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, entry_id=entry.id,
                        line_no=index + 1, entry_date=entry_date, source_kind=source_kind, **asdict(line))
            for index, line in enumerate(lines)]
    return entry, rows


def _open_day(day: date, locked: date | None) -> date:
    return day if locked is None or day > locked else locked + timedelta(days=1)


def reverse(store: BooksStore, context: TenantContext, entry: JournalEntry, *, actor: str, now: datetime, locked: date | None,
            description: str | None = None) -> JournalEntry:
    """Post the mirror of ``entry`` (debits and credits swapped) and mark it reversed. Dated on the entry's day when that day is
    open, else on the first open day."""
    lines, _ = store.find("journal_lines", where={"entry_id": entry.id}, order_by="line_no", descending=False, limit=500)
    mirrored = [Line(line.account_id, line.credit, line.debit, line.currency, -line.amount, line.rate, line.rate_date, line.rate_source, line.category,
                     line.project_id, line.cost_centre_id, line.customer_id, line.counterparty_workspace_id, line.pair_id, line.flow) for line in lines]
    day = _open_day(entry.entry_date, locked)
    reversal, rows = _entry_rows(context, entry_date=day, event_date=entry.entry_date, source_kind=entry.source_kind, source_id=entry.source_id,
                                 event_key=f"reversal:{entry.id}", fingerprint=entry.fingerprint,
                                 description=description or f"Reversal: {entry.description}", lines=mirrored, actor=actor, now=now,
                                 reverses_id=entry.id)
    store.add_entry(reversal, rows)
    if not store.save("journal_entries", entry.model_copy(update={"status": "reversed", "reversed_by_id": reversal.id})):
        raise LedgerError(409, "This entry changed while it was being reversed. Reload and try again.", "stale")
    return reversal


def _plan(context: TenantContext, store: BooksStore, today: date) -> tuple[Any, Chart, list[Proposal], list[JournalEntry], date | None]:
    from .books_service import gather

    inputs = gather(context)
    chart = Chart.of(chart_accounts(store))
    return inputs, chart, propose(inputs, chart), _live_entries(store), locked_through(store)


def post(context: TenantContext, *, actor: str = SYSTEM_POSTER, today: date | None = None, now: datetime | None = None,
         dry_run: bool = False) -> PostResult:
    """Bring the journal in line with the source documents (see the module docstring). ``dry_run`` writes nothing and says what
    a run would do (the reports' "not posted yet")."""
    moment = now or utc_now()
    day = today or moment.date()
    result = PostResult()
    with _one_run(context), open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None:
            result.skipped = "books_not_set_up"
            return result
        if not dry_run:
            store.lock_books()
            ensure_chart(store, context, moment, actor)
        inputs, chart, proposals, live, locked = _plan(context, store, day)
        result.truncated = inputs.truncated
        if dry_run and not chart.by_key:
            due = [p for p in proposals if p.event_date <= day]
            result.posted = len(due)
            result.scheduled = len(proposals) - len(due)
            return result
        converter = R.Converter(inputs.rates, settings.base_currency, settings.rate_max_age_days)
        due = {p.event_key: p for p in proposals if p.event_date <= day}
        result.scheduled = len(proposals) - len(due)
        live_by_key = {entry.event_key: entry for entry in live}
        issues: list[Issue] = []
        to_post: list[tuple[Proposal, list[Line]]] = []
        to_reverse: list[JournalEntry] = []
        for entry in live:
            proposal = due.get(entry.event_key)
            if proposal is None:
                if not inputs.truncated:  # a record not read (too many to read at once) is not a record that went away
                    to_reverse.append(entry)
            elif proposal.fingerprint != entry.fingerprint:
                to_reverse.append(entry)
        changed = {entry.event_key for entry in to_reverse}
        for key, proposal in due.items():
            if key in live_by_key and key not in changed:
                result.unchanged += 1
                continue
            realised = realise(proposal, converter, chart)
            if isinstance(realised, Issue):
                issues.append(realised)
            else:
                to_post.append((proposal, realised))
        result.issues = len(issues)
        result.issue_rows = [_issue_view(issue) for issue in sorted(issues, key=lambda item: (item.event_date, item.event_key))[:50]]
        result.reversed = len(to_reverse)
        result.posted = len(to_post)
        result.late = sum(1 for proposal, _ in to_post if locked is not None and proposal.event_date <= locked)
        if dry_run:
            return result
        for entry in to_reverse:
            reverse(store, context, entry, actor=actor, now=moment, locked=locked)
        for proposal, lines in to_post:
            entry, rows = _entry_rows(context, entry_date=_open_day(proposal.event_date, locked), event_date=proposal.event_date,
                                      source_kind=proposal.source_kind, source_id=proposal.source_id, event_key=proposal.event_key,
                                      fingerprint=proposal.fingerprint, description=proposal.description, lines=lines, actor=actor, now=moment)
            try:
                store.add_entry(entry, rows)
            except DuplicateError:
                continue  # posted by another run in the meantime
        _replace_issues(store, context, issues, moment)
    return result


def _issue_view(issue: Issue) -> dict[str, Any]:
    return {"event_key": issue.event_key, "source_kind": issue.source_kind, "source_id": issue.source_id, "event_date": issue.event_date.isoformat(),
            "reason": issue.reason, "currency": issue.currency, "rate_date": issue.rate_date.isoformat() if issue.rate_date else None,
            "description": issue.description}


def _replace_issues(store: BooksStore, context: TenantContext, issues: list[Issue], now: datetime) -> None:
    current, _ = store.find("posting_issues", limit=MAX_SCAN)
    wanted = {issue.event_key: issue for issue in issues}
    for row in current:
        found = wanted.get(row.event_key)
        if found is None or (found.reason, found.currency, found.rate_date) != (row.reason, row.currency, row.rate_date):
            store.delete("posting_issues", row.id)
        else:
            wanted.pop(row.event_key)
    for issue in wanted.values():
        try:
            store.add("posting_issues", PostingIssue(id=new_id("bpi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now,
                                                     event_key=issue.event_key, source_kind=issue.source_kind, source_id=issue.source_id,
                                                     event_date=issue.event_date, reason=issue.reason, currency=issue.currency,
                                                     rate_date=issue.rate_date, description=issue.description[:300]))
        except DuplicateError:
            continue


def post_quietly(context: TenantContext, *, actor: str = SYSTEM_POSTER) -> None:
    """After a change in the books: post it now. A failure is logged and left to Post now and the daily run (the reports say what
    is not posted yet), never the caller's error."""
    try:
        post(context, actor=actor)
    except Exception:  # noqa: BLE001 - posting is retried by the daily run; the change itself was saved
        logger.exception("business books posting run failed", extra={"anum_workspace_id": _log_safe(context.workspace_id)})


def _log_safe(value: str) -> str:
    """Identifiers can come from request headers in local mode: no forged log lines."""
    return value.replace("\r", "").replace("\n", "")


# -- manual entries and reversals -------------------------------------------------------------------------------


def manual_entry(store: BooksStore, context: TenantContext, *, entry_date: date, description: str, lines: list[dict[str, Any]], today: date,
                 now: datetime, event_key: str | None = None) -> JournalEntry:
    """A journal entry an owner or an accountant types (opening balances, a correction): base currency, balanced, two lines or more,
    on an open day not in the future."""
    settings = store.get_settings()
    if settings is None:
        raise LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
    store.lock_books()
    ensure_chart(store, context, now, context.user_id)
    if entry_date > today:
        raise LedgerError(422, "A journal entry cannot be dated in the future.", "date_in_future")
    locked = locked_through(store)
    if locked is not None and entry_date <= locked:
        raise LedgerError(409, f"That day is in a closed period (closed through {locked.isoformat()}). Reopen it first.", "period_locked",
                          locked_through=locked.isoformat())
    base = settings.base_currency
    known = {account.id: account for account in chart_accounts(store)}
    rows: list[Line] = []
    for index, item in enumerate(lines):
        account = known.get(item.get("account_id") or "")
        if account is None:
            raise LedgerError(404, f"Line {index + 1}: account not found.", "account_not_found", line=index + 1)
        if account.archived:
            raise LedgerError(409, f"Line {index + 1}: that account is archived.", "account_archived", line=index + 1)
        debit, credit = item.get("debit") or Decimal(0), item.get("credit") or Decimal(0)
        if (debit > 0) == (credit > 0):
            raise LedgerError(422, f"Line {index + 1}: enter a debit or a credit, not both.", "invalid_line", line=index + 1)
        amount = debit if debit > 0 else -credit
        rows.append(Line(account.id, debit, credit, base, amount, None, None, None, category=None, project_id=item.get("project_id"),
                         cost_centre_id=item.get("cost_centre_id"), flow="other" if account.subtype == "bank" else None))
    total_debit = sum((row.debit for row in rows), Decimal(0))
    total_credit = sum((row.credit for row in rows), Decimal(0))
    if len(rows) < 2 or total_debit != total_credit:
        raise LedgerError(422, f"Debits ({quantize(total_debit, base)}) and credits ({quantize(total_credit, base)}) must be equal, on two lines or more.",
                          "unbalanced", debit=str(quantize(total_debit, base)), credit=str(quantize(total_credit, base)))
    entry_id = new_id("bjm")
    entry, line_rows = _entry_rows(context, entry_date=entry_date, event_date=entry_date, source_kind="manual", source_id=entry_id,
                                   event_key=event_key or f"manual:{entry_id}", fingerprint=entry_id, description=description, lines=rows,
                                   actor=context.user_id, now=now)
    store.add_entry(entry, line_rows)
    return entry


def reverse_manual(store: BooksStore, context: TenantContext, entry_id: str, *, reason: str, now: datetime) -> JournalEntry:
    """Reverse a manual entry. Entries posted from a source document change only through that document (void the bill, the
    payment or the adjustment), so the journal never disagrees with the records."""
    store.lock_books()
    entry = store.get("journal_entries", entry_id, for_update=True)
    if entry is None:
        raise LedgerError(404, "Journal entry not found.", "entry_not_found")
    if entry.source_kind != "manual" or entry.reverses_id is not None:
        raise LedgerError(409, "This entry comes from a source document. Change or void the document instead; the journal follows it.",
                          "not_manual")
    if entry.status != "posted":
        raise LedgerError(409, "This entry is already reversed.", "already_reversed")
    return reverse(store, context, entry, actor=context.user_id, now=now, locked=locked_through(store), description=f"Reversed: {reason}")


# -- reading the journal ----------------------------------------------------------------------------------------------


def _totals(store: BooksStore, where: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal]]:
    return {row["account_id"]: (row["debit"], row["credit"]) for row in store.ledger_totals(where, ("account_id",))}


def _between(start: date | None, end: date) -> Any:
    return Between(start, end) if start is not None else AtMost(end)


AUTO_AND_MANUAL = set(AUTO_KINDS) | {"manual"}


@dataclass
class Reader:
    """One business's journal, read in one unit of work."""

    store: BooksStore
    base: str
    accounts: list[LedgerAccount]

    def totals(self, end: date, start: date | None = None, *, closing: bool | None = None) -> dict[str, tuple[Decimal, Decimal]]:
        """Sums per account of lines dated ``start``..``end`` (every line up to ``end`` without ``start``). ``closing``: only closing
        entries (True), none of them (False), or both (None)."""
        where: dict[str, Any] = {"entry_date": _between(start, end)}
        if closing is True:
            where["source_kind"] = "closing"
        elif closing is False:
            where["source_kind"] = AUTO_AND_MANUAL
        return _totals(self.store, where)


def reader(store: BooksStore) -> Reader:
    settings = store.get_settings()
    if settings is None:
        raise LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
    return Reader(store, settings.base_currency, chart_accounts(store))


def status(context: TenantContext, today: date) -> dict[str, Any]:
    """What is not in the journal yet: posting issues (with their reasons) and changes a posting run would make."""
    plan = post(context, dry_run=True, today=today)
    return {"to_post": plan.posted, "to_reverse": plan.reversed, "issues": plan.issues, "issue_rows": plan.issue_rows, "scheduled": plan.scheduled,
            "truncated": plan.truncated, "complete": plan.posted == 0 and plan.reversed == 0 and plan.issues == 0 and not plan.truncated}


def statements(context: TenantContext, start: date, end: date, *, today: date, compare: bool = False) -> dict[str, Any]:
    """The trial balance, balance sheet, P&L (with its reconciliation to the derived P&L), changes in equity and cash flow for one
    period, from the journal. ``complete`` is false while anything is not posted."""
    from .books_service import gather

    pending = status(context, today)
    with open_books_store(context) as store:
        read = reader(store)
        accounts = read.accounts
        before = start - timedelta(days=1)
        opening = read.totals(before)
        period_all = read.totals(end, start)
        period = read.totals(end, start, closing=False)
        closing_moves = read.totals(end, start, closing=True)
        cumulative = read.totals(end)
        bank_ids = {account.id for account in accounts if account.subtype == "bank"}
        movements = [row for row in store.ledger_totals({"entry_date": Between(start, end), "account_id": bank_ids}, ("flow", "currency"))] if bank_ids else []
        cash_open = {row["currency"]: row["amount"] for row in store.ledger_totals({"entry_date": AtMost(before), "account_id": bank_ids}, ("currency",))} if bank_ids else {}
        cash_close = {row["currency"]: row["amount"] for row in store.ledger_totals({"entry_date": AtMost(end), "account_id": bank_ids}, ("currency",))} if bank_ids else {}
        cash_base = {row["account_id"]: row["debit"] - row["credit"] for row in store.ledger_totals({"entry_date": Between(start, end), "account_id": bank_ids},
                                                                                                    ("account_id",))} if bank_ids else {}
        issues, _ = store.find("posting_issues", where={"event_date": AtMost(end)}, limit=1)
        closes, _ = store.find("closes", where={"status": "closed"}, order_by="period_end", limit=1)
        comparatives: dict[str, Any] = {}
        if compare:
            for name, (first, last) in LR.comparative_periods(start, end).items():
                comparatives[name] = {"from": first.isoformat(), "to": last.isoformat(),
                                      "pnl": LR.pnl(accounts, read.totals(last, first, closing=False), read.base),
                                      "balance_sheet": LR.balance_sheet(accounts, read.totals(last), read.base)}
    base = read.base
    journal_pnl = LR.pnl(accounts, period, base)
    # The P&L derived from the records for the same days (accrual), to show that the journal agrees with it, line by line.
    inputs = gather(context, start - timedelta(days=3 * 366))
    upto = min(end, today)
    book = R.Book(inputs)
    converter = R.Converter(inputs.rates, inputs.settings.base_currency, inputs.settings.rate_max_age_days)
    derived = R.statement(R.total_up(R.entries(book, R.Basis.ACCRUAL, start, upto, converter, set()), converter)) if start <= upto else None
    late = late_entries(context, start, end)
    complete = pending["complete"] and not inputs.truncated
    notes = []
    if not pending["complete"]:
        notes.append("not_posted")
    if late:
        notes.append("late_postings")
    if derived is not None and not derived["complete"]:
        notes.append("derived_not_converted")
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "base_currency": base,
        "complete": complete,
        "pending": pending,
        "notes": notes,
        "late_entries": late,
        "closed_through": closes[0].period_end.isoformat() if closes else None,
        "trial_balance": LR.trial_balance(accounts, opening, period_all, base),
        "balance_sheet": {**LR.balance_sheet(accounts, cumulative, base), "as_of": end.isoformat(), "complete": complete and not issues},
        "pnl": journal_pnl,
        "reconciliation": {**LR.reconciliation(journal_pnl, derived, base), "to": upto.isoformat()} if derived is not None else None,
        "equity": LR.equity_statement(accounts, opening, period, closing_moves, cumulative, base),
        "cash_flow": LR.cash_flow(cash_open, cash_close, movements, {"net": sum(cash_base.values(), Decimal(0))}, base),
        "comparatives": comparatives,
    }


def late_entries(context: TenantContext, start: date, end: date) -> int:
    """Entries posted in the period for an event dated in a closed period before it."""
    with open_books_store(context) as store:
        _, total = store.find("journal_entries", where={"entry_date": Between(start, end), "late": True}, limit=1)
    return total


def general_ledger(context: TenantContext, account_id: str, start: date, end: date, *, limit: int, offset: int) -> dict[str, Any]:
    """One account: the opening balance, its lines in the period with the running balance and their source document, the closing
    balance."""
    with open_books_store(context) as store:
        read = reader(store)
        account = next((row for row in read.accounts if row.id == account_id), None)
        if account is None:
            raise LedgerError(404, "Account not found.", "account_not_found")
        before = start - timedelta(days=1)
        opening_d, opening_c = read.totals(before).get(account.id, (Decimal(0), Decimal(0)))
        closing_d, closing_c = read.totals(end).get(account.id, (Decimal(0), Decimal(0)))
        lines, total = store.find("journal_lines", where={"account_id": account.id, "entry_date": Between(start, end)}, order_by="entry_date",
                                  descending=False, limit=offset + limit, offset=0)
        entries = {row.id: row for row in store.find("journal_entries", where={"id": {line.entry_id for line in lines}}, limit=len(lines) + 1)[0]} if lines else {}
    base = read.base
    lines.sort(key=lambda line: (line.entry_date, line.created_at, line.entry_id, line.line_no))
    running = LR.natural(account, opening_d, opening_c)
    rows = []
    for index, line in enumerate(lines):
        running += LR.natural(account, line.debit, line.credit)
        if index < offset:
            continue
        entry = entries.get(line.entry_id)
        rows.append({**line_view(line, base), "description": entry.description if entry else "", "source_id": entry.source_id if entry else None,
                     "late": bool(entry and entry.late), "reversal": bool(entry and entry.reverses_id), "balance": LR.s(running, base)})
    return {"account": account_view(account), "base_currency": base, "period": {"from": start.isoformat(), "to": end.isoformat()},
            "opening": LR.s(LR.natural(account, opening_d, opening_c), base), "closing": LR.s(LR.natural(account, closing_d, closing_c), base),
            "items": rows, "total": total, "limit": limit, "offset": offset}


def line_view(line: JournalLine, base: str) -> dict[str, Any]:
    return {"id": line.id, "entry_id": line.entry_id, "line_no": line.line_no, "entry_date": line.entry_date.isoformat(), "source_kind": line.source_kind,
            "account_id": line.account_id, "debit": LR.s(line.debit, base), "credit": LR.s(line.credit, base), "currency": line.currency,
            "amount": str(quantize(line.amount, line.currency)), "rate": format(line.rate.normalize(), "f") if line.rate is not None else None,
            "rate_date": line.rate_date.isoformat() if line.rate_date else None, "rate_source": line.rate_source, "category": line.category,
            "project_id": line.project_id, "cost_centre_id": line.cost_centre_id, "flow": line.flow,
            "counterparty_workspace_id": line.counterparty_workspace_id, "pair_id": line.pair_id}


def account_view(account: LedgerAccount) -> dict[str, Any]:
    return {"id": account.id, "code": account.code, "name": account.name, "subtype": account.subtype, "type": account.type.value,
            "system_key": account.system_key, "category": account.category, "archived": account.archived, "version": account.version}


def journal(context: TenantContext, *, start: date | None, end: date | None, source_kind: str | None, limit: int, offset: int) -> dict[str, Any]:
    """Entries newest first with their lines (paged)."""
    where: dict[str, Any] = {}
    if start or end:
        where["entry_date"] = Between(start or date(1900, 1, 1), end or date(9999, 12, 31))
    if source_kind:
        where["source_kind"] = source_kind
    with open_books_store(context) as store:
        read = reader(store)
        entries, total = store.find("journal_entries", where=where, order_by="entry_date", limit=limit, offset=offset)
        ids = [entry.id for entry in entries]
        lines, _ = store.find("journal_lines", where={"entry_id": set(ids)}, order_by="line_no", descending=False, limit=len(ids) * 50 + 1) if ids else ([], 0)
    names = {account.id: account for account in read.accounts}
    by_entry: dict[str, list[JournalLine]] = defaultdict(list)
    for line in lines:
        by_entry[line.entry_id].append(line)
    items = []
    for entry in entries:
        own = sorted(by_entry.get(entry.id, []), key=lambda line: line.line_no)
        items.append({**entry_view(entry), "lines": [{**line_view(line, read.base), "account_code": names[line.account_id].code if line.account_id in names else "",
                                                      "account_name": names[line.account_id].name if line.account_id in names else "",
                                                      "account_key": names[line.account_id].system_key if line.account_id in names else None} for line in own],
                      "total": LR.s(sum((line.debit for line in own), Decimal(0)), read.base)})
    return {"items": items, "total": total, "limit": limit, "offset": offset, "base_currency": read.base}


def entry_view(entry: JournalEntry) -> dict[str, Any]:
    return {"id": entry.id, "entry_date": entry.entry_date.isoformat(), "event_date": entry.event_date.isoformat(), "source_kind": entry.source_kind,
            "source_id": entry.source_id, "description": entry.description, "status": entry.status, "reverses_id": entry.reverses_id,
            "reversed_by_id": entry.reversed_by_id, "late": entry.late, "created_by": entry.created_by, "created_at": entry.created_at.isoformat(),
            "can_reverse": entry.source_kind == "manual" and entry.status == "posted" and entry.reverses_id is None}


# -- period close -----------------------------------------------------------------------------------------------------


def fiscal_year_ends(first: date, last: date, start_month: int) -> list[date]:
    """Every last day of a financial year from ``first`` to ``last``."""
    out = []
    _, end = R.fiscal_year(first, start_month)
    while end <= last:
        out.append(end)
        end = R.add_months(end + timedelta(days=1), 12) - timedelta(days=1)
    return out


def _checklist(context: TenantContext, store: BooksStore, first: date | None, through: date) -> dict[str, Any]:
    from .books_service import allocations_by_id

    window: Any = Between(first, through) if first else AtMost(through)
    issues, issue_total = store.find("posting_issues", where={"event_date": window}, limit=20)
    drafts, draft_total = store.find("bills", where={"status": "draft", "bill_date": window}, limit=1)
    review = sum(1 for row in allocations_by_id(context).values() if row.status == "needs_review" and (first is None or row.posted_on >= first)
                 and row.posted_on <= through)
    return {"issues": issue_total, "issue_rows": [{"event_key": row.event_key, "source_kind": row.source_kind, "source_id": row.source_id,
                                                    "event_date": row.event_date.isoformat(), "reason": row.reason, "currency": row.currency,
                                                    "description": row.description} for row in issues],
            "drafts": draft_total, "allocations_in_review": review}


def close_status(context: TenantContext, today: date) -> dict[str, Any]:
    pending = status(context, today)
    with open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None:
            raise LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
        locked = locked_through(store)
        history, total = store.find("closes", order_by="period_end", limit=50)
        suggested = month_end(locked + timedelta(days=1)) if locked else month_end(R.add_months(R.month_start(today), -1))
        first = locked + timedelta(days=1) if locked else None
        checks = _checklist(context, store, first, suggested)
    can_close = suggested < today
    ready = can_close and pending["to_post"] == 0 and pending["to_reverse"] == 0 and checks["issues"] == 0 and checks["drafts"] == 0 and \
        checks["allocations_in_review"] == 0
    return {
        "closed_through": locked.isoformat() if locked else None,
        "next": {"through": suggested.isoformat(), "year_end": suggested in fiscal_year_ends(suggested.replace(day=1), suggested,
                                                                                               settings.fiscal_year_start_month),
                 "ended": can_close, "ready": ready, **checks, "to_post": pending["to_post"] + pending["to_reverse"]},
        "history": [close_view(row) for row in history], "history_total": total,
        "fiscal_year_start_month": settings.fiscal_year_start_month,
    }


def close_view(row: PeriodClose) -> dict[str, Any]:
    return {"id": row.id, "period_start": row.period_start.isoformat(), "period_end": row.period_end.isoformat(), "kind": row.kind, "status": row.status,
            "closed_by": row.closed_by, "closed_at": row.created_at.isoformat(), "closing_entry_id": row.closing_entry_id,
            "reopened_by": row.reopened_by, "reopened_at": row.reopened_at.isoformat() if row.reopened_at else None, "reopen_reason": row.reopen_reason,
            "snapshot": row.snapshot}


def _closing_entry(store: BooksStore, context: TenantContext, year_end: date, base: str, accounts: list[LedgerAccount], *, actor: str,
                   now: datetime) -> JournalEntry | None:
    """Move the P&L accounts' balances up to ``year_end`` (everything not closed before) to retained earnings."""
    totals = _totals(store, {"entry_date": AtMost(year_end)})
    retained = next(account for account in accounts if account.system_key == "retained_earnings")
    lines: list[Line] = []
    net = Decimal(0)
    for account in sorted(accounts, key=lambda row: row.code):
        if account.type not in PNL_TYPES:
            continue
        debit, credit = totals.get(account.id, (Decimal(0), Decimal(0)))
        balance = debit - credit
        if balance == 0:
            continue
        net += balance
        lines.append(Line(account.id, Decimal(0) if balance > 0 else -balance, balance if balance > 0 else Decimal(0), base, -balance, None, None, None))
    if not lines:
        return None
    lines.append(Line(retained.id, net if net > 0 else Decimal(0), Decimal(0) if net > 0 else -net, base, net, None, None, None))
    if net == 0:
        lines.pop()
    entry, rows = _entry_rows(context, entry_date=year_end, event_date=year_end, source_kind="closing", source_id=None, event_key=f"closing:{year_end.isoformat()}",
                              fingerprint=f"closing:{year_end.isoformat()}", description=f"Year-end close to retained earnings, {year_end.isoformat()}",
                              lines=lines, actor=actor, now=now)
    store.add_entry(entry, rows)
    return entry


def _snapshot(store: BooksStore, accounts: list[LedgerAccount], base: str, day: date) -> dict[str, Any]:
    totals = _totals(store, {"entry_date": AtMost(day)})
    balances = {account.code: LR.s(totals[account.id][0] - totals[account.id][1], base) for account in accounts if account.id in totals
                and totals[account.id][0] != totals[account.id][1]}
    debit = sum((value[0] for value in totals.values()), Decimal(0))
    return {"as_of": day.isoformat(), "currency": base, "balances": balances, "debit": LR.s(debit, base)}


def snapshot_matches(context: TenantContext, close: PeriodClose) -> bool:
    with open_books_store(context) as store:
        read = reader(store)
        return _snapshot(store, read.accounts, read.base, close.period_end) == close.snapshot


def close(context: TenantContext, through: date, *, today: date, now: datetime) -> dict[str, Any]:
    """Close every month from the day after the last closed one to ``through`` (a month end that has passed)."""
    if through != month_end(through):
        raise LedgerError(422, "Choose the last day of a month.", "not_month_end")
    if through >= today:
        raise LedgerError(422, "Only a month that has ended can be closed.", "month_not_ended")
    post(context, actor=SYSTEM_POSTER, today=today, now=now)
    with _one_run(context), open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None:
            raise LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
        store.lock_books()
        locked = locked_through(store)
        if locked is not None and through <= locked:
            raise LedgerError(409, f"The books are already closed through {locked.isoformat()}.", "already_closed", locked_through=locked.isoformat())
        first = locked + timedelta(days=1) if locked else None
        checks = _checklist(context, store, first, through)
        if checks["issues"] or checks["drafts"] or checks["allocations_in_review"]:
            raise LedgerError(409, "Finish the period first: post or fix every item, approve or void draft bills and confirm allocations in review.",
                              "period_not_ready", **checks)
        accounts = chart_accounts(store)
        base = settings.base_currency
        lines, _ = store.find("journal_lines", order_by="entry_date", descending=False, limit=1)
        earliest = first or (lines[0].entry_date if lines else R.month_start(through))
        year_ends = fiscal_year_ends(min(earliest, R.month_start(through)), through, settings.fiscal_year_start_month)
        closing_ids: dict[date, str] = {}
        for year_end in year_ends:
            if locked is not None and year_end <= locked:
                continue
            entry = _closing_entry(store, context, year_end, base, accounts, actor=context.user_id, now=now)
            if entry is not None:
                closing_ids[year_end] = entry.id
        month = R.month_start(first) if first else R.month_start(through)
        made = []
        while month <= through:
            end = month_end(month)
            row = PeriodClose(id=new_id("bclose"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, period_start=month,
                              period_end=end, kind="year" if end in year_ends else "month", closed_by=context.user_id, closing_entry_id=closing_ids.get(end),
                              snapshot=_snapshot(store, accounts, base, end))
            store.add("closes", row)
            made.append(row)
            month = R.add_months(month, 1)
    return {"closed_through": through.isoformat(), "previous": locked.isoformat() if locked else None, "closed": [close_view(row) for row in made]}


def reopen(context: TenantContext, month: date, reason: str, *, now: datetime) -> dict[str, Any]:
    """Reopen ``month`` and every later closed month (owners only, with a reason). Year-end closing entries of reopened years are
    reversed; the close rows stay as the trail."""
    with _one_run(context), open_books_store(context) as store:
        if store.get_settings() is None:
            raise LedgerError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
        store.lock_books()
        before = locked_through(store)
        target = month_end(month)
        if before is None or target > before:
            raise LedgerError(409, "That month is not closed.", "not_closed")
        # A month before the first close row is closed by it: reopening it reopens every close row from there on.
        rows, _ = store.find("closes", where={"status": "closed", "period_end": Between(target, date(9999, 12, 31))}, order_by="period_end", limit=MAX_SCAN)
        for row in rows:
            if not store.save("closes", row.model_copy(update={"status": "reopened", "reopened_by": context.user_id, "reopened_at": now,
                                                               "reopen_reason": reason})):
                raise LedgerError(409, "The period changed while it was being reopened. Reload and try again.", "stale")
        after = locked_through(store)
        for row in rows:
            if row.closing_entry_id:
                entry = store.get("journal_entries", row.closing_entry_id, for_update=True)
                if entry is not None and entry.status == "posted":
                    reverse(store, context, entry, actor=context.user_id, now=now, locked=after, description=f"Year reopened: {reason}")
    return {"closed_through": after.isoformat() if after else None, "previous": before.isoformat(), "reopened": [close_view(row) for row in rows]}


def require_open(store: BooksStore, *days: date | None, what: str = "This") -> None:
    """Refuse a change to a record dated in a closed period (the database refuses it too)."""
    locked = locked_through(store)
    if locked is None:
        return
    for day in days:
        if day is not None and day <= locked:
            raise LedgerError(409, f"{what} is dated in a closed period (closed through {locked.isoformat()}). An owner can reopen the period with a reason.",
                              "period_locked", locked_through=locked.isoformat())


__all__ = ["LedgerError", "PostResult", "close", "close_status", "general_ledger", "journal", "locked_through", "manual_entry", "post", "post_quietly",
           "reopen", "require_open", "reverse_manual", "statements", "status"]
