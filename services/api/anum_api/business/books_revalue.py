"""Period-end revaluation of balances in other currencies (IAS 21), docs/business-books.md#period-end-revaluation.

At a month end the monetary balances the business holds in another currency than its base currency (bank and cash,
receivables, payables, loans, intercompany balances, what it owes members, interest payable) are worth their original
amount at the **closing rate** of that day. The difference to what they are booked at (their historical value) is an
unrealised exchange gain or loss, posted on the month end to *Unrealised exchange gains and losses* (7010) and reversed
on the first day of the next month, so every settlement still realises its difference against the historical value.

* **Never a guessed rate.** The closing rate is the business's own book rate for that currency, dated on or before the
  month end and no older than the settings allow (``books_report.Converter``). Without one, that currency is not revalued:
  a posting issue ``missing_closing_rate`` names it and the day, the close of that month waits for it, and every other
  currency is revalued as usual.
* **Idempotent.** One entry per month end and currency (``revaluation:<month end>:<currency>``, its reversal
  ``...:reverse``). Run again with the same balances and rate it changes nothing; when a balance or the rate changed, the
  old pair is reversed and a new one posted.
* It runs for every month a close covers (``books_ledger.close``) and on demand (``POST /ledger/revaluation``). Never on a
  closed day.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from ..schemas import TenantContext, new_id
from . import books_report as R
from .books_chart import accounts as chart_accounts
from .books_chart import ensure_chart
from .books_models import BookSettings, JournalEntry, LedgerAccount, PostingIssue
from .books_posting import Line
from .books_store import MAX_SCAN, AtMost, BooksStore, DuplicateError, Not, open_books_store

ZERO = Decimal(0)
MONETARY = ("bank", "receivable", "payable", "loan", "owed_to_members", "interest_payable")
RATE_SCAN = 20_000


@dataclass
class Balance:
    account: LedgerAccount
    currency: str
    original: Decimal  # debit positive, in ``currency``
    booked: Decimal  # debit positive, in the base currency


def _key(period_end: date, currency: str) -> str:
    return f"revaluation:{period_end.isoformat()}:{currency}"


def balances(store: BooksStore, base: str, period_end: date) -> list[Balance]:
    """Monetary balances in other currencies on ``period_end``, with what they are booked at."""
    accounts = {row.id: row for row in chart_accounts(store) if row.subtype in MONETARY}
    if not accounts:
        return []
    rows = store.ledger_totals({"entry_date": AtMost(period_end), "account_id": set(accounts), "currency": Not(base)}, ("account_id", "currency"))
    out = [Balance(accounts[row["account_id"]], row["currency"], row["amount"], row["debit"] - row["credit"]) for row in rows
           if row["account_id"] in accounts and (row["amount"] != 0 or row["debit"] != row["credit"])]
    return sorted(out, key=lambda item: (item.currency, item.account.code))


def plan(store: BooksStore, settings: BookSettings, period_end: date) -> list[dict[str, Any]]:
    """Per currency: the closing rate (or why there is none) and each account's revaluation, without writing anything."""
    base = settings.base_currency
    rates, _ = store.find("rates", order_by="as_of", limit=RATE_SCAN)
    converter = R.Converter(rates, base, settings.rate_max_age_days)
    by_currency: dict[str, list[Balance]] = {}
    for item in balances(store, base, period_end):
        by_currency.setdefault(item.currency, []).append(item)
    out = []
    for currency, items in sorted(by_currency.items()):
        path = converter.path(currency, period_end)
        if path is None:
            out.append({"currency": currency, "status": "missing_rate", "rate": None, "rate_date": None, "source": None, "difference": ZERO,
                        "accounts": [{"account": item.account, "original": item.original, "booked": item.booked, "closing": None, "difference": None}
                                     for item in items]})
            continue
        rows = []
        for item in items:
            closing = quantize(item.original * path.rate, base)
            rows.append({"account": item.account, "original": item.original, "booked": item.booked, "closing": closing, "difference": closing - item.booked})
        out.append({"currency": currency, "status": "ready", "rate": path.rate, "rate_date": path.as_of, "source": path.source,
                    "difference": sum((row["difference"] for row in rows), ZERO), "accounts": rows})
    return out


def _lines(rows: list[dict[str, Any]], unrealised: str, base: str) -> list[Line]:
    out: list[Line] = []
    net = ZERO
    for row in rows:
        difference = row["difference"]
        if not difference:
            continue
        net += difference
        account = row["account"]
        out.append(Line(account.id, difference if difference > 0 else ZERO, -difference if difference < 0 else ZERO, base, difference, None, None, None,
                        category="fx_revaluation"))
    if net:
        out.append(Line(unrealised, ZERO if net > 0 else -net, net if net > 0 else ZERO, base, -net, None, None, None, category="fx_revaluation"))
    return out if len(out) >= 2 else []


def _mirror(lines: list[Line]) -> list[Line]:
    return [Line(line.account_id, line.credit, line.debit, line.currency, -line.amount, None, None, None, category=line.category) for line in lines]


def _fingerprint(period_end: date, currency: str, rate: Any, lines: list[Line]) -> str:
    facts = [period_end.isoformat(), currency, str(rate)] + [f"{line.account_id}|{line.debit}|{line.credit}" for line in lines]
    return hashlib.sha256("\x1f".join(facts).encode()).hexdigest()


def run(store: BooksStore, context: TenantContext, settings: BookSettings, period_end: date, *, now: datetime, actor: str,
        locked: date | None) -> dict[str, Any]:
    """Revalue on ``period_end`` (an open month end) inside the caller's unit of work, under its books lock."""
    from .books_ledger import _entry_rows, reverse

    base = settings.base_currency
    chart = {row.system_key: row.id for row in ensure_chart(store, context, now, actor) if row.system_key}
    following = period_end + timedelta(days=1)
    planned = plan(store, settings, period_end)
    live, _ = store.find("journal_entries", where={"source_kind": "revaluation", "status": "posted", "reverses_id": None,
                                                   "event_date": AtMost(following)}, limit=MAX_SCAN)
    live_by_key = {row.event_key: row for row in live if row.event_key.startswith(f"revaluation:{period_end.isoformat()}:")}
    issues: list[PostingIssue] = []
    summary = []
    posted = reversed_count = 0
    seen: set[str] = set()
    for item in planned:
        currency = item["currency"]
        key = _key(period_end, currency)
        seen.add(key)
        current = live_by_key.get(key)
        if item["status"] == "missing_rate":
            issues.append(PostingIssue(id=new_id("bpi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, event_key=key,
                                       source_kind="revaluation", source_id=f"{period_end.isoformat()}:{currency}", event_date=period_end,
                                       reason="missing_closing_rate", currency=currency, rate_date=period_end,
                                       description=f"No closing rate for {currency} on {period_end.isoformat()}: record one to revalue it"))
            lines: list[Line] = []
        else:
            lines = _lines(item["accounts"], chart["fx_unrealised"], base)
        fingerprint = _fingerprint(period_end, currency, item["rate"], lines)
        if current is not None and current.fingerprint == fingerprint:
            summary.append(_view(item, "unchanged", base))
            continue
        if current is not None:
            reversed_count += _undo(store, context, current, live_by_key.get(f"{key}:reverse"), now=now, actor=actor, locked=locked, reverse=reverse)
        if not lines:
            summary.append(_view(item, "missing_rate" if item["status"] == "missing_rate" else "nothing_to_revalue", base))
            continue
        rate_text = format(item["rate"].normalize(), "f")
        description = f"Revaluation of {currency} balances at {rate_text} ({item['source']}, {item['rate_date'].isoformat()})"
        entry, rows = _entry_rows(context, entry_date=period_end, event_date=period_end, source_kind="revaluation",
                                  source_id=f"{period_end.isoformat()}:{currency}", event_key=key, fingerprint=fingerprint, description=description,
                                  lines=lines, actor=actor, now=now)
        back, back_rows = _entry_rows(context, entry_date=following, event_date=following, source_kind="revaluation",
                                      source_id=f"{period_end.isoformat()}:{currency}", event_key=f"{key}:reverse", fingerprint=fingerprint,
                                      description=f"Reversal of the {period_end.isoformat()} revaluation of {currency}", lines=_mirror(lines),
                                      actor=actor, now=now)
        try:
            store.add_entry(entry, rows)
            store.add_entry(back, back_rows)
        except DuplicateError:
            summary.append(_view(item, "unchanged", base))
            continue
        posted += 1
        summary.append(_view(item, "revalued", base))
    # A currency with no open balance any more: its earlier revaluation goes.
    for key, current in live_by_key.items():
        if key.endswith(":reverse") or key in seen:
            continue
        reversed_count += _undo(store, context, current, live_by_key.get(f"{key}:reverse"), now=now, actor=actor, locked=locked, reverse=reverse)
    _replace_issues(store, period_end, issues)
    return {"period_end": period_end.isoformat(), "base_currency": base, "currencies": summary, "posted": posted, "reversed": reversed_count,
            "missing": [row["currency"] for row in summary if row["status"] == "missing_rate"]}


def _undo(store: BooksStore, context: TenantContext, entry: JournalEntry, back: JournalEntry | None, *, now: datetime, actor: str, locked: date | None,
          reverse: Any) -> int:
    count = 0
    for item in (entry, back):
        if item is not None and item.status == "posted":
            reverse(store, context, item, actor=actor, now=now, locked=locked, description=f"Revaluation replaced: {item.description}")
            count += 1
    return count


def _replace_issues(store: BooksStore, period_end: date, issues: list[PostingIssue]) -> None:
    prefix = f"revaluation:{period_end.isoformat()}:"
    current, _ = store.find("posting_issues", where={"source_kind": "revaluation", "event_date": period_end}, limit=MAX_SCAN)
    wanted = {issue.event_key: issue for issue in issues}
    for row in current:
        if row.event_key.startswith(prefix) and row.event_key not in wanted:
            store.delete("posting_issues", row.id)
        elif row.event_key in wanted:
            wanted.pop(row.event_key)
    for issue in wanted.values():
        try:
            store.add("posting_issues", issue)
        except DuplicateError:
            continue


def _view(item: dict[str, Any], status: str, base: str) -> dict[str, Any]:
    return {
        "currency": item["currency"], "status": status,
        "rate": format(item["rate"].normalize(), "f") if item["rate"] is not None else None,
        "rate_date": item["rate_date"].isoformat() if item["rate_date"] else None, "source": item["source"],
        "difference": str(quantize(item["difference"], base)),
        "accounts": [{"account_id": row["account"].id, "code": row["account"].code, "name": row["account"].name, "system_key": row["account"].system_key,
                      "original": str(quantize(row["original"], item["currency"])), "booked": str(quantize(row["booked"], base)),
                      "closing": str(quantize(row["closing"], base)) if row["closing"] is not None else None,
                      "difference": str(quantize(row["difference"], base)) if row["difference"] is not None else None} for row in item["accounts"]],
    }


def preview(context: TenantContext, period_end: date) -> dict[str, Any]:
    """What a revaluation on ``period_end`` would post, read only."""
    with open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None:
            return {"period_end": period_end.isoformat(), "currencies": [], "base_currency": None}
        planned = plan(store, settings, period_end)
        live, _ = store.find("journal_entries", where={"source_kind": "revaluation", "status": "posted", "reverses_id": None,
                                                       "event_date": period_end}, limit=MAX_SCAN)
    done = {row.event_key: row for row in live}
    base = settings.base_currency
    out = []
    for item in planned:
        current = done.get(_key(period_end, item["currency"]))
        status = item["status"] if item["status"] == "missing_rate" else ("revalued" if current is not None else "to_revalue")
        out.append(_view(item, status, base))
    return {"period_end": period_end.isoformat(), "base_currency": base, "currencies": out}


__all__ = ["MONETARY", "balances", "plan", "preview", "run"]
