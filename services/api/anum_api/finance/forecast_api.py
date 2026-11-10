"""Routes for the cash-flow forecast and the emergency reserve under ``/api/v1/finance``
(docs/finance.md#forecast, #emergency-reserve), and the forecast's producer for the Suggestions queue.

* ``GET /forecast``: per currency, day by day to the end of this month and over three calendar months, from
  today's bank and cash balance, the expected salary, bills, debt minimums, installments, regular remittances and
  the average spending (``forecast.py``). ``finance:read``.
* ``GET /reserve``: each emergency reserve (a goal with the purpose ``emergency``), what it holds, the progress and
  how many months of essential spending it covers, plus a suggested target for currencies without one.
  ``finance:read``. The reserve is saved through the goals routes (``POST``, ``PATCH /goals``), never here.

* ``GET|POST /expected-income``, ``PATCH|DELETE /expected-income/{id}``: one-off income the person expects on a date
  (a bonus, a refund, a sale), which the forecast counts until it is marked received (``received_on``). Owner only
  (``finance_expected_income``, migration ``0158_finance_depth``); reading needs ``finance:read``, writing
  ``finance:manage``.

The forecast and the reserve read the person's own records in short owner-scoped units of work
(``planning_service.load``), in a plain ``def`` route (FastAPI's thread pool, never the event loop), and write nothing.
ANUM only advises.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import forecast as F
from . import planner as PL
from . import planning_service as S
from . import suggestions as SG
from . import personal as P
from .api import Amount, _audit, _clean, _currency, _invalid, _manage, _money, _not_found, _read
from .models import Goal
from .personal_store import MAX_ROWS, open_personal_store

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


def expected_income(context: TenantContext) -> list[P.ExpectedIncome]:
    with open_personal_store(context) as store:
        return [i for i in store.list(context, "expected_income") if isinstance(i, P.ExpectedIncome)]


def build_forecast(context: TenantContext, *, now: datetime | None = None) -> F.Forecast:
    inputs = inputs_from(S.load(context, now=now))
    return F.build(replace(inputs, expected_income=expected_income(context)))


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


# -- expected one-off income -------------------------------------------------------------------------------


class ExpectedIncomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    amount: Amount
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    expected_on: date
    received_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class ExpectedIncomeView(BaseModel):
    id: str
    label: str
    amount: Decimal
    currency: str
    expected_on: date
    received_on: date | None
    notes: str | None
    state: str  # expected, late (the date passed and it is not marked received) or received
    created_at: Any
    updated_at: Any


class ExpectedIncomePage(BaseModel):
    items: list[ExpectedIncomeView]
    total: int
    limit: int
    offset: int
    note: str = "Money you expect once. The forecast counts it on its date until you mark it received. ANUM never moves money."


def _today(context: TenantContext) -> tuple[str, date]:
    from .personal_api import _settings

    return _settings(context)


def _income_view(item: P.ExpectedIncome, today: date) -> ExpectedIncomeView:
    state = "received" if item.received_on is not None else "late" if item.expected_on < today else "expected"
    return ExpectedIncomeView(**item.model_dump(exclude={"tenant_id", "workspace_id", "owner_user_id"}), state=state)


def _build_income(context: TenantContext, body: ExpectedIncomeInput, existing: P.ExpectedIncome | None, base: str) -> P.ExpectedIncome:
    label = _clean(body.label, 120)
    if not label:
        raise _invalid("Say what the income is, for example a bonus or a refund.")
    currency = _currency(body.currency, base)
    now = utc_now()
    return P.ExpectedIncome(
        id=existing.id if existing else new_id("fin_xinc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        owner_user_id=context.user_id, label=label, amount=_money(body.amount, currency, field="Amount", positive=True, allow_zero=False),
        currency=currency, expected_on=body.expected_on, received_on=body.received_on, notes=_clean(body.notes, 1000),
        created_at=existing.created_at if existing else now, updated_at=now,
    )


@router.get("/expected-income", response_model=ExpectedIncomePage)
def list_expected_income(state: str = Query(default="open", pattern="^(open|received|all)$"), limit: int = Query(default=25, ge=1, le=100),
                         offset: int = Query(default=0, ge=0, le=100_000), context: TenantContext = Depends(tenant_context)) -> ExpectedIncomePage:
    """One-off income you expect, soonest first: still expected (``open``, the default), received, or all."""
    _read(context)
    _base, today = _today(context)
    rows = sorted(expected_income(context), key=lambda i: (i.expected_on, i.id))
    views = [_income_view(i, today) for i in rows]
    shown = [v for v in views if state == "all" or (state == "received") == (v.state == "received")]
    return ExpectedIncomePage(items=shown[offset:offset + limit], total=len(shown), limit=limit, offset=offset)


@router.post("/expected-income", response_model=ExpectedIncomeView, status_code=status.HTTP_201_CREATED)
def create_expected_income(body: ExpectedIncomeInput, context: TenantContext = Depends(tenant_context),
                           repository: AnumRepository = Depends(repository_context)) -> ExpectedIncomeView:
    _manage(context)
    base, today = _today(context)
    item = _build_income(context, body, None, base)
    with open_personal_store(context) as store:
        if store.count(context, "expected_income") >= MAX_ROWS:
            raise _invalid(f"You can keep up to {MAX_ROWS} of these. Delete old ones first.")
        store.save("expected_income", item)
    _audit(repository, context, "finance.expected_income.create", item.id)
    return _income_view(item, today)


@router.patch("/expected-income/{item_id}", response_model=ExpectedIncomeView)
def update_expected_income(item_id: str, body: ExpectedIncomeInput, context: TenantContext = Depends(tenant_context),
                           repository: AnumRepository = Depends(repository_context)) -> ExpectedIncomeView:
    """Replace it with the full body sent (mark it received with ``received_on``)."""
    _manage(context)
    base, today = _today(context)
    with open_personal_store(context) as store:
        existing = store.get(context, "expected_income", item_id)
        if not isinstance(existing, P.ExpectedIncome):
            raise _not_found("Expected income")
        item = _build_income(context, body, existing, base)
        store.save("expected_income", item)
    _audit(repository, context, "finance.expected_income.update", item_id)
    return _income_view(item, today)


@router.delete("/expected-income/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_expected_income(item_id: str, context: TenantContext = Depends(tenant_context),
                           repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_personal_store(context) as store:
        if not store.delete(context, "expected_income", item_id):
            raise _not_found("Expected income")
    _audit(repository, context, "finance.expected_income.delete", item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
