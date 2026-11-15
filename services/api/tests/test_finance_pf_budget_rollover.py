"""Budget rollover: what a spending budget carries from one period into the next (docs/finance.md#budget-rollover).

In-memory stores. Every merchant, description and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from test_finance_pf_budgets import budget, row, txn
from test_finance_sms import API, OTHER_PERSON, OWNER, TENANT, WORKSPACE, VIEWER, account, clean, client  # noqa: F401

from anum_api.finance import budget_rollover as RO
from anum_api.finance import safe_to_spend as STS
from anum_api.finance.commitments import plan_budget_report
from anum_api.finance.models import (
    Account,
    AccountKind,
    BudgetPeriod,
    BudgetTerms,
    Category,
    OverspendMode,
    PlanSettings,
    RolloverMode,
    TransactionType,
)
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext

D = Decimal
TODAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
G = Category.GROCERIES
EXP = TransactionType.EXPENSE


def terms(start: date, rollover: RolloverMode = RolloverMode.NONE, *, overspend: OverspendMode = OverspendMode.ABSORB, cap: str | None = None,
          amount: str = "1000", categories: list[Category] | None = None, currency: str = "AED", budget_id: str = "food") -> BudgetTerms:
    return BudgetTerms(budget_id=budget_id, effective_from=start, currency=currency, amount=D(amount), categories=categories or [G],
                       rollover=rollover, cap=D(cap) if cap else None, overspend=overspend, set_at=NOW)


def spend(month: int, amount: str, day: int = 10, tid: str | None = None) -> object:
    return row(tid or f"g{month}_{day}_{amount}", f"-{amount}", "SAMPLE GROCER", date(2026, month, day), category=G, kind=EXP)


# Aug 800, Sep 900, Oct 1100 of a 1000 budget; nothing yet in November.
THREE_MONTHS = [spend(8, "800"), spend(9, "900"), spend(10, "1100")]


def line(rollovers: list[BudgetTerms], rows: list, *, on: date = TODAY, food=None):  # type: ignore[no-untyped-def]
    report = plan_budget_report([food or budget("food", "1000", [G])], rows, on=on, today=TODAY, bills=[], bill_links=[], rollovers=rollovers)
    return report.budgets[0]


def carried(view) -> list[tuple[str, str | None]]:  # type: ignore[no-untyped-def]
    return [(str(p.carried_in), None if p.carried_out is None else str(p.carried_out)) for p in view.chain]


# -- each mode across three months ---------------------------------------------------------------------------


def test_no_rollover_starts_every_period_from_its_amount() -> None:
    view = line([terms(date(2026, 8, 1))], THREE_MONTHS)
    assert view.chain == [] and view.chain_periods == 0
    assert view.carried_in == D("0.00") and view.available == D("1000.00") and view.rollover == "none" and view.overspend == "absorb"
    plain = line([], THREE_MONTHS)
    assert (plain.available, plain.remaining, plain.spent) == (view.available, view.remaining, view.spent)


def test_surplus_carries_all_that_is_left_and_absorbs_an_overspend() -> None:
    view = line([terms(date(2026, 8, 1), RolloverMode.SURPLUS)], THREE_MONTHS)
    # Aug 1000 - 800 = 200 out; Sep 1200 - 900 = 300 out; Oct 1300 - 1100 = 200 out; Nov 1000 + 200.
    assert carried(view) == [("0.00", "200.00"), ("200.00", "300.00"), ("300.00", "200.00"), ("200.00", None)]
    assert [p.spent for p in view.chain] == [D("800.00"), D("900.00"), D("1100.00"), D("0.00")]
    assert view.carried_in == D("200.00") and view.available == D("1200.00") and view.remaining == D("1200.00")
    over = line([terms(date(2026, 8, 1), RolloverMode.SURPLUS)], [spend(8, "1300"), spend(9, "100")])
    # The August overspend is absorbed: September starts from 1000 and carries its 900 into October.
    assert carried(over)[:3] == [("0.00", "0.00"), ("0.00", "900.00"), ("900.00", "1900.00")]


def test_capped_carry_never_passes_the_cap() -> None:
    view = line([terms(date(2026, 8, 1), RolloverMode.CAPPED, cap="150")], THREE_MONTHS)
    # Aug left 200, carry 150; Sep 1150 - 900 = 250, carry 150; Oct 1150 - 1100 = 50, carry 50; Nov 1050.
    assert carried(view) == [("0.00", "150.00"), ("150.00", "150.00"), ("150.00", "50.00"), ("50.00", None)]
    assert view.available == D("1050.00") and view.rollover == "capped" and view.rollover_cap == D("150.00")
    assert all(p.carried_out is None or p.carried_out <= D("150") for p in view.chain)


def test_a_carried_deficit_lowers_the_next_period_and_keeps_growing_until_repaid() -> None:
    rows = [spend(8, "1200"), spend(9, "900"), spend(10, "500")]
    view = line([terms(date(2026, 8, 1), overspend=OverspendMode.CARRY)], rows)
    # Aug -200 out; Sep 800 - 900 = -100 out; Oct 900 - 500 = 400 left, nothing carries (no surplus rollover).
    assert carried(view) == [("0.00", "-200.00"), ("-200.00", "-100.00"), ("-100.00", "0.00"), ("0.00", None)]
    assert view.available == D("1000.00")
    deeper = line([terms(date(2026, 8, 1), overspend=OverspendMode.CARRY)], [spend(8, "1600"), spend(9, "1500"), spend(10, "200")])
    # A deficit larger than a period's amount: September starts below zero and is over before any spending.
    assert carried(deeper)[:3] == [("0.00", "-600.00"), ("-600.00", "-1100.00"), ("-1100.00", "-300.00")]
    assert deeper.carried_in == D("-300.00") and deeper.available == D("700.00")
    in_debt = line([terms(date(2026, 8, 1), overspend=OverspendMode.CARRY)], [spend(8, "1600"), spend(9, "1500")], on=date(2026, 10, 5))
    assert in_debt.available == D("-100.00") and in_debt.risk == "over" and in_debt.percent == 100 and in_debt.remaining == D("0.00")


def test_surplus_and_deficit_carry_together() -> None:
    view = line([terms(date(2026, 8, 1), RolloverMode.SURPLUS, overspend=OverspendMode.CARRY)], [spend(8, "1200"), spend(9, "500"), spend(10, "1000")])
    assert carried(view) == [("0.00", "-200.00"), ("-200.00", "300.00"), ("300.00", "300.00"), ("300.00", None)]


# -- a setting change mid-history ---------------------------------------------------------------------------


def test_a_change_applies_from_its_period_on_and_earlier_periods_keep_their_figures() -> None:
    before = line([terms(date(2026, 8, 1), RolloverMode.SURPLUS)], THREE_MONTHS)
    # In October the owner raised the amount to 1500 and switched the rollover off.
    history = [terms(date(2026, 8, 1), RolloverMode.SURPLUS), terms(date(2026, 10, 1), amount="1500")]
    after = line(history, THREE_MONTHS)
    assert [p.model_dump() for p in after.chain[:2]] == [p.model_dump() for p in before.chain[:2]]  # Aug and Sep untouched
    october = after.chain[2]
    # October keeps what September carried (300) on its new amount, and carries nothing out under "none".
    assert (october.amount, october.carried_in, october.available, october.carried_out) == (D("1500.00"), D("300.00"), D("1800.00"), D("0.00"))
    assert [p.rollover for p in after.chain] == ["surplus", "surplus", "none", "none"]
    assert after.available == D("1500.00") and after.rollover == "none"
    # Looking back at September shows the terms September used, not today's.
    september = line(history, THREE_MONTHS, on=date(2026, 9, 20))
    assert september.amount == D("1000.00") and september.rollover == "surplus" and september.carried_in == D("200.00")


def test_categories_follow_the_terms_of_each_period() -> None:
    rows = [*THREE_MONTHS, row("d9", "-400", "SAMPLE CAFE", date(2026, 9, 12), category=Category.DINING, kind=EXP)]
    history = [terms(date(2026, 8, 1), RolloverMode.SURPLUS), terms(date(2026, 10, 1), RolloverMode.SURPLUS, categories=[G, Category.DINING])]
    view = line(history, rows, food=budget("food", "1000", [G, Category.DINING]))
    # The September cafe row was outside the budget then: September still carries 300.
    assert view.chain[1].spent == D("900.00") and view.chain[1].carried_out == D("300.00")


def test_a_currency_change_restarts_the_chain_without_converting() -> None:
    history = [terms(date(2026, 8, 1), RolloverMode.SURPLUS), terms(date(2026, 10, 1), RolloverMode.SURPLUS, currency="SAR")]
    rows = [*THREE_MONTHS, row("s10", "-100", "SAMPLE GROCER", date(2026, 10, 10), category=G, kind=EXP, currency="SAR")]
    sar = budget("food", "1000", [G])
    sar = sar.model_copy(update={"currency": "SAR"})
    view = line(history, rows, food=sar)
    assert view.chain[2].restarted and view.chain[2].carried_in == D("0.00") and view.chain[2].spent == D("100.00")
    assert view.carried_in == D("900.00") and view.currency == "SAR"


# -- weekly and custom periods ------------------------------------------------------------------------------


def test_weekly_budget_carries_week_by_week_from_its_weekday() -> None:
    weekly = budget("food", "200", [G], period=BudgetPeriod.WEEKLY, starts_on=date(2026, 10, 1))  # Thursday weeks
    rows = [row("w1", "-150", "SAMPLE GROCER", date(2026, 10, 23), category=G, kind=EXP),  # week from 22 Oct
            row("w2", "-260", "SAMPLE GROCER", date(2026, 10, 29), category=G, kind=EXP),  # week from 29 Oct
            row("w3", "-100", "SAMPLE GROCER", date(2026, 11, 11), category=G, kind=EXP)]  # week from 5 Nov
    view = line([terms(date(2026, 10, 22), RolloverMode.SURPLUS, amount="200")], rows, food=weekly)
    assert [p.start for p in view.chain] == [date(2026, 10, 22), date(2026, 10, 29), date(2026, 11, 5), date(2026, 11, 12)]
    # 50 left, then 250 - 260 overspend absorbed, then 200 - 100 = 100, then 300 available this week.
    assert carried(view) == [("0.00", "50.00"), ("50.00", "0.00"), ("0.00", "100.00"), ("100.00", None)]
    assert (view.period_start, view.period_end, view.available) == (date(2026, 11, 12), date(2026, 11, 18), D("300.00"))


def test_custom_period_repeats_back_to_back_once_it_carries() -> None:
    trip = budget("food", "500", [G], period=BudgetPeriod.CUSTOM, starts_on=date(2026, 10, 1), ends_on=date(2026, 10, 10))
    rows = [row("c1", "-300", "SAMPLE GROCER", date(2026, 10, 5), category=G, kind=EXP),
            row("c2", "-100", "SAMPLE GROCER", date(2026, 10, 15), category=G, kind=EXP)]
    view = line([terms(date(2026, 10, 1), RolloverMode.SURPLUS, amount="500")], rows, food=trip)
    assert [(p.start, p.end) for p in view.chain] == [(date(2026, 10, 1), date(2026, 10, 10)), (date(2026, 10, 11), date(2026, 10, 20)),
                                                       (date(2026, 10, 21), date(2026, 10, 30)), (date(2026, 10, 31), date(2026, 11, 9)),
                                                       (date(2026, 11, 10), date(2026, 11, 19))]
    assert view.carried_in == D("1600.00")  # 200 + 400 + 500 + 500 carried
    plain = line([], rows, food=trip)
    assert (plain.period_start, plain.period_end) == (date(2026, 10, 1), date(2026, 10, 10))  # without rollover it does not repeat


# -- counted once -------------------------------------------------------------------------------------------


def test_each_row_counts_once_in_its_own_period_and_non_spending_never_counts() -> None:
    rows = [
        spend(9, "400", day=30, tid="end_sep"), spend(10, "300", day=1, tid="start_oct"),
        row("ref", "100", "SAMPLE GROCER REFUND", date(2026, 10, 2), category=G, kind=TransactionType.REFUND),
        row("own", "-500", "OWN ACCOUNT", date(2026, 10, 3), category=Category.TRANSFER, kind=TransactionType.INTERNAL_TRANSFER),
        row("card", "-700", "CARD PAYMENT", date(2026, 10, 4), category=Category.TRANSFER, kind=TransactionType.CARD_PAYMENT),
        row("usd", "-50", "SAMPLE GROCER", date(2026, 10, 5), category=G, kind=EXP, currency="USD"),
        row("lent", "-250", "SAMPLE GROCER", date(2026, 10, 6), category=G, kind=EXP),
    ]
    report = plan_budget_report([budget("food", "1000", [G])], rows, on=TODAY, today=TODAY, bills=[], bill_links=[], lending=frozenset({"lent"}),
                                rollovers=[terms(date(2026, 9, 1), RolloverMode.SURPLUS)])
    view = report.budgets[0]
    september, october = view.chain[0], view.chain[1]
    assert september.spent == D("400.00") and october.spent == D("200.00")  # 300 less the 100 refund; transfers, USD and lending left out
    assert sum((p.spent for p in view.chain), D(0)) == D("600.00")
    assert carried(view) == [("0.00", "600.00"), ("600.00", "1400.00"), ("1400.00", None)]
    # Reading the report again, or for an earlier date, gives the same figures (nothing stored, nothing rewritten).
    again = plan_budget_report([budget("food", "1000", [G])], rows, on=TODAY, today=TODAY, bills=[], bill_links=[], lending=frozenset({"lent"}),
                               rollovers=[terms(date(2026, 9, 1), RolloverMode.SURPLUS)])
    assert again.budgets[0].model_dump() == view.model_dump()
    assert report.totals[0].carried_in == D("1400.00") and report.totals[0].available == D("2400.00")


def test_window_reaches_back_to_the_chain_start() -> None:
    food = budget("food", "1000", [G])
    assert RO.window([food], [], TODAY) == (date(2026, 11, 1), date(2026, 11, 30))
    assert RO.window([food], [terms(date(2026, 8, 1), RolloverMode.CAPPED, cap="10")], TODAY) == (date(2026, 8, 1), date(2026, 11, 30))
    assert RO.window([food], [terms(date(2026, 8, 1))], TODAY) == (date(2026, 11, 1), date(2026, 11, 30))


def test_safe_to_spend_keeps_back_what_the_rollover_carried_in() -> None:
    owned = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u"}
    bank = Account(**owned, id="bank", name="Current", kind=AccountKind.BANK, currency="AED", created_at=NOW, updated_at=NOW)
    food = budget("food", "1000", [G], reserve_in_safe_to_spend=True)
    view = STS.compute(today=TODAY, base_currency="AED", timezone="Asia/Dubai", accounts=[bank], balances={"bank": D("5000")}, bills=[], debts=[],
                       goals=[], plan=PlanSettings(), rates=[], budgets=[food], month_rows=THREE_MONTHS,
                       rollovers=[terms(date(2026, 8, 1), RolloverMode.SURPLUS)])
    assert view.currencies[0].planned == D("1200.00")


# -- the routes ---------------------------------------------------------------------------------------------


def _body(**extra: object) -> dict:
    return {"name": "Food", "currency": "AED", "amount": "1000", "categories": ["groceries"], **extra}


def _context() -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=OWNER["x-user-id"], roles=["owner"])


def test_routes_save_the_setting_from_the_current_period_and_keep_it_on_an_edit() -> None:
    assert client.post(f"{API}/budgets/plans", headers=OWNER, json=_body(rollover="capped")).status_code == 422  # a cap is needed
    assert client.post(f"{API}/budgets/plans", headers=OWNER, json=_body(rollover="capped", rollover_cap="0")).status_code == 422
    made = client.post(f"{API}/budgets/plans", headers=OWNER, json=_body(rollover="capped", rollover_cap="150", overspend="carry"))
    assert made.status_code == 201, made.text
    line = made.json()
    assert (line["rollover"], line["rollover_cap"], line["overspend"]) == ("capped", "150.00", "carry")
    assert line["carried_in"] == "0.00" and line["available"] == "1000.00" and line["chain_periods"] == 1
    start = date.fromisoformat(line["period_start"])
    history = finance_store.get_planning(_context()).budget_rollovers
    assert [(t.effective_from, t.rollover, t.cap) for t in history] == [(start, RolloverMode.CAPPED, D("150.00"))]
    # An edit that leaves the rollover out (an older app) keeps it; the same period is rewritten, never an earlier one.
    kept = client.patch(f"{API}/budgets/plans/{line['id']}", headers=OWNER, json=_body(amount="1200"))
    assert kept.status_code == 200 and kept.json()["rollover"] == "capped" and kept.json()["available"] == "1200.00"
    history = finance_store.get_planning(_context()).budget_rollovers
    assert len(history) == 1 and history[0].amount == D("1200.00")
    # Once money carries, the period stays: a weekly budget from a monthly one is refused.
    assert client.patch(f"{API}/budgets/plans/{line['id']}", headers=OWNER, json=_body(period="weekly")).status_code == 422
    # Only spending budgets carry.
    assert client.post(f"{API}/budgets/plans", headers=OWNER,
                       json={"name": "Home", "currency": "AED", "amount": "500", "kind": "remittance", "rollover": "surplus"}).status_code == 422
    # A budget that never carried keeps no history.
    plain = client.post(f"{API}/budgets/plans", headers=OWNER, json=_body(name="Plain")).json()
    assert plain["rollover"] == "none" and all(t.budget_id != plain["id"] for t in finance_store.get_planning(_context()).budget_rollovers)
    assert client.delete(f"{API}/budgets/plans/{line['id']}", headers=OWNER).status_code == 204
    assert finance_store.get_planning(_context()).budget_rollovers == []


def test_routes_carry_last_months_balance_from_real_rows() -> None:
    bank = account(currency="AED")
    made = client.post(f"{API}/budgets/plans", headers=OWNER, json=_body(rollover="surplus")).json()
    start = date.fromisoformat(made["period_start"])
    previous = (start - timedelta(days=1)).replace(day=1)
    # The rollover was on since last month (synthetic history), and last month 700 was spent: 300 carries in.
    planning = finance_store.get_planning(_context())
    planning.budget_rollovers = [planning.budget_rollovers[0].model_copy(update={"effective_from": previous})]
    finance_store.save_planning(_context(), planning)
    days_back = (date.today() - previous).days - 3
    txn(bank["id"], "-700.00", "POS CARREFOUR CITY", days=days_back)
    view = client.get(f"{API}/budgets/plans", headers=OWNER).json()["budgets"][0]
    assert view["carried_in"] == "300.00" and view["available"] == "1300.00"
    assert [p["rollover"] for p in view["chain"]] == ["surplus", "surplus"] and view["chain"][0]["carried_out"] == "300.00"
    # Another person in the workspace, a viewer and another workspace see none of it and change nothing.
    assert client.get(f"{API}/budgets/plans", headers=OTHER_PERSON).json()["budgets"] == []
    assert client.patch(f"{API}/budgets/plans/{made['id']}", headers=OTHER_PERSON, json=_body(rollover="none")).status_code in (403, 404)
    assert client.get(f"{API}/budgets/plans", headers=VIEWER).status_code == 403
    other = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=OTHER_PERSON["x-user-id"], roles=["member"])
    assert finance_store.get_planning(other).budget_rollovers == []
    # Switching it off now leaves last month as it was and stops carrying from this month on.
    off = client.patch(f"{API}/budgets/plans/{made['id']}", headers=OWNER, json=_body(rollover="none")).json()
    assert off["rollover"] == "none" and off["carried_in"] == "300.00" and off["chain"][0]["rollover"] == "surplus"
    assert [t.rollover for t in RO.history_for(made["id"], finance_store.get_planning(_context()).budget_rollovers)] == [RolloverMode.SURPLUS,
                                                                                                                        RolloverMode.NONE]
