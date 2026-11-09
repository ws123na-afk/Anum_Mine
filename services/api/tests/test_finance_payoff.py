"""The one payoff engine (anum_api/finance/planner.py, docs/finance.md#debts-and-goals): the Debts page, the
advisor's Plan view, the assistant and the mobile app all read it through ``GET /planner/payoff``. These are
the rules the web's own engine used to check before it was removed, now checked where the engine lives."""

from __future__ import annotations

from decimal import Decimal as D

from anum_api.finance import planner as PL


def debt(i: str, balance: str, apr: str, minimum: str) -> PL.PlanDebt:
    return PL.PlanDebt(i, i, D(balance), D(apr), D(minimum))


def test_avalanche_takes_the_highest_rate_first_snowball_the_smallest_balance() -> None:
    big, small = debt("big", "3000", "24", "10"), debt("small", "500", "6", "10")
    assert [d.id for d in PL.order_for([small, big], "avalanche")] == ["big", "small"]
    assert [d.id for d in PL.order_for([big, small], "snowball")] == ["small", "big"]


def test_without_interest_the_months_are_the_balance_over_the_payment() -> None:
    assert PL.simulate([debt("a", "1000", "0", "100")], "avalanche", "AED", None).months == 10
    assert PL.simulate([debt("a", "1000", "0", "100")], "avalanche", "AED", D("250")).months == 4


def test_interest_is_a_twelfth_of_the_yearly_rate_rounded_once() -> None:
    plan = PL.simulate([debt("a", "1200", "12", "0")], "avalanche", "AED", D("1212"))
    assert (plan.months, plan.total_interest, plan.balances) == (1, D("12.00"), [D("0.00")])


def test_a_paid_off_debt_frees_its_minimum_for_the_next_one() -> None:
    plan = PL.simulate([debt("a", "100", "0", "50"), debt("b", "300", "0", "50")], "snowball", "AED", D("100"))
    assert plan.months == 4
    assert plan.balances == [D("300.00"), D("200.00"), D("100.00"), D("0.00")]
    assert plan.paid_off_in == {"a": 2, "b": 4}


def test_minimums_only_never_roll_over() -> None:
    # Without a payment each debt gets only its own minimum: B's freed 50 does not go to A.
    plan = PL.simulate([debt("a", "100", "0", "50"), debt("b", "300", "0", "50")], "snowball", "AED", None)
    assert (plan.months, plan.paid_off_in) == (6, {"a": 2, "b": 6})


def test_avalanche_costs_less_when_the_large_debt_has_the_high_rate() -> None:
    debts = [debt("card", "15500", "30", "400"), debt("car", "9000", "4", "1000")]
    a, s = PL.simulate(debts, "avalanche", "AED", D("3000")), PL.simulate(debts, "snowball", "AED", D("3000"))
    assert a.months is not None and s.months is not None and a.total_interest < s.total_interest


def test_payments_that_never_catch_up_with_the_interest_never_end() -> None:
    assert PL.simulate([debt("a", "1000", "24", "10")], "avalanche", "AED", None).months is None


def test_a_planned_payment_below_the_minimums_pays_the_minimums() -> None:
    plan = PL.simulate([debt("a", "1000", "0", "100"), debt("b", "1000", "0", "100")], "avalanche", "AED", D("50"))
    assert (plan.below_minimums, plan.payment, plan.months) == (True, D("200.00"), 10)


def test_paying_more_each_month_is_sooner_and_cheaper() -> None:
    debts = [debt("a", "1200", "0", "100")]
    base, more = PL.simulate(debts, "avalanche", "AED", None), PL.simulate(debts, "avalanche", "AED", D("200"))
    assert (base.months, more.months) == (12, 6)
    charged = [debt("a", "5000", "18", "150")]
    base, more = PL.simulate(charged, "avalanche", "AED", D("200")), PL.simulate(charged, "avalanche", "AED", D("250"))
    assert base.months is not None and more.months is not None and more.months < base.months and more.total_interest < base.total_interest


def test_month_end_balances_stop_at_the_outlook_horizon() -> None:
    plan = PL.simulate([debt("a", "10000", "0", "100")], "avalanche", "AED", None)
    assert plan.months == 100 and len(plan.balances) == PL.OUTLOOK_MONTHS and plan.balances[0] == D("9900.00")
