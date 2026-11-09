"""Statements from the business journal: pure functions over account totals (docs/business-books.md#reports-from-the-journal).

Every figure here is in the business's base (functional) currency and comes from posted journal
lines: the trial balance, the balance sheet, the profit and loss, the statement of changes in equity
and the direct cash flow (per original currency, with opening and closing cash). ``books_ledger``
reads the sums and hands them over; the same sums always give the same statements.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from .books_models import SUBTYPES, AccountType, LedgerAccount
from .books_report import COST_OF_SALES, FINANCE_COSTS, FX_DIFFERENCE, OPERATING, OTHER_INCOME, REVENUE

ZERO = Decimal(0)
Totals = dict[str, tuple[Decimal, Decimal]]  # account id -> (debit, credit)

PNL_GROUP = {"revenue": REVENUE, "other_income": OTHER_INCOME, "cost_of_sales": COST_OF_SALES, "operating_expense": OPERATING,
             "depreciation": OPERATING, "finance_cost": FINANCE_COSTS, "fx": FX_DIFFERENCE}
CREDIT_GROUPS = frozenset({REVENUE, OTHER_INCOME, FX_DIFFERENCE})
CURRENT_ASSETS = ("bank", "receivable", "tax_receivable", "prepayment", "current_asset")
NON_CURRENT_ASSETS = ("fixed_asset", "accumulated_depreciation")
CURRENT_LIABILITIES = ("payable", "tax_payable", "accrual", "interest_payable", "owed_to_members", "current_liability")
NON_CURRENT_LIABILITIES = ("loan",)
EQUITY = ("capital", "drawings", "retained_earnings", "current_result")
OPERATING_FLOWS = ("customers_paid", "suppliers_paid", "members_reimbursed", "interest_paid", "intercompany_in", "intercompany_out", "other")
INVESTING_FLOWS = ("capital_spend", "intercompany_lent")
FINANCING_FLOWS = ("loans_received", "loans_repaid", "owner_contributions", "owner_drawings", "intercompany_borrowed")


def s(amount: Decimal, currency: str) -> str:
    return str(quantize(amount, currency))


def natural(account: LedgerAccount, debit: Decimal, credit: Decimal) -> Decimal:
    """An account's balance on its normal side: debit less credit for assets and expenses, the other way round otherwise."""
    if account.type in (AccountType.ASSET, AccountType.EXPENSE):
        return debit - credit
    return credit - debit


def _row(account: LedgerAccount, amount: Decimal, base: str) -> dict[str, Any]:
    return {"account_id": account.id, "code": account.code, "name": account.name, "subtype": account.subtype, "system_key": account.system_key,
            "amount": s(amount, base)}


def trial_balance(accounts: Iterable[LedgerAccount], opening: Totals, period: Totals, base: str) -> dict[str, Any]:
    """Opening balance, the period's debits and credits and the closing balance per account; totals and whether they agree."""
    rows = []
    sums = defaultdict(Decimal)
    for account in sorted(accounts, key=lambda item: item.code):
        od, oc = opening.get(account.id, (ZERO, ZERO))
        pd, pc = period.get(account.id, (ZERO, ZERO))
        if not (od or oc or pd or pc):
            continue
        open_net, close_net = od - oc, od - oc + pd - pc
        rows.append({"account_id": account.id, "code": account.code, "name": account.name, "subtype": account.subtype, "type": account.type.value,
                     "opening_debit": s(max(open_net, ZERO), base), "opening_credit": s(max(-open_net, ZERO), base),
                     "debit": s(pd, base), "credit": s(pc, base),
                     "closing_debit": s(max(close_net, ZERO), base), "closing_credit": s(max(-close_net, ZERO), base)})
        sums["opening_debit"] += max(open_net, ZERO)
        sums["opening_credit"] += max(-open_net, ZERO)
        sums["debit"] += pd
        sums["credit"] += pc
        sums["closing_debit"] += max(close_net, ZERO)
        sums["closing_credit"] += max(-close_net, ZERO)
    totals = {key: s(sums[key], base) for key in ("opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit")}
    difference = sums["closing_debit"] - sums["closing_credit"]
    return {"rows": rows, "totals": totals, "balanced": difference == 0 and sums["debit"] == sums["credit"],
            "difference": s(difference, base)}


def pnl(accounts: Iterable[LedgerAccount], period: Totals, base: str) -> dict[str, Any]:
    """The profit and loss of the period's P&L account movements (closing entries left out by the caller)."""
    groups: dict[str, Decimal] = defaultdict(Decimal)
    lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account in sorted(accounts, key=lambda item: item.code):
        group = PNL_GROUP.get(account.subtype)
        if group is None:
            continue
        debit, credit = period.get(account.id, (ZERO, ZERO))
        amount = credit - debit if group in CREDIT_GROUPS else debit - credit
        if amount == 0:
            continue
        groups[group] += amount
        lines[group].append(_row(account, amount, base))
    gross = groups[REVENUE] - groups[COST_OF_SALES]
    operating = gross - groups[OPERATING]
    net = operating + groups[OTHER_INCOME] - groups[FINANCE_COSTS] + groups[FX_DIFFERENCE]

    def section(group: str) -> dict[str, Any]:
        return {"lines": lines[group], "total": s(groups[group], base)}

    return {
        "currency": base,
        "revenue": section(REVENUE), "cost_of_sales": section(COST_OF_SALES), "gross_profit": s(gross, base), "gross_margin_pct": pct(gross, groups[REVENUE]),
        "operating_expenses": section(OPERATING), "operating_profit": s(operating, base), "other_income": section(OTHER_INCOME),
        "finance_costs": section(FINANCE_COSTS), "fx_differences": s(groups[FX_DIFFERENCE], base), "net_profit": s(net, base),
        "net_margin_pct": pct(net, groups[REVENUE]),
        "groups": {key: s(value, base) for key, value in groups.items()},
    }


def pct(part: Decimal, whole: Decimal) -> str | None:
    if whole <= 0:
        return None
    return str((part * Decimal(100) / whole).quantize(Decimal("0.1")))


def net_result(accounts: Iterable[LedgerAccount], totals: Totals) -> Decimal:
    """Revenue less expenses of ``totals`` (a profit is positive)."""
    out = ZERO
    for account in accounts:
        if account.type in (AccountType.REVENUE, AccountType.EXPENSE):
            debit, credit = totals.get(account.id, (ZERO, ZERO))
            out += credit - debit
    return out


def balance_sheet(accounts: list[LedgerAccount], cumulative: Totals, base: str) -> dict[str, Any]:
    """Assets, liabilities and equity on a day, from every line up to it. The result not yet closed to retained earnings is
    shown in equity as the current year result."""
    by_subtype: dict[str, list[tuple[LedgerAccount, Decimal]]] = defaultdict(list)
    for account in sorted(accounts, key=lambda item: item.code):
        if account.type in (AccountType.REVENUE, AccountType.EXPENSE):
            continue
        debit, credit = cumulative.get(account.id, (ZERO, ZERO))
        amount = natural(account, debit, credit)
        if account.subtype == "accumulated_depreciation":
            amount = debit - credit  # a contra asset: shown as a negative asset
        if amount != 0 or account.system_key:
            by_subtype[account.subtype].append((account, amount))

    def block(subtypes: tuple[str, ...]) -> tuple[list[dict[str, Any]], Decimal]:
        rows, total = [], ZERO
        for subtype in subtypes:
            for account, amount in by_subtype.get(subtype, []):
                if amount == 0:
                    continue
                rows.append(_row(account, amount, base))
                total += amount
        return rows, total

    current_assets, ca = block(CURRENT_ASSETS)
    fixed, nca = block(NON_CURRENT_ASSETS)
    current_liabilities, cl = block(CURRENT_LIABILITIES)
    long_term, ncl = block(NON_CURRENT_LIABILITIES)
    equity_rows, equity_total = block(EQUITY)
    unclosed = net_result(accounts, cumulative)
    if unclosed != 0:
        result_account = next((a for a in accounts if a.system_key == "current_result"), None)
        equity_rows.append({"account_id": result_account.id if result_account else None, "code": result_account.code if result_account else "",
                            "name": "Result not yet closed", "subtype": "current_result", "system_key": "unclosed_result", "amount": s(unclosed, base)})
        equity_total += unclosed
    assets = ca + nca
    liabilities = cl + ncl
    return {
        "currency": base,
        "assets": {"current": {"rows": current_assets, "total": s(ca, base)}, "non_current": {"rows": fixed, "total": s(nca, base)}, "total": s(assets, base)},
        "liabilities": {"current": {"rows": current_liabilities, "total": s(cl, base)}, "non_current": {"rows": long_term, "total": s(ncl, base)},
                        "total": s(liabilities, base)},
        "equity": {"rows": equity_rows, "total": s(equity_total, base)},
        "liabilities_and_equity": s(liabilities + equity_total, base),
        "balanced": assets == liabilities + equity_total,
        "difference": s(assets - liabilities - equity_total, base),
    }


def equity_statement(accounts: list[LedgerAccount], opening: Totals, period: Totals, closing_moves: Totals, ending: Totals,
                     base: str) -> dict[str, Any]:
    """Changes in equity over the period: opening balances, owner money in and out, the result, the year-end transfer to retained
    earnings and the closing balances. ``period`` leaves out closing entries; ``closing_moves`` holds only them; ``ending`` is every
    line up to the last day (the check)."""
    columns = ("capital", "drawings", "retained_earnings", "result")

    def equity_of(totals: Totals) -> dict[str, Decimal]:
        out = {column: ZERO for column in columns}
        for account in accounts:
            if account.type != AccountType.EQUITY:
                continue
            debit, credit = totals.get(account.id, (ZERO, ZERO))
            column = {"capital": "capital", "drawings": "drawings", "retained_earnings": "retained_earnings"}.get(account.subtype, "retained_earnings")
            out[column] += credit - debit
        return out

    opened = equity_of(opening)
    opened["result"] = net_result(accounts, opening)
    moves = equity_of(period)
    result = net_result(accounts, period)
    transfer = equity_of(closing_moves)["retained_earnings"]
    contributions, drawings = moves["capital"], moves["drawings"]
    other = moves["retained_earnings"]
    closing = {
        "capital": opened["capital"] + contributions,
        "drawings": opened["drawings"] + drawings,
        "retained_earnings": opened["retained_earnings"] + other + transfer,
        "result": opened["result"] + result - transfer,
    }

    def row(key: str, values: dict[str, Decimal]) -> dict[str, Any]:
        return {"key": key, **{column: s(values.get(column, ZERO), base) for column in columns}, "total": s(sum(values.values(), ZERO), base)}

    return {
        "currency": base,
        "rows": [
            row("opening", opened),
            row("contributions", {"capital": contributions}),
            row("drawings", {"drawings": drawings}),
            row("result", {"result": result}),
            row("transfer_to_retained_earnings", {"retained_earnings": transfer, "result": -transfer}),
            row("other", {"retained_earnings": other}),
            row("closing", closing),
        ],
        # The movements explain the change: the closing balances equal the balances read on the last day.
        "balanced": closing == {**equity_of(ending), "result": net_result(accounts, ending)},
    }


def cash_flow(opening: dict[str, Decimal], closing: dict[str, Decimal], movements: list[dict[str, Any]], base_totals: dict[str, Decimal],
              base: str) -> dict[str, Any]:
    """The direct cash flow per original currency: opening cash, money in and out by activity, closing cash. ``movements``
    are rows of ``flow``, ``currency`` and the signed original ``amount`` of bank lines in the period."""
    by_currency: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in movements:
        by_currency[row["currency"]][row["flow"] or "other"] += row["amount"]
    currencies = sorted(set(by_currency) | {c for c, v in opening.items() if v} | {c for c, v in closing.items() if v})
    out = []
    for currency in currencies:
        flows = by_currency.get(currency, {})

        def activity(names: tuple[str, ...], flows: dict[str, Decimal] = flows, currency: str = currency) -> dict[str, Any]:
            lines = [{"key": name, "amount": s(flows[name], currency)} for name in names if flows.get(name)]
            return {"lines": lines, "total": s(sum((flows.get(name, ZERO) for name in names), ZERO), currency)}

        net = sum(flows.values(), ZERO)
        start, end = opening.get(currency, ZERO), closing.get(currency, ZERO)
        out.append({"currency": currency, "opening": s(start, currency), "operating": activity(OPERATING_FLOWS), "investing": activity(INVESTING_FLOWS),
                    "financing": activity(FINANCING_FLOWS), "net": s(net, currency), "closing": s(end, currency),
                    "reconciles": start + net == end})
    return {"by_currency": out, "base_currency": base,
            "base": {key: s(value, base) for key, value in base_totals.items()}}


def reconciliation(journal: dict[str, Any], derived: dict[str, Any], base: str) -> dict[str, Any]:
    """The journal's P&L next to the P&L derived from the records for the same days, accrual basis, with the difference per line."""
    rows = []
    for key, group in (("revenue", "revenue"), ("cost_of_sales", "cost_of_sales"), ("operating_expenses", "operating_expenses"),
                       ("other_income", "other_income"), ("finance_costs", "finance_costs")):
        mine, theirs = Decimal(journal[key]["total"]), Decimal(derived[group]["total"])
        rows.append({"key": key, "journal": s(mine, base), "derived": s(theirs, base), "difference": s(mine - theirs, base)})
    for key in ("fx_differences", "net_profit"):
        mine, theirs = Decimal(journal[key]), Decimal(derived[key])
        rows.append({"key": key, "journal": s(mine, base), "derived": s(theirs, base), "difference": s(mine - theirs, base)})
    return {"rows": rows, "agrees": all(Decimal(row["difference"]) == 0 for row in rows)}


def comparative_periods(start: date, end: date) -> dict[str, tuple[date, date]]:
    """The previous period of the same length, ending the day before ``start``, and the same days a year earlier."""
    from datetime import timedelta

    length = (end - start).days
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length)

    def year_back(day: date) -> date:
        try:
            return day.replace(year=day.year - 1)
        except ValueError:  # 29 February
            return day.replace(year=day.year - 1, day=28)

    return {"previous": (previous_start, previous_end), "last_year": (year_back(start), year_back(end))}


def subtype_type(subtype: str) -> AccountType:
    return SUBTYPES[subtype][0]


__all__ = ["balance_sheet", "cash_flow", "comparative_periods", "equity_statement", "natural", "net_result", "pnl", "reconciliation", "trial_balance"]
