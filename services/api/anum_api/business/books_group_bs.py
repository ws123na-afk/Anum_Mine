"""The group (consolidated) balance sheet from the journals of the businesses in a group, pure
(docs/business-books.md#group-balance-sheet).

Each business's balance sheet comes from its own journal on the chosen day, in its functional (base) currency. In the
group's presentation currency:

* **Same currency:** taken as it is.
* **Another functional currency** (IAS 21, the current-rate method): assets and liabilities at the **closing rate** of
  the day; equity and the result at the rates of the days they arose (each journal day's equity and P&L lines at that
  day's rate, the transaction rates). The difference is the **translation reserve**, shown in the group's equity.
* **Rates:** each business's own book rates (typed or fetched, dated on or before the day, within its maximum age), never
  an invented one. A business with any figure that cannot be converted is listed as not converted, with what is missing,
  and left out of the total: a balance sheet is never summed from part of a business.
* **Eliminations:** balances between two businesses of the group (owed by and owed to related businesses, from the
  journal lines' counterparty) are taken out of both sides; a difference between the two sides' converted values (their
  rates differ) is shown in equity as the intercompany difference, so the total still balances.
* **Completeness:** a business whose journal has items not posted yet or posting issues is flagged; so is the total.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from .books_ledger_report import (
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    NON_CURRENT_ASSETS,
    NON_CURRENT_LIABILITIES,
)
from .books_models import AccountType, LedgerAccount
from .books_report import Converter

ZERO = Decimal(0)
ROWS = ("current_assets", "non_current_assets", "total_assets", "current_liabilities", "non_current_liabilities", "total_liabilities", "capital",
        "retained_earnings", "translation_reserve", "intercompany_difference", "total_equity", "liabilities_and_equity")


@dataclass
class BsMember:
    workspace_id: str
    name: str
    base: str
    accounts: list[LedgerAccount]
    cumulative: dict[str, tuple[Decimal, Decimal]]  # account id -> (debit, credit) up to the day
    equity_days: list[dict[str, Any]]  # rows of account_id, entry_date, debit, credit on equity and P&L accounts
    intercompany: list[dict[str, Any]]  # rows of account_id, counterparty_workspace_id, debit, credit on 1150 / 2050
    rates: list[Any]
    max_age: int
    complete: bool = True
    notes: list[str] = field(default_factory=list)


def _sides(accounts: list[LedgerAccount], cumulative: dict[str, tuple[Decimal, Decimal]]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for account in accounts:
        debit, credit = cumulative.get(account.id, (ZERO, ZERO))
        net = debit - credit
        if account.subtype in CURRENT_ASSETS:
            out["current_assets"] += net
        elif account.subtype in NON_CURRENT_ASSETS:
            out["non_current_assets"] += net
        elif account.subtype in CURRENT_LIABILITIES:
            out["current_liabilities"] -= net
        elif account.subtype in NON_CURRENT_LIABILITIES:
            out["non_current_liabilities"] -= net
        elif account.type == AccountType.EQUITY:
            out["capital" if account.subtype in ("capital", "drawings") else "retained_earnings"] -= net
        elif account.type in (AccountType.REVENUE, AccountType.EXPENSE):
            out["retained_earnings"] -= net  # the result not yet closed
    return out


def translate(member: BsMember, presentation: str, day: date) -> dict[str, Any]:
    """One business's column in the presentation currency, or why it cannot be converted."""
    sides = _sides(member.accounts, member.cumulative)
    ic_kind = {account.id: account.system_key for account in member.accounts if account.system_key in ("ic_receivable", "ic_payable")}
    if member.base == presentation:
        values = {key: quantize(sides.get(key, ZERO), presentation) for key in ("current_assets", "non_current_assets", "current_liabilities",
                                                                                 "non_current_liabilities", "capital", "retained_earnings")}
        values["translation_reserve"] = ZERO
        intercompany = [(row["counterparty_workspace_id"], ic_kind[row["account_id"]], row["debit"] - row["credit"]) for row in member.intercompany
                        if row["account_id"] in ic_kind and row["counterparty_workspace_id"]]
        return {"values": values, "intercompany": intercompany, "missing": [], "rate": None}
    converter = Converter(member.rates, presentation, member.max_age)
    closing = converter.path(member.base, day)
    missing: list[dict[str, str]] = []
    if closing is None:
        missing.append({"currency": member.base, "day": day.isoformat(), "what": "closing_rate"})
    historical = ZERO
    days_missing: set[date] = set()
    for row in member.equity_days:
        amount = row["credit"] - row["debit"]
        if amount == 0:
            continue
        path = converter.path(member.base, row["entry_date"])
        if path is None:
            days_missing.add(row["entry_date"])
            continue
        historical += amount * path.rate
    for missed in sorted(days_missing)[:20]:
        missing.append({"currency": member.base, "day": missed.isoformat(), "what": "transaction_rate"})
    if missing:
        return {"values": None, "intercompany": [], "missing": missing, "missing_days": len(days_missing), "rate": None}
    rate = closing.rate  # type: ignore[union-attr]

    def at_close(value: Decimal) -> Decimal:
        return quantize(value * rate, presentation)

    values = {key: at_close(sides.get(key, ZERO)) for key in ("current_assets", "non_current_assets", "current_liabilities", "non_current_liabilities")}
    # Equity at historical rates: capital and drawings, and the rest (retained earnings and the result) as one figure.
    capital_days = ZERO
    capital_ids = {account.id for account in member.accounts if account.subtype in ("capital", "drawings")}
    for row in member.equity_days:
        if row["account_id"] in capital_ids:
            path = converter.path(member.base, row["entry_date"])
            capital_days += (row["credit"] - row["debit"]) * path.rate  # type: ignore[union-attr]
    values["capital"] = quantize(capital_days, presentation)
    values["retained_earnings"] = quantize(historical, presentation) - values["capital"]
    net_assets = values["current_assets"] + values["non_current_assets"] - values["current_liabilities"] - values["non_current_liabilities"]
    values["translation_reserve"] = net_assets - values["capital"] - values["retained_earnings"]
    intercompany = [(row["counterparty_workspace_id"], ic_kind[row["account_id"]], at_close(row["debit"] - row["credit"])) for row in member.intercompany
                    if row["account_id"] in ic_kind and row["counterparty_workspace_id"]]
    return {"values": values, "intercompany": intercompany, "missing": [],
            "rate": {"from_currency": member.base, "to_currency": presentation, "rate": format(rate.normalize(), "f"),
                     "as_of": closing.as_of.isoformat(), "source": closing.source}}  # type: ignore[union-attr]


def _totals(values: dict[str, Decimal]) -> dict[str, Decimal]:
    out = {key: values.get(key, ZERO) for key in ROWS}
    out["total_assets"] = out["current_assets"] + out["non_current_assets"]
    out["total_liabilities"] = out["current_liabilities"] + out["non_current_liabilities"]
    out["total_equity"] = out["capital"] + out["retained_earnings"] + out["translation_reserve"] + out["intercompany_difference"]
    out["liabilities_and_equity"] = out["total_liabilities"] + out["total_equity"]
    return out


def consolidate(members: list[BsMember], presentation: str, day: date) -> dict[str, Any]:
    """Columns per business, the eliminations and the total, in ``presentation``; balanced when assets equal liabilities and equity."""
    group = {member.workspace_id for member in members}
    columns: dict[str, dict[str, Decimal]] = {}
    not_converted = []
    rates = []
    pairs: dict[tuple[str, str], Decimal] = defaultdict(Decimal)  # (owner of the balance, counterparty) -> receivable less payable
    included: list[str] = []
    for member in members:
        result = translate(member, presentation, day)
        if result["values"] is None:
            not_converted.append({"workspace_id": member.workspace_id, "name": member.name, "currency": member.base, "missing": result["missing"],
                                  "missing_days": result.get("missing_days", 0)})
            continue
        included.append(member.workspace_id)
        columns[member.workspace_id] = _totals(result["values"])
        if result["rate"]:
            rates.append({**result["rate"], "workspace_id": member.workspace_id})
        for counterparty, kind, value in result["intercompany"]:
            if counterparty in group:
                pairs[(member.workspace_id, counterparty)] += value  # a receivable is a debit (positive), a payable a credit (negative)
    eliminations: dict[str, Decimal] = defaultdict(Decimal)
    done: set[frozenset[str]] = set()
    for (owner, counterparty), value in pairs.items():
        key = frozenset((owner, counterparty))
        if key in done or owner not in included or counterparty not in included:
            continue
        done.add(key)
        mine, theirs = value, pairs.get((counterparty, owner), ZERO)
        for amount in (mine, theirs):
            if amount > 0:
                eliminations["current_assets"] -= amount
            elif amount < 0:
                eliminations["current_liabilities"] -= -amount
        # Both sides at their own closing rates: what does not cancel out is the intercompany difference.
        eliminations["intercompany_difference"] -= mine + theirs
    elim = _totals(dict(eliminations))
    total = _totals({key: sum((columns[w].get(key, ZERO) for w in included), ZERO) + eliminations.get(key, ZERO) for key in ROWS})
    by_member = {member.workspace_id: member for member in members}
    rows = []
    for key in ROWS:
        rows.append({"key": key, "businesses": {w: str(quantize(columns[w][key], presentation)) for w in included},
                     "eliminations": str(quantize(elim[key], presentation)), "total": str(quantize(total[key], presentation))})
    complete = not not_converted and all(by_member[w].complete for w in included)
    return {
        "as_of": day.isoformat(), "currency": presentation, "rows": rows, "included": included, "not_converted": not_converted, "rates": rates,
        "balanced": total["total_assets"] == total["liabilities_and_equity"],
        "difference": str(quantize(total["total_assets"] - total["liabilities_and_equity"], presentation)),
        "complete": complete,
        "businesses": [{"workspace_id": m.workspace_id, "name": m.name, "base_currency": m.base, "complete": m.complete, "notes": m.notes,
                        "included": m.workspace_id in included, "translated": m.base != presentation} for m in members],
    }


__all__ = ["ROWS", "BsMember", "consolidate", "translate"]
