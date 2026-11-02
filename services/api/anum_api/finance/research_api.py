"""Routes for symbol research under ``/api/v1/finance/research`` (docs/finance-research.md#api).

``finance:read`` reads research, runs backtests and asks for an AI summary (they change nothing
real). Turning a backtest into a paper buy plan changes the owner's IBKR settings, so it also needs
the workspace owner, like every IBKR change. Routes are plain ``def``: outside sources and the model
are called in a worker thread with no database session open; each audit row is written in its own
short unit of work afterwards and carries codes only (never a symbol, amount or figure).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import UnitsOfWork, tenant_context, units_of_work
from ..schemas import TenantContext, utc_now
from . import research_service as svc
from .api import _audit, _read
from .ibkr_api import _owner
from .ibkr_service import BrokerRefusal
from .research_backtest import BacktestInput

router = APIRouter(prefix="/api/v1/finance/research", tags=["finance"])


def _record(units: UnitsOfWork, context: TenantContext, action: str, outcome: str, meta: dict[str, Any] | None = None) -> None:
    with units(context) as repository:
        if outcome == "success":
            _audit(repository, context, action, "finance_research", meta)
        else:
            from ..audit import AuditRecord
            from ..schemas import new_id

            repository.record_audit(AuditRecord(id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                                                actor=context.user_id, action=action, target="finance_research", outcome="denied",
                                                correlation_id="finance_research", created_at=utc_now(), metadata=meta or {}))


def _call(units: UnitsOfWork, context: TenantContext, action: str, fn: Any, *args: Any, audit: bool = True, **kwargs: Any) -> Any:
    try:
        result = fn(*args, **kwargs)
    except BrokerRefusal as exc:
        if audit:
            _record(units, context, action, "denied", {"code": exc.code})
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    if audit:
        _record(units, context, action, "success", {"outcome": result.get("status")} if isinstance(result, dict) and "status" in result else None)
    return result


@router.get("/symbols")
def list_symbols(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _read(context)
    return svc.symbols_view(context, utc_now())


@router.get("/portfolio")
def get_portfolio(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    _read(context)
    return svc.portfolio(context, utc_now())


@router.get("/symbols/{symbol}")
def get_research(symbol: str, refresh: bool = Query(default=False), context: TenantContext = Depends(tenant_context),
                 units: UnitsOfWork = Depends(units_of_work)) -> dict[str, Any]:
    _read(context)
    return _call(units, context, "finance.research.read", svc.research, context, symbol, utc_now(), refresh=refresh, audit=refresh)


@router.post("/symbols/{symbol}/summary")
def create_summary(symbol: str, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> dict[str, Any]:
    _read(context)
    return _call(units, context, "finance.research.summary", svc.summarise, context, symbol, utc_now())


@router.post("/backtests")
def run_backtest(body: BacktestInput, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> dict[str, Any]:
    _read(context)
    return _call(units, context, "finance.research.backtest", svc.backtest, context, body, utc_now(), audit=False)


class PaperRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=10)
    amount: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    day_of_month: int = Field(ge=1, le=28)


@router.post("/paper-run", status_code=201)
def create_paper_run(body: PaperRunInput, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> dict[str, Any]:
    _owner(context)
    return _call(units, context, "finance.research.paper_run", svc.paper_run, context, body.symbol, body.amount, body.day_of_month, utc_now())
