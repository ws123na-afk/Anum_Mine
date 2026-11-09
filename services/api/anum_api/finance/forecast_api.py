"""Routes for the cash-flow forecast and the emergency reserve under ``/api/v1/finance``
(docs/finance.md#forecast, #emergency-reserve), and the forecast's producer for the Suggestions queue.

* ``GET /forecast``: per currency, day by day to the end of this month and over three calendar months, from
  today's bank and cash balance, the expected salary, bills, debt minimums, installments, regular remittances and
  the average spending (``forecast.py``). ``finance:read``.
* ``GET /reserve``: each emergency reserve (a goal with the purpose ``emergency``), what it holds, the progress and
  how many months of essential spending it covers, plus a suggested target for currencies without one.
  ``finance:read``. The reserve is saved through the goals routes (``POST``, ``PATCH /goals``), never here.

Both read the person's own records in short owner-scoped units of work (``planning_service.load``), in a plain
``def`` route (FastAPI's thread pool, never the event loop), and write nothing. ANUM only advises.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends

from ..dependencies import tenant_context
from ..schemas import TenantContext
from . import forecast as F
from . import planner as PL
from . import planning_service as S
from . import suggestions as SG
from .api import _read
from .models import Goal

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


def inputs_from(ctx: S.PlanContext) -> F.ForecastInputs:
    """The forecast's inputs from the planning context: the same records every other Finance figure reads."""
    links = ctx.links
    return F.ForecastInputs(
        today=ctx.today, timezone=ctx.inputs.timezone, base_currency=ctx.base_currency, accounts=ctx.accounts, balances=ctx.balances,
        transactions=ctx.inputs.transactions, bills=ctx.bills, debts=ctx.debts, goals=ctx.goals,
        bill_links=ctx.inputs.bill_links, debt_payments=links.debt_payments if links is not None else [], installments=ctx.installments,
        remittances=ctx.remittances, lending_ids=ctx.inputs.lending_ids, salary_profile=ctx.salary_profile, payslips=ctx.payslips,
        payslip_credits=ctx.inputs.payslip_credits,
    )


def build_forecast(context: TenantContext, *, now: datetime | None = None) -> F.Forecast:
    return F.build(inputs_from(S.load(context, now=now)))


def essentials(ctx: S.PlanContext) -> dict[str, tuple[Decimal | None, str]]:
    """Per currency, the monthly essential spending of the advisor's runway (bills, minimums and the essential
    categories), or the bare minimum (bills and minimums) when there is not enough history."""
    from .advisor import build_note

    out: dict[str, tuple[Decimal | None, str]] = {}
    for block in build_note(ctx.inputs).currencies:
        chosen = next((s for s in block.runway.scenarios if s.kind == "essential"), None)
        basis = "essential"
        if chosen is None:
            chosen = next((s for s in block.runway.scenarios if s.kind == "bare"), None)
            basis = "bare"
        out[block.currency] = (chosen.monthly_outflow, basis) if chosen is not None and chosen.monthly_outflow > 0 else (None, "none")
    return out


def build_reserves(context: TenantContext, *, now: datetime | None = None) -> F.Reserves:
    ctx = S.load(context, now=now)
    goals = [g for g in ctx.goals if isinstance(g, Goal)]
    return F.reserves(goals, ctx.accounts, essentials(ctx), PL.RESERVE_MONTHS)


@router.get("/forecast", response_model=F.Forecast)
def forecast(context: TenantContext = Depends(tenant_context)) -> F.Forecast:
    """Where your bank and cash money is expected to be, day by day, to the end of this month and three months out."""
    _read(context)
    return build_forecast(context)


@router.get("/reserve", response_model=F.Reserves)
def reserve(context: TenantContext = Depends(tenant_context)) -> F.Reserves:
    """Your emergency reserve: target, what it holds, progress and the months of essential spending it covers."""
    _read(context)
    return build_reserves(context)


# -- the Suggestions queue --------------------------------------------------------------------------------


def produce(snap: SG.Snapshot) -> list[SG.Suggestion]:
    """A cash shortage the forecast sees coming: the expected balance going below zero or below the emergency
    reserve target, with the date. Reads in its own short units of work; never writes."""
    result = build_forecast(snap.context, now=snap.now)
    out: list[SG.Suggestion] = []
    for row in result.currencies:
        for warning in row.warnings:
            if warning.kind not in ("below_zero", "below_reserve"):
                continue
            below_reserve = warning.kind == "below_reserve"
            month = f"{warning.on.year:04d}-{warning.on.month:02d}"
            out.append(SG.Suggestion(
                key=f"cash_shortage:forecast:{row.currency}:{warning.kind}:{month}", producer="forecast", kind="cash_shortage",
                detected=(f"Your {row.currency} money is expected to go below your emergency reserve on {warning.on.isoformat()}." if below_reserve
                          else f"Your {row.currency} money is expected to go below zero on {warning.on.isoformat()}."),
                why=("The forecast takes your bills, minimum payments, installments and usual spending off today's balance and adds your expected salary."),
                params={"currency": row.currency, "reason": warning.kind, "date": warning.on.isoformat(), "balance": str(warning.balance),
                        "threshold": str(warning.threshold), "month": month},
                evidence=[SG.Evidence(label="Forecast", value=f"{warning.on.isoformat()} {row.currency} {warning.balance}")],
                effect=[SG.Effect(amount=abs(warning.threshold - warning.balance), currency=row.currency, direction="out",
                                  note="below_reserve" if below_reserve else "shortfall")],
                action=SG.ProposedAction(kind="acknowledge"), href="#finance-safe", occurred_on=warning.on,
            ))
    return out


SG.register_producer("forecast", produce)
