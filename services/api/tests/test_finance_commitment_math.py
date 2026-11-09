"""One bill, one debt, one number: Fixed costs, Safe to spend, the advisor and the planner read the same
building blocks (anum_api/finance/commitment_math.py, docs/finance.md#how-figures-are-counted)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal as D

from anum_api.finance import advisor as A
from anum_api.finance import commitment_math as M
from anum_api.finance import personal as P
from anum_api.finance import safe_to_spend as S
from anum_api.finance.models import Account, AccountKind, Bill, Cadence, Category, Debt, DebtKind, PlanSettings

STAMP = datetime(2026, 11, 1, tzinfo=UTC)
TODAY = date(2026, 11, 5)
OWNED = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "created_at": STAMP, "updated_at": STAMP}


def bill(name: str, amount: str, cadence: Cadence = Cadence.MONTHLY, due: date = date(2026, 11, 20), **over: object) -> Bill:
    return Bill(id=f"b_{name}", name=name, amount=D(amount), currency="AED", cadence=cadence, next_due_on=due, category=Category.UTILITIES,
                **OWNED, **over)  # type: ignore[arg-type]


def debt(name: str, balance: str, minimum: str, due_day: int | None = 25, kind: DebtKind = DebtKind.LOAN, **over: object) -> Debt:
    return Debt(id=f"d_{name}", name=name, kind=kind, balance=D(balance), currency="AED", minimum_payment=D(minimum), due_day=due_day,
                **OWNED, **over)  # type: ignore[arg-type]


BANK = Account(id="a1", name="Current", kind=AccountKind.BANK, currency="AED", opening_balance=D("5000"), **OWNED)  # type: ignore[arg-type]


def test_monthly_equivalent_is_one_rule_with_one_off_left_out() -> None:
    assert M.monthly_equivalent(D(120), Cadence.WEEKLY) == D(520)
    assert M.monthly_equivalent(D(300), Cadence.QUARTERLY) == D(100)
    assert M.monthly_equivalent(D(1200), Cadence.YEARLY) == D(100)
    assert M.monthly_equivalent(D(900), Cadence.ONCE) is None
    assert M.bill_monthly(bill("Visa", "900", Cadence.ONCE)) == 0


def test_debt_minimum_never_exceeds_what_is_owed() -> None:
    assert M.debt_minimum(debt("Card", "80", "100")) == D(80)
    assert M.debt_minimum(debt("Loan", "2000", "500")) == D(500)
    assert M.debt_minimum(debt("Paid", "0", "500")) == 0
    assert M.debt_minimum(debt("None", "900", "0")) == 0


def test_liquid_counts_bank_and_cash_only() -> None:
    card = Account(id="a2", name="Card", kind=AccountKind.CARD, currency="AED", opening_balance=D("-300"), **OWNED)  # type: ignore[arg-type]
    old = Account(id="a3", name="Old", kind=AccountKind.CASH, currency="AED", opening_balance=D("50"), archived=True, **OWNED)  # type: ignore[arg-type]
    assert M.liquid([BANK, card, old], {"a1": D("-1000")}, "AED") == D(4000)


def test_the_same_bill_and_debt_give_the_same_figure_everywhere() -> None:
    bills = [bill("Rent", "3000"), bill("Gym", "120", Cadence.WEEKLY, due=date(2026, 11, 12)), bill("Car loan", "1500")]
    debts = [debt("Car loan", "20000", "1500"), debt("Card", "80", "100", kind=DebtKind.CARD), debt("Personal", "9000", "700")]

    counted, skipped = P.fixed_commitments(bills, debts, [], [], TODAY)
    fixed = {c.ref_id: c.monthly for c in counted}
    safe = S.compute(today=TODAY, base_currency="AED", timezone="Asia/Dubai", accounts=[BANK], balances={}, bills=bills, debts=debts, goals=[],
                     plan=PlanSettings(), rates=[])
    safe_lines = {line.ref_id: -line.amount for line in safe.currencies[0].lines if line.kind == "minimum_payment"}
    coming = A._coming(TODAY, bills, debts, set(), [], "AED")
    coming_minimums = {i.ref_id: i.amount for i in coming.items if i.kind in ("minimum", "card_minimum")}
    runway = A._runway("AED", TODAY, [BANK], {}, bills, debts, {}, "none", [], None, 0)

    # A debt's minimum is the same number in Fixed costs, Safe to spend and the advisor's coming payments.
    for ref in ("d_Card", "d_Personal"):
        assert fixed[ref] == safe_lines[ref] == coming_minimums[ref] == M.debt_minimum(next(d for d in debts if d.id == ref))
    # The car loan's minimum is saved as a bill: counted once, as the bill, by every reader.
    assert ("d_Car loan", "in_bill") in {(c.ref_id, c.note) for c in skipped}
    assert "d_Car loan" not in safe_lines and "d_Car loan" not in coming_minimums
    assert any(e.ref_id == "d_Car loan" and e.note == "minimum_in_bill" for e in safe.excluded)
    # Monthly figures: Fixed costs and the advisor's runway count bills and minimums the same way.
    bills_total = sum((M.bill_monthly(b) for b in bills), D(0))
    minimums_total = M.debt_minimum(debts[1]) + M.debt_minimum(debts[2])
    assert runway.bills_monthly == M.monthly_equivalent(D(3000), Cadence.MONTHLY) + D("520.00") + D(1500) == bills_total
    assert runway.minimums_monthly == minimums_total
    assert P.commitment_totals(counted)["AED"] == bills_total + minimums_total
    # Money you have is the same liquid figure Safe to spend and the runway start from.
    assert safe.currencies[0].available == runway.liquid == M.liquid([BANK], {}, "AED")


def test_one_off_bill_counts_on_its_due_date_but_not_as_a_monthly_cost() -> None:
    once = bill("Visa fee", "900", Cadence.ONCE, due=date(2026, 11, 20))
    counted, skipped = P.fixed_commitments([once], [], [], [], TODAY)
    assert counted == [] and [(c.ref_id, c.note) for c in skipped] == [("b_Visa fee", "once")]
    safe = S.compute(today=TODAY, base_currency="AED", timezone="Asia/Dubai", accounts=[BANK], balances={}, bills=[once], debts=[], goals=[],
                     plan=PlanSettings(), rates=[])
    assert safe.currencies[0].bills == D(900)
    assert A._runway("AED", TODAY, [BANK], {}, [once], [], {}, "none", [], None, 0).bills_monthly == 0
