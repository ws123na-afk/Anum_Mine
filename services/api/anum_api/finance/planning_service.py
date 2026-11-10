"""The Plan view's and the assistant's planning answers, read from the person's own records
(docs/finance.md#planning-and-advice). Advice only: ANUM never moves money, pays, buys or transfers.

``load`` reads everything in short owner-scoped units of work (the links first, in their own unit, as
``obligations.load`` requires), never on the event loop: callers run it with ``asyncio.to_thread`` or from a
synchronous route. The answers are worked out by the pure ``planner`` and ``personal`` functions. Live figures
(prices, financing rates, remittance fees) come only from ``live_research``, which runs the registered
``web_search`` tool through the runtime's policy (``business.pipeline.call_tool``): every figure carries the
address it was read from and the date; nothing found is "unknown", never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from ..schemas import TenantContext, utc_now
from . import commitment_math as M
from . import obligations
from . import personal as P
from . import planner as PL
from . import salary as SAL
from .advisor import SPENDABLE, AdvisorInputs, build_note
from .models import Account, AccountKind, Bill, Category, Debt, DebtKind, Goal, GoalPurpose
from .money import quantize


@dataclass
class PlanContext:
    today: date
    base_currency: str
    accounts: list[Account]
    balances: dict[str, Decimal]
    inputs: AdvisorInputs
    bills: list[Bill]
    debts: list[Debt]  # with today's balances
    goals: list[Goal]
    commitments: list[P.Commitment]
    skipped: list[P.Commitment]
    remittances: list[P.Remittance]
    safe: dict[str, Decimal]  # currency: safe to spend this month
    fx_fees: dict[str, tuple[Decimal, int]] = field(default_factory=dict)  # account currency: (fees paid, payments) in 400 days
    late_fees: dict[str, tuple[Decimal, int]] = field(default_factory=dict)  # currency: (late fees, count) in 400 days
    salary_profile: P.SalaryProfile | None = None
    payslips: list[P.Payslip] = field(default_factory=list)
    # The provider statements' installments and the links (bill matches, debt payments), for the forecast.
    installments: list = field(default_factory=list)
    links: obligations.Links | None = None


_LATE = re.compile(r"(?<![\w])(late|overdue|penalty|غرامة|تأخير)(?![\w])", re.IGNORECASE)


def load(context: TenantContext, *, now: datetime | None = None) -> PlanContext:
    from .advisor_jobs import gather
    from .personal_store import open_personal_store
    from .safe_to_spend import build_safe_to_spend
    from .sms_store import open_sms_stores

    inputs = gather(context, now=now)  # links in their own unit, then one store unit
    links = obligations.load(context)
    with open_sms_stores(context) as (finance, sms):
        safe = build_safe_to_spend(finance, context, now=now, links=links)
        goals = obligations.current_goals([g for g in finance.list_items(context, "goal") if isinstance(g, Goal)], inputs.accounts, inputs.balances)
        planning = finance.get_planning(context)
        entries = sms.list_entries(context, start=inputs.today - timedelta(days=400), end=inputs.today, limit=20_000)
    with open_personal_store(context) as store:
        remittances = store.list(context, "remittance")
    fx: dict[str, tuple[Decimal, int]] = {}
    for entry in entries:
        if entry.fx_fee and entry.fx_fee > 0:
            currency = next((p.currency for p in entry.postings if p.account_id), None)
            if currency:
                total, count = fx.get(currency, (Decimal(0), 0))
                fx[currency] = (total + entry.fx_fee, count + 1)
    late: dict[str, tuple[Decimal, int]] = {}
    for t in inputs.transactions:
        if t.amount < 0 and t.category == Category.FEES and _LATE.search(t.description):
            total, count = late.get(t.currency, (Decimal(0), 0))
            late[t.currency] = (total - t.amount, count + 1)
    counted, skipped = P.fixed_commitments(inputs.bills, inputs.debts, planning.installment_entries, remittances, inputs.today)
    return PlanContext(
        today=inputs.today, base_currency=inputs.base_currency, accounts=inputs.accounts, balances=inputs.balances, inputs=inputs,
        bills=inputs.bills, debts=inputs.debts, goals=goals, commitments=counted, skipped=skipped, remittances=remittances,
        safe={c.currency: c.safe_to_spend for c in safe.currencies}, fx_fees=fx, late_fees=late,
        salary_profile=links.salary_profile if isinstance(links.salary_profile, P.SalaryProfile) else None, payslips=links.payslips,
        installments=list(planning.installment_entries), links=links,
    )


# -- figures from the context -----------------------------------------------------------------------


def liquid(ctx: PlanContext, currency: str) -> Decimal:
    return M.liquid(ctx.accounts, ctx.balances, currency)


def income(ctx: PlanContext, currency: str) -> SAL.MonthlyIncome:
    """The one monthly income figure (``salary.monthly_income``): the salary profile's expected net, else the
    average of the last three full months of records."""
    return SAL.monthly_income(currency=currency, today=ctx.today, transactions=ctx.inputs.transactions, accounts=ctx.accounts,
                              lending_ids=ctx.inputs.lending_ids, profile=ctx.salary_profile, payslips=ctx.payslips)


def income_monthly(ctx: PlanContext, currency: str) -> Decimal | None:
    return income(ctx, currency).amount


def essential_monthly(ctx: PlanContext, currency: str) -> Decimal | None:
    note = build_note(ctx.inputs)
    block = next((c for c in note.currencies if c.currency == currency), None)
    if block is None:
        return None
    scenario = next((s for s in block.runway.scenarios if s.kind == "essential"), None) or next(
        (s for s in block.runway.scenarios if s.kind == "bare"), None)
    return scenario.monthly_outflow if scenario is not None and scenario.monthly_outflow > 0 else None


def commitments_monthly(ctx: PlanContext, currency: str) -> Decimal:
    return P.commitment_totals(ctx.commitments).get(currency, Decimal(0))


def emergency_saved(ctx: PlanContext, currency: str) -> Decimal:
    return sum((g.saved_amount for g in ctx.goals if g.currency == currency and g.purpose == GoalPurpose.EMERGENCY), Decimal(0))


# -- how to pay and what to avoid -------------------------------------------------------------------


@dataclass
class PayWay:
    account_id: str
    name: str
    kind: str
    currency: str
    balance: Decimal
    note: str  # covers, short, card_interest, card_no_rate, card, fx


def how_to_pay(ctx: PlanContext, currency: str, price: Decimal) -> list[PayWay]:
    """Which of the person's accounts could pay: bank and cash in the price's currency first (those that cover
    it), then cards (with the rate the person entered, never assumed), then accounts in another currency (FX)."""
    out: list[PayWay] = []
    rates = {d.account_id: d.apr_percent for d in ctx.debts if d.account_id}
    for a in ctx.accounts:
        if a.archived or a.kind in (AccountKind.LOAN, AccountKind.INVESTMENT):
            continue
        balance = quantize(M.account_balance(a, ctx.balances), a.currency)
        if a.currency != currency:
            if a.kind in SPENDABLE or a.kind == AccountKind.CARD:
                out.append(PayWay(a.id, a.name, a.kind.value, a.currency, balance, "fx"))
            continue
        if a.kind in SPENDABLE:
            out.append(PayWay(a.id, a.name, a.kind.value, a.currency, balance, "covers" if balance >= price else "short"))
        elif a.kind == AccountKind.CARD:
            rate = rates.get(a.id)
            out.append(PayWay(a.id, a.name, a.kind.value, a.currency, balance, "card_interest" if rate and rate > 0 else "card_no_rate"))
    order = {"covers": 0, "card_interest": 1, "card_no_rate": 1, "short": 2, "fx": 3}
    out.sort(key=lambda w: (order.get(w.note, 9), -w.balance, w.name.casefold()))
    return out


@dataclass
class Avoid:
    code: str  # fx_fees, late_fees, card_interest, commitments_high, emergency_low
    currency: str | None
    params: dict[str, str]


def what_to_avoid(ctx: PlanContext, currency: str, *, monthly_after: Decimal | None = None, income: Decimal | None = None) -> list[Avoid]:
    """Only what the person's own records show: FX fees they paid, late fees, card interest at their rate,
    commitments over half of income, an emergency fund below three months of essentials."""
    out: list[Avoid] = []
    for cur, (total, count) in sorted(ctx.fx_fees.items()):
        out.append(Avoid("fx_fees", cur, {"amount": str(quantize(total, cur)), "count": str(count)}))
    for cur, (total, count) in sorted(ctx.late_fees.items()):
        out.append(Avoid("late_fees", cur, {"amount": str(quantize(total, cur)), "count": str(count)}))
    for d in ctx.debts:
        if d.kind == DebtKind.CARD and d.currency == currency and d.balance > 0 and d.apr_percent > 0:
            out.append(Avoid("card_interest", currency, {"name": d.name, "rate": str(d.apr_percent.normalize())}))
    if monthly_after is not None and income and income > 0 and monthly_after / income > PL.TIGHT_RATIO:
        out.append(Avoid("commitments_high", currency, {"percent": str((monthly_after * 100 / income).quantize(Decimal(1)))}))
    essential = essential_monthly(ctx, currency)
    if essential is not None and emergency_saved(ctx, currency) < essential * PL.RESERVE_MONTHS:
        out.append(Avoid("emergency_low", currency, {"saved": str(quantize(emergency_saved(ctx, currency), currency)),
                                                     "target": str(quantize(essential * PL.RESERVE_MONTHS, currency))}))
    return out


def afford(ctx: PlanContext, currency: str, price: Decimal, monthly_cost: Decimal | None) -> PL.Affordability:
    return PL.affordability(
        currency=currency, price=price, monthly_cost=monthly_cost, safe_to_spend=ctx.safe.get(currency), liquid=liquid(ctx, currency),
        income_monthly=income_monthly(ctx, currency), commitments_monthly=commitments_monthly(ctx, currency),
        essential_monthly=essential_monthly(ctx, currency),
    )


def plan_debts(ctx: PlanContext, currency: str) -> list[PL.PlanDebt]:
    return [PL.PlanDebt(id=d.id, name=d.name, balance=d.balance, apr_percent=d.apr_percent, minimum=M.plan_minimum(d))
            for d in ctx.debts if d.currency == currency and d.balance > 0]


def debt_currencies(ctx: PlanContext) -> list[str]:
    return sorted({d.currency for d in ctx.debts if d.balance > 0}, key=lambda c: (c != ctx.base_currency, c))


def planned_payment(context: TenantContext, currency: str) -> Decimal | None:
    """The monthly payment saved on the Debts plan for this currency (None: minimums only)."""
    from .store import open_finance_store

    with open_finance_store(context) as store:
        plan = next((p for p in store.get_planning(context).debt_plans if p.currency == currency), None)
    return plan.monthly_payment if plan is not None else None


# -- live research ----------------------------------------------------------------------------------

LiveStatus = Literal["ok", "unavailable", "blocked", "failed", "not_found"]
_PERCENT = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s?%")
_AMOUNT = re.compile(r"\b(AED|SAR|USD|EUR|GBP|INR|PKR|PHP|EGP|BDT|LKR|NPR|QAR|KWD|OMR|BHD|JOD)\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)\b")


@dataclass
class LiveFigure:
    kind: Literal["percent", "amount"]
    value: str
    currency: str | None = None
    # A percent whose own clause names an interest or profit rate and no fee (never sent to clients).
    rate: bool = False


@dataclass
class LiveItem:
    title: str
    url: str
    read_on: date
    figures: list[LiveFigure]


@dataclass
class LiveResearch:
    status: LiveStatus
    query: str
    read_on: date
    items: list[LiveItem] = field(default_factory=list)
    message: str = ""


_CLAUSE_END = re.compile(r"[;,،|\n()]|\.\s")
_RATE_WORDS = re.compile(r"\b(?:interest|apr|rates?|loans?|financing|profit|p\.a|per annum|annual|flat|reducing)\b|معدل|فائدة|ربح|سنوي", re.IGNORECASE)
_FEE_WORDS = re.compile(r"\b(?:fees?|charges?|processing|admin|commission|insurance|cashback|discount|down|deposit|vat|tax|off)\b|رسوم|عمولة|خصم|ضريبة",
                        re.IGNORECASE)


def _clause(text: str, start: int, end: int) -> str:
    """The clause around text[start:end]: from the last separator before it to the first one after it."""
    before = [m.end() for m in _CLAUSE_END.finditer(text, 0, start)]
    after = _CLAUSE_END.search(text, end)
    return text[before[-1] if before else 0 : after.start() if after else len(text)]


def figures_in(text: str) -> list[LiveFigure]:
    found: list[LiveFigure] = []
    for match in _PERCENT.finditer(text):
        clause = _clause(text, match.start(), match.end())
        found.append(LiveFigure("percent", match.group(1), rate=bool(_RATE_WORDS.search(clause)) and not _FEE_WORDS.search(clause)))
    for match in _AMOUNT.finditer(text):
        found.append(LiveFigure("amount", match.group(2).replace(",", ""), match.group(1)))
    unique: list[LiveFigure] = []
    for f in found:
        same = next((u for u in unique if (u.kind, u.value, u.currency) == (f.kind, f.value, f.currency)), None)
        if same is None:
            unique.append(f)
        elif f.rate and not same.rate:
            unique[unique.index(same)] = f
    return unique[:6]


async def live_research(context: TenantContext, query: str) -> LiveResearch:
    """One ``web_search`` call under the runtime's policy (audited). Figures found in a result's title and
    snippet are returned with that result's address and today's date; a result with no figure is kept so the
    person can read it. No web search: ``unavailable``, and the caller continues with the person's own data."""
    from ..agent_tools import ToolCall
    from ..business.pipeline import call_tool
    from ..business.research import _registry as registry
    from ..web_search import WEB_SEARCH_TOOL

    text = " ".join(query.split())[:300]
    today = utc_now().date()
    if not text:
        return LiveResearch("failed", text, today, message="Say what to look up.")
    if registry is None or WEB_SEARCH_TOOL not in registry.names:
        return LiveResearch("unavailable", text, today, message="Live prices are unavailable: web search is not set up on this server.")
    status, result, summary = await call_tool(context, ToolCall(name=WEB_SEARCH_TOOL, arguments={"query": text}), "finance_plan")
    if status != "done" or result is None:
        return LiveResearch("blocked" if status == "blocked" else "failed", text, today, message=summary)
    items: list[LiveItem] = []
    for row in (result.output or {}).get("results", [])[:5]:
        url = str(row.get("url") or "")
        if not url.startswith(("https://", "http://")):
            continue
        items.append(LiveItem(title=str(row.get("title") or url)[:200], url=url[:2048], read_on=today,
                              figures=figures_in(f"{row.get('title') or ''} {row.get('snippet') or ''}")))
    if not items:
        return LiveResearch("not_found", text, today, message="Nothing was found, so the figure is unknown.")
    return LiveResearch("ok", text, today, items=items)


def live_rates(research: LiveResearch, limit: int = 3) -> list[tuple[Decimal, str, date]]:
    """Distinct interest or profit rates found, each with the address it was read from and the date (at most
    ``limit``). A percent whose clause does not name a rate, or names a fee, is shown to the person but never
    costed as a loan rate: "1% processing fee" is not a 1% loan."""
    out: list[tuple[Decimal, str, date]] = []
    for item in research.items:
        for f in item.figures:
            if f.kind != "percent" or not f.rate:
                continue
            value = Decimal(f.value)
            if Decimal(0) < value <= Decimal(60) and all(v != value for v, _, _ in out):
                out.append((value, item.url, item.read_on))
            if len(out) >= limit:
                return out
    return out


__all__ = ["Avoid", "LiveFigure", "LiveItem", "LiveResearch", "PayWay", "PlanContext", "afford", "commitments_monthly", "debt_currencies",
           "emergency_saved", "essential_monthly", "figures_in", "how_to_pay", "income_monthly", "liquid", "live_rates", "live_research", "load",
           "plan_debts", "planned_payment", "what_to_avoid"]
