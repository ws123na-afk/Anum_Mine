"""Authenticated, non-persistent broker statement preview and reconciliation API.

POST endpoints receive user-supplied snapshots, never broker credentials. They
perform validation and calculations in memory only. No deposits, real orders,
broker fetch, approval bypass or cross-account operations are possible.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import tenant_context
from ..schemas import TenantContext
from .api import _read
from .broker_import import parse_broker_statement_csv
from .broker_readiness import BrokerReadiness, BrokerReadinessResult, evaluate_broker_readiness
from .broker_reconciliation import (
    BrokerDataError, BrokerReconciliation, BrokerSnapshot,
    ReconciliationTolerance, reconcile_snapshots,
)

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
    facts: BrokerReadiness


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


@router.post("/readiness", response_model=BrokerReadinessResult)
def readiness(
    request: ReadinessRequest, context: TenantContext = Depends(tenant_context),
) -> BrokerReadinessResult:
    _read(context)
    return evaluate_broker_readiness(request.facts)
