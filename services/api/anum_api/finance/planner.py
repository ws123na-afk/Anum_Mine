"""Planning arithmetic for the money advisor's Plan view and the assistant's planning tools
(docs/finance.md#planning-and-advice). Advice only: ANUM never moves money.

Pure functions over figures already read; no store, clock or network. Every figure is an estimate from
the person's own records or from a live source the caller names (its address and the date it was read):

* **Payoff order** (``payoff``): avalanche (highest rate first) and snowball (smallest balance first), month
  by month in minor units like a lender's statement: a twelfth of the yearly rate on each balance is added
  (rounded half to even to the minor unit), every debt gets its minimum (at most what it owes), the rest of
  the monthly payment goes in strategy order and a paid-off debt frees its minimum for the next one. The
  one payoff engine: the Debts page, the advisor's Plan view, the assistant and the mobile app all read it
  through ``GET /planner/payoff``.
* **Amortisation** (``amortised``): standard annuity payment ``P x r / (1 - (1 + r)^-n)`` with ``r`` the
  yearly rate over 12, or, for a flat rate (common for UAE car and personal finance), profit
  ``P x rate x years`` spread evenly. A rate is never invented: none means the option cannot be costed.
* **Affordability** (``affordability``): the price against Safe to spend, money in bank and cash, the
  monthly income average and the fixed commitments, with ANUM's rules of thumb (stated as such): monthly
  commitments up to a third of income are comfortable, up to half are tight, over half are too much;
  keeping at least three months of essential spending after a cash purchase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal

from .money import minor_digits, quantize

MAX_MONTHS = 600
OUTLOOK_MONTHS = 12  # month-end balances returned for the outlook chart
COMFORTABLE_RATIO = Decimal("0.33")
TIGHT_RATIO = Decimal("0.5")
RESERVE_MONTHS = 3

Strategy = Literal["avalanche", "snowball"]


@dataclass(frozen=True)
class PlanDebt:
    id: str
    name: str
    balance: Decimal
    apr_percent: Decimal
    minimum: Decimal


@dataclass
class PayoffResult:
    strategy: Strategy
    months: int | None  # None: the payments never catch up with the interest (or 50 years pass)
    total_interest: Decimal
    payment: Decimal  # the monthly payment used (the minimums together when none is planned or it is below them)
    below_minimums: bool
    order: list[str]  # debt ids in the order extra money goes
    paid_off_in: dict[str, int | None]
    balances: list[Decimal] = field(default_factory=list)  # total owed at the end of each month, month 1 first (at most ``horizon``)


def _unit(currency: str) -> Decimal:
    return Decimal(1).scaleb(-minor_digits(currency))


def order_for(debts: list[PlanDebt], strategy: Strategy) -> list[PlanDebt]:
    def key(d: PlanDebt) -> tuple:
        rate, balance = -d.apr_percent, d.balance
        first, second = (rate, balance) if strategy == "avalanche" else (balance, rate)
        return (first, second, d.name.casefold(), d.id)

    return sorted(debts, key=key)


def simulate(debts: list[PlanDebt], strategy: Strategy, currency: str, payment: Decimal | None, horizon: int = OUTLOOK_MONTHS) -> PayoffResult:
    unit = _unit(currency)
    minimums = sum((d.minimum for d in debts), Decimal(0))
    below = payment is not None and payment < minimums
    budget = None if payment is None or below else payment
    order = order_for([d for d in debts if d.balance > 0], strategy)
    owed = {d.id: d.balance for d in order}
    paid_off: dict[str, int | None] = {d.id: None for d in order}
    interest = Decimal(0)
    month = 0
    total = sum(owed.values(), Decimal(0))
    balances: list[Decimal] = []
    while total > 0 and month < MAX_MONTHS:
        month += 1
        for d in order:
            if owed[d.id] <= 0:
                continue
            added = (owed[d.id] * d.apr_percent / 1200).quantize(unit, rounding=ROUND_HALF_EVEN)
            interest += added
            owed[d.id] += added
        left = budget if budget is not None else None
        for d in order:
            if owed[d.id] <= 0:
                continue
            pay = min(d.minimum, owed[d.id]) if left is None else min(d.minimum, owed[d.id], left)
            owed[d.id] -= pay
            if left is not None:
                left -= pay
        if left is not None:
            for d in order:
                if left <= 0:
                    break
                if owed[d.id] <= 0:
                    continue
                pay = min(owed[d.id], left)
                owed[d.id] -= pay
                left -= pay
        for d in order:
            if paid_off[d.id] is None and owed[d.id] <= 0:
                paid_off[d.id] = month
        total = sum(owed.values(), Decimal(0))
        if month <= horizon:
            balances.append(quantize(total, currency))
    return PayoffResult(strategy=strategy, months=month if total <= 0 else None, total_interest=quantize(interest, currency),
                        payment=quantize(budget if budget is not None else minimums, currency), below_minimums=below,
                        order=[d.id for d in order], paid_off_in=paid_off, balances=balances)


@dataclass
class PayoffComparison:
    currency: str
    avalanche: PayoffResult
    snowball: PayoffResult
    # "avalanche" saves interest; "same" when both cost the same; None when neither ends.
    cheaper: Literal["avalanche", "snowball", "same"] | None
    interest_saved: Decimal  # by the cheaper order
    first_win_months: int | None  # snowball: months until the first debt is gone (the quick win)


def payoff(debts: list[PlanDebt], currency: str, payment: Decimal | None) -> PayoffComparison:
    """Both orders with ``payment`` a month (``None``: each debt gets only its minimum, nothing rolls over)."""
    a = simulate(debts, "avalanche", currency, payment)
    s = simulate(debts, "snowball", currency, payment)
    cheaper: Literal["avalanche", "snowball", "same"] | None
    if a.months is None and s.months is None:
        cheaper, saved = None, Decimal(0)
    elif s.months is None or (a.months is not None and a.total_interest < s.total_interest):
        cheaper, saved = "avalanche", s.total_interest - a.total_interest if s.months is not None else Decimal(0)
    elif a.months is None or s.total_interest < a.total_interest:
        cheaper, saved = "snowball", a.total_interest - s.total_interest if a.months is not None else Decimal(0)
    else:
        cheaper, saved = "same", Decimal(0)
    wins = [m for m in s.paid_off_in.values() if m is not None]
    return PayoffComparison(currency=currency, avalanche=a, snowball=s, cheaper=cheaper, interest_saved=quantize(saved, currency),
                            first_win_months=min(wins) if wins else None)


# -- amortisation and options -------------------------------------------------------------------------

RateBasis = Literal["reducing", "flat"]


@dataclass(frozen=True)
class Amortised:
    monthly: Decimal
    months: int
    total_paid: Decimal  # every monthly payment
    interest: Decimal  # interest or profit: total paid less what was borrowed


def amortised(principal: Decimal, annual_percent: Decimal, months: int, currency: str, basis: RateBasis = "reducing") -> Amortised:
    """The monthly payment, rounded up to the minor unit so the loan is covered, and what it costs in all."""
    if months < 1:
        raise ValueError("months must be at least 1")
    unit = _unit(currency)
    with localcontext() as ctx:
        ctx.prec = 40
        if principal <= 0:
            payment = Decimal(0)
        elif basis == "flat":
            profit = principal * annual_percent / 100 * Decimal(months) / 12
            payment = (principal + profit) / months
        elif annual_percent == 0:
            payment = principal / months
        else:
            r = annual_percent / 1200
            payment = principal * r / (1 - (1 + r) ** -months)
    monthly = payment.quantize(unit, rounding=ROUND_CEILING)
    total = monthly * months
    return Amortised(monthly=monthly, months=months, total_paid=total, interest=quantize(max(total - principal, Decimal(0)), currency))


OptionKind = Literal["cash", "bank_loan", "installments"]


@dataclass
class OptionInput:
    kind: OptionKind
    label: str
    months: int = 1
    rate_percent: Decimal | None = None  # None with a loan: unknown, the option is not costed
    basis: RateBasis = "reducing"
    fees: Decimal = Decimal(0)  # arrangement, processing or late fees the person names
    down_payment: Decimal = Decimal(0)
    rate_source: str = "owner"  # "owner", or the address of the live source read
    rate_read_on: str | None = None


@dataclass
class OptionResult:
    kind: OptionKind
    label: str
    upfront: Decimal | None
    monthly: Decimal | None
    months: int
    total_cost: Decimal | None  # everything paid: down payment, monthly payments and fees
    interest: Decimal | None  # interest or profit
    extra_over_cash: Decimal | None
    rate_percent: Decimal | None
    basis: RateBasis
    rate_source: str
    rate_read_on: str | None
    status: Literal["ok", "rate_unknown"]


def compare(price: Decimal, currency: str, options: list[OptionInput]) -> list[OptionResult]:
    """Each way to pay, cheapest in total first; a loan without a known rate is listed as unknown, never guessed."""
    out: list[OptionResult] = []
    for o in options:
        if o.kind == "cash":
            total = quantize(price + o.fees, currency)
            out.append(OptionResult("cash", o.label, total, None, 0, total, Decimal(0), quantize(o.fees, currency), None, "reducing", o.rate_source, None, "ok"))
            continue
        financed = max(price - o.down_payment, Decimal(0))
        rate = o.rate_percent  # never assumed: installments without stated terms are unknown too
        if rate is None:
            out.append(OptionResult(o.kind, o.label, quantize(o.down_payment, currency), None, o.months, None, None, None, None, o.basis, o.rate_source,
                                    o.rate_read_on, "rate_unknown"))
            continue
        a = amortised(financed, rate, max(o.months, 1), currency, o.basis)
        total = quantize(o.down_payment + a.total_paid + o.fees, currency)
        out.append(OptionResult(o.kind, o.label, quantize(o.down_payment + o.fees, currency), a.monthly, a.months, total, a.interest,
                                quantize(total - price, currency), rate, o.basis, o.rate_source, o.rate_read_on, "ok"))
    out.sort(key=lambda r: (r.total_cost is None, r.total_cost or Decimal(0)))  # stable: equal totals keep the order given
    return out


# -- affordability --------------------------------------------------------------------------------------


Verdict = Literal["yes_from_cash", "yes_from_savings", "cash_leaves_little", "monthly_comfortable", "monthly_tight", "too_much", "not_enough_data"]


@dataclass
class Affordability:
    currency: str
    price: Decimal
    monthly_cost: Decimal | None
    safe_to_spend: Decimal | None
    liquid: Decimal
    income_monthly: Decimal | None  # average of the last full months; None: no income recorded
    commitments_monthly: Decimal
    essential_monthly: Decimal | None
    ratio_now: Decimal | None  # commitments / income
    ratio_after: Decimal | None  # with the new monthly cost
    months_left_after_cash: Decimal | None  # money left after paying cash, in months of essential spending
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)  # codes the clients word


def affordability(*, currency: str, price: Decimal, monthly_cost: Decimal | None, safe_to_spend: Decimal | None, liquid: Decimal,
                  income_monthly: Decimal | None, commitments_monthly: Decimal, essential_monthly: Decimal | None) -> Affordability:
    reasons: list[str] = []
    ratio_now = (commitments_monthly / income_monthly).quantize(Decimal("0.01")) if income_monthly and income_monthly > 0 else None
    ratio_after = ((commitments_monthly + (monthly_cost or Decimal(0))) / income_monthly).quantize(Decimal("0.01")) \
        if income_monthly and income_monthly > 0 else None
    left = liquid - price
    months_left = (left / essential_monthly).quantize(Decimal("0.1")) if essential_monthly and essential_monthly > 0 else None
    verdict: Verdict
    if monthly_cost is None:
        if safe_to_spend is not None and price <= safe_to_spend:
            verdict = "yes_from_cash"
            reasons.append("within_safe_to_spend")
        elif price <= liquid and (months_left is None or months_left >= RESERVE_MONTHS):
            verdict = "yes_from_savings"
            reasons.append("above_safe_to_spend" if safe_to_spend is not None else "within_savings")
        elif price <= liquid:
            verdict = "cash_leaves_little"
            reasons.append("reserve_below_three_months")
        else:
            verdict = "too_much"
            reasons.append("more_than_you_have")
        if income_monthly is None:
            reasons.append("no_income_recorded")
    else:
        if ratio_after is None:
            verdict = "not_enough_data"
            reasons.append("no_income_recorded")
        elif ratio_after <= COMFORTABLE_RATIO:
            verdict = "monthly_comfortable"
        elif ratio_after <= TIGHT_RATIO:
            verdict = "monthly_tight"
            reasons.append("commitments_over_a_third")
        else:
            verdict = "too_much"
            reasons.append("commitments_over_half")
    return Affordability(currency=currency, price=price, monthly_cost=monthly_cost, safe_to_spend=safe_to_spend, liquid=liquid,
                         income_monthly=income_monthly, commitments_monthly=commitments_monthly, essential_monthly=essential_monthly,
                         ratio_now=ratio_now, ratio_after=ratio_after, months_left_after_cash=months_left, verdict=verdict, reasons=reasons)


__all__ = ["Affordability", "Amortised", "MAX_MONTHS", "OptionInput", "OptionResult", "PayoffComparison", "PayoffResult", "PlanDebt", "affordability",
           "amortised", "compare", "order_for", "payoff", "simulate"]
