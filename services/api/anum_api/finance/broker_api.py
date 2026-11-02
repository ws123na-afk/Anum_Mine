"""Authenticated, non-persistent broker statement preview and reconciliation API.

POST endpoints receive user-supplied snapshots, never broker credentials. They
perform validation and calculations in memory only. No deposits, real orders,
broker fetch, approval bypass or cross-account operations are possible.

``/readiness`` is the live eligibility preflight: it also reads the person's own
live preconditions (read only) and records an audit row with ids only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext
from . import gateway
from .api import _audit, _read
from .broker_import import parse_broker_statement_csv
from .broker_analytics import StatementAnalytics, analyze_broker_statement
from .broker_history import StatementChangeReport, compare_broker_history
from .broker_paper import PaperSimulationRequest, PaperSimulationResult, simulate_paper_trade
from .broker_readiness import BrokerProfile, BrokerReadiness, LivePreflightResult, evaluate_live_preflight
from .broker_reconciliation import (
    BrokerDataError, BrokerReconciliation, BrokerSnapshot,
    ReconciliationTolerance, reconcile_snapshots,
)
from .store import open_finance_store

router = APIRouter(prefix="/api/v1/finance/broker", tags=["finance"])


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=512000)


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: BrokerSnapshot
    observed: BrokerSnapshot
    tolerance: ReconciliationTolerance = Field(default_factory=ReconciliationTolerance)
    max_snapshot_age_seconds: int = Field(default=900, ge=0, le=86400)


class ReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: BrokerReadiness = Field(default_factory=BrokerReadiness)
    profile: BrokerProfile | None = None


@router.post("/statement-preview", response_model=BrokerSnapshot)
def statement_preview(
    request: PreviewRequest, context: TenantContext = Depends(tenant_context),
) -> BrokerSnapshot:
    _read(context)
    try:
        return parse_broker_statement_csv(request.statement.encode("utf-8"))
    except BrokerDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reconcile", response_model=BrokerReconciliation)
def reconcile(
    request: ReconcileRequest, context: TenantContext = Depends(tenant_context),
) -> BrokerReconciliation:
    _read(context)
    try:
        return reconcile_snapshots(
            request.baseline, request.observed,
            tolerance=request.tolerance,
            max_snapshot_age_seconds=request.max_snapshot_age_seconds,
        )
    except (BrokerDataError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/readiness", response_model=LivePreflightResult)
def readiness(
    request: ReadinessRequest,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> LivePreflightResult:
    """Live eligibility preflight: what is still missing before live could ever be considered.

    Read only. It changes no setting, connects nothing and sends no order; the answer
    always says live trading is off and zero orders were sent. The audit row holds ids
    only (no country, assets or ticked facts).
    """
    _read(context)
    with open_finance_store(context) as store:
        system = gateway.preconditions(store, context)
    result = evaluate_live_preflight(request.facts, request.profile, system)
    _audit(repository, context, "finance.broker.live_preflight", context.user_id)
    return result


@router.post("/analyze", response_model=StatementAnalytics)
def analyze(
    snapshot: BrokerSnapshot, context: TenantContext = Depends(tenant_context),
) -> StatementAnalytics:
    """Analyze uploaded positions without any broker transaction or persistent write."""
    _read(context)
    return analyze_broker_statement(snapshot)


@router.post("/history", response_model=StatementChangeReport)
def history(
    request: ReconcileRequest, context: TenantContext = Depends(tenant_context),
) -> StatementChangeReport:
    """Explain differences in two statements without manufacturing investment returns."""
    _read(context)
    try:
        return compare_broker_history(request.baseline, request.observed)
    except BrokerDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/paper/simulate", response_model=PaperSimulationResult)
def paper_simulate(
    request: PaperSimulationRequest,
    context: TenantContext = Depends(tenant_context),
) -> PaperSimulationResult:
    """Stateless, authenticated offline math; no trades, account changes or funding."""
    _read(context)
    return simulate_paper_trade(request)
