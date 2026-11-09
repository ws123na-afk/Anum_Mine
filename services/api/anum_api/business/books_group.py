"""Consolidated books across the owner's businesses (docs/business-books.md#consolidated-books): pure figures.

One combined P&L, the balance figures and the cash flow in a reporting currency the owner chooses,
with a column per business, a column of eliminations and the total.

* **Who is in it.** Only businesses where the viewer holds ``business:manage`` (checked by the API,
  ``books_service.manageable_workspace``) and whose books are set up. Nothing is read from any other.
* **Currencies.** Every figure is converted on its own date with **that business's own** dated rates
  (its book rates, typed or fetched, within its maximum rate age), never an invented one. A figure
  without a rate is listed as *not converted*, per business and currency, and never summed.
* **Eliminations.** Intercompany entries are written in both businesses, mirrored. When both sides
  are in the consolidation, a recharge (other income on one side, a cost on the other), a loan and a
  settlement between them are taken out of the total, each side by the value it was converted at,
  so internal recharges and loans never inflate revenue, costs or debt. An entry with a business
  outside the consolidation stays: for the group it is a real receivable, payable, income or cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from .books_models import Basis
from .books_report import (
    CASH_IN,
    COST_OF_SALES,
    FINANCE_COSTS,
    FX_DIFFERENCE,
    MEMBERS,
    OPERATING,
    OTHER_INCOME,
    REVENUE,
    Book,
    Converter,
    Inputs,
    balances,
    cash_flows,
    entries,
)

ZERO = Decimal(0)
PNL_ROWS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "operating_profit", "other_income", "finance_costs", "fx_differences",
            "net_profit")
BALANCE_ROWS = ("receivables", "payables", "owed_to_members", "debt", "intercompany_receivable", "intercompany_payable")
CASH_ROWS = ("customers_paid", "suppliers_paid", "capital_spend", "loans_received", "loans_repaid", "interest_paid", "owner_contributions",
             "owner_drawings", "members_reimbursed", "intercompany_in", "intercompany_out", "intercompany_lent", "intercompany_borrowed", "net",
             "paid_by_members")
_GROUP_ROW = {REVENUE: "revenue", COST_OF_SALES: "cost_of_sales", OPERATING: "operating_expenses", OTHER_INCOME: "other_income",
              FINANCE_COSTS: "finance_costs", FX_DIFFERENCE: "fx_differences"}


@dataclass
class Member:
    workspace_id: str
    name: str
    inputs: Inputs


def _results(groups: dict[str, Decimal]) -> dict[str, Decimal]:
    """The statement rows from the group totals (the same arithmetic as one business's P&L)."""
    out = {row: groups.get(row, ZERO) for row in ("revenue", "cost_of_sales", "operating_expenses", "other_income", "finance_costs", "fx_differences")}
    out["gross_profit"] = out["revenue"] - out["cost_of_sales"]
    out["operating_profit"] = out["gross_profit"] - out["operating_expenses"]
    out["net_profit"] = out["operating_profit"] + out["other_income"] - out["finance_costs"] + out["fx_differences"]
    return out


class _Sheet:
    """Rows by column in the reporting currency, and what could not be converted (per column, row and currency)."""

    def __init__(self, currency: str) -> None:
        self.currency = currency
        self.cells: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))  # column -> row -> amount
        self.missing: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)  # (column, row, currency) -> amount

    def put(self, column: str, row: str, amount: Decimal, currency: str, converter: Converter, on: date) -> Decimal | None:
        value = converter.convert(amount, currency, on)
        if value is None:
            self.missing[(column, row, currency)] += amount
            return None
        self.cells[column][row] += value
        return value


def _q(amount: Decimal, currency: str) -> str:
    return str(quantize(amount, currency))


def consolidate(members: list[Member], start: date, end: date, basis: Basis, currency: str, today: date | None = None) -> dict[str, Any]:
    """The consolidated P&L, balances and cash flow of ``members`` in ``currency`` for ``start``..``end``."""
    group = {member.workspace_id for member in members}
    pnl, owed, cash = _Sheet(currency), _Sheet(currency), _Sheet(currency)
    eliminated: dict[str, dict[str, Decimal]] = {"pnl": defaultdict(Decimal), "balances": defaultdict(Decimal), "cash": defaultdict(Decimal)}
    elim_missing: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)  # (section, row, currency) -> amount taken out unconverted
    conversions: list[dict[str, Any]] = []
    notes: dict[str, list[str]] = {}
    on = min(end, today) if today else end
    for member in members:
        column = member.workspace_id
        book = Book(member.inputs)
        converter = Converter(member.inputs.rates, currency, member.inputs.settings.rate_max_age_days)
        member_notes: set[str] = set()
        groups: dict[str, Decimal] = defaultdict(Decimal)
        for entry in entries(book, basis, start, end, converter, member_notes):
            row = _GROUP_ROW[entry.group]
            value = pnl.put(column, row, entry.amount, entry.currency, converter, entry.on)
            if value is not None:
                groups[row] += value
            if entry.counterparty in group:
                if value is None:
                    elim_missing[("pnl", row, entry.currency)] += entry.amount
                else:
                    eliminated["pnl"][row] += value
        for row, amount in _results(groups).items():
            pnl.cells[column][row] = amount

        # Balances on the last day, each currency converted on that day with the business's rates.
        figures = balances(book, end, today)
        for row, items, field in (("receivables", figures["receivables"], "total"), ("payables", figures["payables"], "total"),
                                  ("owed_to_members", figures["owed_to_members"], "amount"), ("debt", figures["debt"], "amount")):
            for item in items:
                owed.put(column, row, Decimal(item[field]), item["currency"], converter, on)
        for item in figures["intercompany"]:
            amount = Decimal(item["amount"])
            row = "intercompany_receivable" if amount > 0 else "intercompany_payable"
            value = owed.put(column, row, abs(amount), item["currency"], converter, on)
            if item["workspace_id"] in group:
                if value is None:
                    elim_missing[("balances", row, item["currency"])] += abs(amount)
                else:
                    eliminated["balances"][row] += value

        # Cash flow: every movement converted on its own date; money members paid from their own pocket shown apart.
        net = ZERO
        for flow in cash_flows(book, start, end):
            row = "paid_by_members" if flow.line.startswith(MEMBERS) else flow.line
            value = cash.put(column, row, flow.amount, flow.currency, converter, flow.on)
            if row != "paid_by_members" and value is not None:
                net += value if flow.line in CASH_IN else -value
            if flow.counterparty in group:
                if value is None:
                    elim_missing[("cash", row, flow.currency)] += flow.amount
                else:
                    eliminated["cash"][row] += value
                    eliminated["cash"]["net"] += value if flow.line in CASH_IN else -value
        cash.cells[column]["net"] = net

        for item in converter.conversions():
            conversions.append({**item, "workspace_id": column})
        if member.inputs.truncated:
            member_notes.add("truncated")
        if any(key[0] == column for sheet in (pnl, owed, cash) for key in sheet.missing):
            member_notes.add("not_converted")
        notes[column] = sorted(member_notes)

    def totals(sheet: _Sheet, section: str, rows: tuple[str, ...]) -> dict[str, Decimal]:
        return {row: sum((sheet.cells[m.workspace_id].get(row, ZERO) for m in members), ZERO) - eliminated[section].get(row, ZERO) for row in rows}

    pnl_total = totals(pnl, "pnl", ("revenue", "cost_of_sales", "operating_expenses", "other_income", "finance_costs", "fx_differences"))
    pnl_total = _results(pnl_total)
    pnl_elim = _results({row: -amount for row, amount in eliminated["pnl"].items()})
    owed_total = totals(owed, "balances", BALANCE_ROWS)
    cash_total = totals(cash, "cash", CASH_ROWS)

    def columns(sheet: _Sheet, rows: tuple[str, ...], elim: dict[str, Decimal], total: dict[str, Decimal]) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            out.append({"key": row,
                        "businesses": {m.workspace_id: _q(sheet.cells[m.workspace_id].get(row, ZERO), currency) for m in members},
                        "eliminations": _q(elim.get(row, ZERO), currency), "total": _q(total.get(row, ZERO), currency)})
        return out

    def missing(sheet: _Sheet, section: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (column, row, code), amount in sorted(sheet.missing.items()):
            if amount != 0:
                rows.append({"workspace_id": column, "row": row, "currency": code, "amount": _q(amount, code)})
        # What the total still lacks: each business's unconverted figures less the internal ones taken out.
        left: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for (_, row, code), amount in sheet.missing.items():
            left[(row, code)] += amount
        for (where, row, code), amount in elim_missing.items():
            if where == section:
                left[(row, code)] -= amount
        return rows + [{"workspace_id": None, "row": row, "currency": code, "amount": _q(amount, code)}
                       for (row, code), amount in sorted(left.items()) if amount != 0]

    not_converted = {"pnl": missing(pnl, "pnl"), "balances": missing(owed, "balances"), "cash": missing(cash, "cash")}
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "basis": basis.value},
        "currency": currency,
        "as_of": end.isoformat(),
        "businesses": [{"workspace_id": m.workspace_id, "name": m.name, "base_currency": m.inputs.settings.base_currency, "notes": notes[m.workspace_id]}
                       for m in members],
        "pnl": columns(pnl, PNL_ROWS, pnl_elim, pnl_total),
        "balances": columns(owed, BALANCE_ROWS, {row: -amount for row, amount in eliminated["balances"].items()}, owed_total),
        "cash_flow": columns(cash, CASH_ROWS, {row: -amount for row, amount in eliminated["cash"].items()}, cash_total),
        "not_converted": not_converted,
        # Incomplete when a figure is not converted or a business had more records than were read (defect B9).
        "complete": not any(not_converted.values()) and not any(m.inputs.truncated for m in members),
        "conversions": conversions,
        "eliminated": any(amount != 0 for section in eliminated.values() for amount in section.values()),
    }


__all__ = ["BALANCE_ROWS", "CASH_ROWS", "Member", "PNL_ROWS", "consolidate"]
