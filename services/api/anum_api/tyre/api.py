"""HTTP API for tyre planning: ``/api/v1/tyre`` (docs/tyre-planning.md#api).

Roles: every member reads (``tyre:read``); members and owners sync, generate and
schedule reports, request delivery and acknowledge alerts (``tyre:operate``); only owners
connect the data source and change the mapping and assumptions (``tyre:manage``).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import delivery
from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import service
from .analytics import (
    AlertKind,
    Confidence,
    FleetSummary,
    ForecastState,
    PurchasePlan,
    TyreForecast,
    fleet_summary,
    purchase_plan,
)
from .fleet import OperationsSummary, operations_summary
from .mapping import PRESETS, SourceTable, fit_to_schema
from .model import ENTITY_FIELDS, Assumptions, Entity, FieldSpec, SourceKind, TyreMapping, dataset_counts
from .reports import summary_text, to_csv, to_pdf
from .sources import (
    TyreSourceError,
    normalize_supabase_url,
    parse_postgres_url,
    parse_reader_login,
    parse_service_account,
    refuse_privileged_key,
)
from .store import (
    AlertStatus,
    ConnectionStatus,
    ReportKind,
    TyreAlert,
    TyreConnection,
    TyreReport,
    open_tyre_store,
)

router = APIRouter(prefix="/api/v1/tyre", tags=["tyre-planning"])

OWNER_ONLY = "Only workspace owners can connect tyre data and change the mapping or assumptions. Ask an owner."
OPERATORS_ONLY = "Viewers can read tyre planning. Ask a workspace member or owner to sync, report or acknowledge."
REPORT_ATTACHMENT_KIND = "tyre_report"


def _require(context: TenantContext, permission: Permission, message: str) -> None:
    if not has_permission(context, permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _read(context: TenantContext) -> None:
    _require(context, Permission.TYRE_READ, "Your role cannot read tyre planning in this workspace.")


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str,
           metadata: dict[str, Any] | None = None, outcome: str = "success") -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action=action, target=target, outcome=outcome,
            correlation_id=target, created_at=utc_now(), metadata=metadata or {},
        )
    )


def _conflict(exc: LookupError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _source_error(exc: TyreSourceError, code: int = status.HTTP_502_BAD_GATEWAY) -> HTTPException:
    detail = exc.message + (f" {exc.hint}" if exc.hint else "")
    return HTTPException(status_code=code, detail=detail)


# --- setup -----------------------------------------------------------------------------


class ConnectionView(BaseModel):
    kind: SourceKind
    label: str
    endpoint: str
    schema_name: str
    credential_configured: bool
    secret_hint: str | None
    status: ConnectionStatus
    last_tested_at: datetime | None
    last_error: str | None
    updated_at: datetime
    updated_by: str


class DiscoveryView(BaseModel):
    tables: list[SourceTable]
    discovered_at: datetime | None
    suggestion: TyreMapping | None = None
    writable_tables: list[str] = Field(default_factory=list)


class SyncView(BaseModel):
    synced_at: datetime
    synced_by: str
    duration_ms: int
    entities: dict[str, Any]
    counts: dict[str, int]


class SetupView(BaseModel):
    connection: ConnectionView | None
    mapping: TyreMapping | None
    mapping_problems: list[str]
    assumptions: Assumptions
    discovery: DiscoveryView | None
    sync: SyncView | None
    fields: dict[str, list[FieldSpec]]
    presets: list[str]
    can_manage: bool
    can_operate: bool
    delivery_channels: list[str]


def _connection_view(connection: TyreConnection) -> ConnectionView:
    return ConnectionView(
        kind=connection.kind, label=connection.label, endpoint=connection.endpoint,
        schema_name=connection.schema_name, credential_configured=bool(connection.secret_ciphertext),
        secret_hint=connection.secret_hint, status=connection.status, last_tested_at=connection.last_tested_at,
        last_error=connection.last_error, updated_at=connection.updated_at, updated_by=connection.updated_by,
    )


def _discovery_view(connection: TyreConnection) -> DiscoveryView | None:
    if connection.discovery is None:
        return None
    return DiscoveryView(
        tables=connection.discovery, discovered_at=connection.discovered_at,
        writable_tables=[table.name for table in connection.discovery if table.writable],
    )


def _setup(context: TenantContext) -> SetupView:
    with open_tyre_store(context) as store:
        connection = store.get_connection(context)
        snapshot = store.get_snapshot(context) if connection else None
    sync = None
    if snapshot is not None:
        sync = SyncView(
            synced_at=snapshot.stats.synced_at, synced_by=snapshot.synced_by, duration_ms=snapshot.stats.duration_ms,
            entities={key: value.model_dump() for key, value in snapshot.stats.entities.items()},
            counts=dataset_counts(snapshot.dataset),
        )
    mapping = connection.mapping if connection else None
    return SetupView(
        connection=_connection_view(connection) if connection else None,
        mapping=mapping,
        mapping_problems=mapping.problems() if mapping else [],
        assumptions=connection.assumptions if connection else Assumptions(),
        discovery=_discovery_view(connection) if connection else None,
        sync=sync,
        fields={entity.value: specs for entity, specs in ENTITY_FIELDS.items()},
        presets=sorted(PRESETS),
        can_manage=has_permission(context, Permission.TYRE_MANAGE),
        can_operate=has_permission(context, Permission.TYRE_OPERATE),
        delivery_channels=sorted(delivery.channels()),
    )


@router.get("/setup", response_model=SetupView)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> SetupView:
    """Connection, mapping, assumptions, last discovery and last sync of the workspace."""
    _read(context)
    return _setup(context)


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    label: str = Field(default="TyrePulse", min_length=1, max_length=120)
    # Supabase sign-in and REST: the project URL. Firestore: the database id ("(default)"). PostgreSQL: unused.
    endpoint: str = Field(default="", max_length=500)
    schema_name: str = Field(default="public", min_length=1, max_length=63, pattern=r"^[a-z_][a-z0-9_]*$")
    # The connection string, API key, service account JSON or, for Supabase sign-in, the JSON
    # {"api_key", "email", "password"} of the dedicated reader. Write-only; omit to keep the saved one.
    secret: str | None = Field(default=None, min_length=1, max_length=20_000)


def _validated_endpoint(payload: ConnectionInput, secret: str | None) -> str:
    try:
        if payload.kind == SourceKind.SUPABASE_POSTGRES:
            return parse_postgres_url(secret).display if secret else ""
        if payload.kind == SourceKind.SUPABASE_REST:
            if secret:
                refuse_privileged_key(secret)
            return normalize_supabase_url(payload.endpoint)
        if payload.kind == SourceKind.SUPABASE_AUTH:
            if secret:
                parse_reader_login(secret)
            return normalize_supabase_url(payload.endpoint)
        if secret:
            parse_service_account(secret)
        database = payload.endpoint.strip() or "(default)"
        if not re.fullmatch(r"\(default\)|[a-z][a-z0-9-]{3,62}", database):
            raise TyreSourceError("The Firestore database id is not valid.")
        return database
    except TyreSourceError as exc:
        raise _source_error(exc, status.HTTP_422_UNPROCESSABLE_CONTENT) from None


@router.put("/connection", response_model=SetupView)
async def save_connection(
    payload: ConnectionInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SetupView:
    """Save where the data is and the read-only credential (encrypted at rest)."""
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    secret = payload.secret.strip() if payload.secret else None
    with open_tyre_store(context) as store:
        existing = store.get_connection(context)
    if secret is None and (existing is None or existing.kind != payload.kind or not existing.secret_ciphertext):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Enter the credential for this kind of connection.")
    endpoint = _validated_endpoint(payload, secret)
    now = utc_now()
    if existing is None:
        connection = TyreConnection(
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, kind=payload.kind,
            created_by=context.user_id, updated_by=context.user_id, created_at=now, updated_at=now,
        )
    else:
        connection = existing
        if connection.kind != payload.kind:
            connection.discovery = connection.discovered_at = None
    connection.kind, connection.label, connection.schema_name = payload.kind, payload.label.strip(), payload.schema_name
    if endpoint or payload.kind != SourceKind.SUPABASE_POSTGRES:
        connection.endpoint = endpoint
    if secret is not None:
        connection.secret_ciphertext = service.encrypt_secret(secret)
        connection.secret_hint = service.secret_hint(payload.kind, secret)
    connection.status, connection.last_error = ConnectionStatus.UNTESTED, None
    connection.updated_by, connection.updated_at = context.user_id, now
    with open_tyre_store(context) as store:
        store.save_connection(connection)
    _audit(repository, context, "tyre.connection.saved", "tyre_connection",
           {"kind": payload.kind.value, "credential_changed": secret is not None})
    return _setup(context)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Forget the connection, credential, mapping, synced data and alerts (reports stay)."""
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    with open_tyre_store(context) as store:
        deleted = store.delete_connection(context)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No tyre data connection to remove.")
    _audit(repository, context, "tyre.connection.deleted", "tyre_connection")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TestView(BaseModel):
    ok: bool
    message: str
    setup: SetupView


@router.post("/connection/test", response_model=TestView)
async def test_connection(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TestView:
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    try:
        connection, message = await service.test_connection(context)
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    ok = connection.status == ConnectionStatus.OK
    _audit(repository, context, "tyre.connection.tested", "tyre_connection", outcome="success" if ok else "failure")
    return TestView(ok=ok, message=message, setup=_setup(context))


@router.post("/discover", response_model=DiscoveryView)
async def discover(context: TenantContext = Depends(tenant_context)) -> DiscoveryView:
    """List the source's tables (or collections) and columns, with a suggested mapping."""
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    try:
        result = await service.discover(context)
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    except TyreSourceError as exc:
        raise _source_error(exc) from None
    return DiscoveryView(
        tables=result.tables, discovered_at=result.discovered_at, suggestion=result.suggestion,
        writable_tables=[table.name for table in result.tables if table.writable],
    )


@router.get("/presets/{name}", response_model=TyreMapping)
async def get_preset(name: str, context: TenantContext = Depends(tenant_context)) -> TyreMapping:
    """A ready-made mapping, fitted to the last discovered schema when there is one."""
    _read(context)
    preset = PRESETS.get(name)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such preset.")
    with open_tyre_store(context) as store:
        connection = store.get_connection(context)
    if connection and connection.discovery:
        return fit_to_schema(preset, connection.discovery)
    return preset


@router.put("/mapping", response_model=SetupView)
async def save_mapping(
    payload: TyreMapping,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SetupView:
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    try:
        connection = service.get_connection(context)
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    connection.mapping = payload
    connection.updated_by, connection.updated_at = context.user_id, utc_now()
    with open_tyre_store(context) as store:
        store.save_connection(connection)
    _audit(repository, context, "tyre.mapping.saved", "tyre_mapping",
           {"entities": sorted(entity.value for entity in payload.entities), "preset": payload.preset})
    return _setup(context)


class PreviewView(BaseModel):
    problems: list[str]
    stats: dict[str, Any]
    sample: dict[str, list[dict[str, Any]]]


@router.post("/mapping/preview", response_model=PreviewView)
async def preview_mapping(payload: TyreMapping, context: TenantContext = Depends(tenant_context)) -> PreviewView:
    """Apply a draft mapping to the first rows of each table without saving it."""
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    try:
        return PreviewView(**await service.preview(context, payload))
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    except TyreSourceError as exc:
        raise _source_error(exc) from None


@router.put("/assumptions", response_model=SetupView)
async def save_assumptions(
    payload: Assumptions,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SetupView:
    """Change thresholds and plan assumptions; alerts are re-evaluated on the synced data."""
    _require(context, Permission.TYRE_MANAGE, OWNER_ONLY)
    try:
        connection = service.get_connection(context)
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    connection.assumptions = payload
    connection.updated_by, connection.updated_at = context.user_id, utc_now()
    with open_tyre_store(context) as store:
        store.save_connection(connection)
        snapshot = store.get_snapshot(context)
    if snapshot is not None:
        service.reevaluate_alerts(context, snapshot.dataset, connection)
    _audit(repository, context, "tyre.assumptions.saved", "tyre_assumptions",
           {"min_tread_mm": payload.min_tread_mm, "horizon_months": payload.horizon_months})
    return _setup(context)


class SyncResultView(BaseModel):
    setup: SetupView
    new_alerts: int


@router.post("/sync", response_model=SyncResultView)
async def sync(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SyncResultView:
    """Read the mapped tables now and recompute forecasts and alerts."""
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    try:
        outcome = await service.sync(context, context.user_id)
    except service.NotConnectedError as exc:
        raise _conflict(exc) from None
    except TyreSourceError as exc:
        _audit(repository, context, "tyre.sync", "tyre_snapshot", {"error": exc.message[:200]}, outcome="failure")
        raise _source_error(exc, status.HTTP_502_BAD_GATEWAY if "mapping" not in exc.message else status.HTTP_409_CONFLICT) from None
    _audit(repository, context, "tyre.sync", "tyre_snapshot", {**outcome.counts, "new_alerts": len(outcome.raised)})
    return SyncResultView(setup=_setup(context), new_alerts=len(outcome.raised))


# --- dashboard, forecasts, plan -------------------------------------------------------------


def _loaded(context: TenantContext) -> tuple[service.Loaded, list[TyreForecast]]:
    _read(context)
    try:
        return service.forecasts_for(context)
    except (service.NotConnectedError, service.NotSyncedError) as exc:
        raise _conflict(exc) from None


class DashboardView(BaseModel):
    summary: FleetSummary
    # Vehicles inspected, schedules, checklists, work orders (docs/tyre-planning.md#fleet-operations-data).
    operations: OperationsSummary
    synced_at: datetime
    alert_counts: dict[str, int]
    top_alerts: list[TyreAlert]
    due_soon: list[TyreForecast]
    due_by_month: dict[str, int]


@router.get("/dashboard", response_model=DashboardView)
async def dashboard(context: TenantContext = Depends(tenant_context)) -> DashboardView:
    loaded, forecasts = _loaded(context)
    today = utc_now().date()
    summary = fleet_summary(loaded.snapshot.dataset, loaded.connection.assumptions, today, forecasts)
    with open_tyre_store(context) as store:
        counts = store.alert_counts(context)
        top, _ = store.list_alerts(context, status="open", kind=None, query=None, limit=5, offset=0)
    by_month: dict[str, int] = {}
    horizon = today + timedelta(days=366)
    for item in forecasts:
        if item.projected_removal and item.projected_removal <= horizon:
            when = max(item.projected_removal, today)
            key = f"{when.year:04d}-{when.month:02d}"
            by_month[key] = by_month.get(key, 0) + 1
    mapping = loaded.connection.mapping
    work_orders = mapping.entities.get(Entity.WORK_ORDERS) if mapping else None
    operations = operations_summary(loaded.snapshot.dataset, today, work_orders.window_days if work_orders else None)
    return DashboardView(
        summary=summary, operations=operations, synced_at=loaded.snapshot.stats.synced_at, alert_counts=counts, top_alerts=top,
        due_soon=[item for item in forecasts if item.projected_removal][:10], due_by_month=dict(sorted(by_month.items())),
    )


class ForecastPage(BaseModel):
    items: list[TyreForecast]
    total: int
    limit: int
    offset: int
    min_tread_mm: float
    synced_at: datetime


_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def month_bounds(month: str) -> tuple[date, date]:
    if not _MONTH.match(month):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="month must be YYYY-MM")
    year, number = int(month[:4]), int(month[5:])
    start = date(year, number, 1)
    end = date(year + (number == 12), number % 12 + 1, 1) - timedelta(days=1)
    return start, end


def filter_forecasts(
    forecasts: list[TyreForecast], *, month: str | None = None, due_by: date | None = None,
    state: str | None = None, confidence: str | None = None, query: str | None = None, today: date | None = None,
) -> list[TyreForecast]:
    items = forecasts
    if month:
        start, end = month_bounds(month)
        today = today or utc_now().date()
        # Overdue tyres count in the current month.
        items = [f for f in items if f.projected_removal and (
            start <= f.projected_removal <= end or (f.projected_removal < start and start <= today <= end))]
    if due_by:
        items = [f for f in items if f.projected_removal and f.projected_removal <= due_by]
    if state:
        items = [f for f in items if f.state.value == state]
    if confidence:
        items = [f for f in items if f.confidence.value == confidence]
    if query:
        needle = query.strip().lower()
        items = [f for f in items if any(needle in (value or "").lower() for value in (
            f.serial, f.tyre_id, f.vehicle_id, f.vehicle_label, f.position, f.brand, f.size))]
    return items


@router.get("/forecasts", response_model=ForecastPage)
async def forecasts(
    month: str | None = Query(default=None, description="Projected removal in this month (YYYY-MM)"),
    due_by: date | None = None,
    state: ForecastState | None = None,
    confidence: Confidence | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> ForecastPage:
    """Projected removal per fitted tyre, soonest first, with search and paging."""
    loaded, all_items = _loaded(context)
    items = filter_forecasts(all_items, month=month, due_by=due_by, state=state.value if state else None,
                             confidence=confidence.value if confidence else None, query=q)
    return ForecastPage(items=items[offset: offset + limit], total=len(items), limit=limit, offset=offset,
                        min_tread_mm=loaded.connection.assumptions.min_tread_mm,
                        synced_at=loaded.snapshot.stats.synced_at)


@router.get("/purchase-plan", response_model=PurchasePlan)
async def get_purchase_plan(
    months: int | None = Query(default=None, ge=1, le=36),
    context: TenantContext = Depends(tenant_context),
) -> PurchasePlan:
    loaded, items = _loaded(context)
    return purchase_plan(loaded.snapshot.dataset, loaded.connection.assumptions, utc_now().date(), items, months=months)


@router.get("/purchase-plan.csv")
async def purchase_plan_csv(
    months: int | None = Query(default=None, ge=1, le=36),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    from .reports import build_report

    loaded, _ = _loaded(context)
    today = utc_now().date()
    end = today + timedelta(days=31 * (months or loaded.connection.assumptions.horizon_months) - 1)
    content = build_report(ReportKind.PURCHASE_PLAN, loaded.snapshot.dataset, loaded.connection.assumptions,
                           as_of=today, period_from=date(today.year, today.month, 1), period_to=end,
                           data_as_of=loaded.snapshot.stats.synced_at)
    return _download(to_csv(content).encode("utf-8"), "text/csv; charset=utf-8", f"tyre-purchase-plan-{today}.csv")


@router.get("/forecasts.csv")
async def forecasts_csv(
    month: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    """Every matching forecast (no paging) as CSV."""
    from .reports import FORECAST_COLUMNS, _forecast_rows, to_csv as render

    loaded, all_items = _loaded(context)
    items = filter_forecasts(all_items, month=month, query=q)
    content = {"title": "Tyre removal forecast", "period_from": month or "", "period_to": "",
               "data_as_of": loaded.snapshot.stats.synced_at.isoformat(), "highlights": [],
               "tables": [{"title": "Forecast", "columns": FORECAST_COLUMNS, "rows": _forecast_rows(items)}],
               "notes": []}
    return _download(render(content).encode("utf-8"), "text/csv; charset=utf-8", "tyre-forecast.csv")


def _download(body: bytes, media_type: str, filename: str) -> Response:
    return Response(content=body, media_type=media_type,
                    headers={"content-disposition": f'attachment; filename="{filename}"',
                             "cache-control": "no-store"})


# --- alerts ------------------------------------------------------------------------------------


class AlertPage(BaseModel):
    items: list[TyreAlert]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    status_filter: Literal["active", "open", "acknowledged", "resolved"] | None = Query(default="active", alias="status"),
    kind: AlertKind | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> AlertPage:
    _read(context)
    with open_tyre_store(context) as store:
        items, total = store.list_alerts(context, status=status_filter, kind=kind.value if kind else None,
                                         query=q, limit=limit, offset=offset)
        counts = store.alert_counts(context)
    return AlertPage(items=items, total=total, limit=limit, offset=offset, counts=counts)


def _set_alert(context: TenantContext, alert_id: str, acknowledge: bool) -> TyreAlert:
    with open_tyre_store(context) as store:
        alert = store.get_alert(context, alert_id)
        if alert is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
        if alert.status == AlertStatus.RESOLVED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This alert already cleared on the latest data.")
        if acknowledge:
            alert.status, alert.acknowledged_by, alert.acknowledged_at = AlertStatus.ACKNOWLEDGED, context.user_id, utc_now()
        else:
            alert.status, alert.acknowledged_by, alert.acknowledged_at = AlertStatus.OPEN, None, None
        store.save_alert(alert)
    return alert


@router.post("/alerts/{alert_id}/acknowledge", response_model=TyreAlert)
async def acknowledge_alert(
    alert_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TyreAlert:
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    alert = _set_alert(context, alert_id, True)
    _audit(repository, context, "tyre.alert.acknowledged", alert.id, {"kind": alert.kind.value})
    return alert


@router.post("/alerts/{alert_id}/reopen", response_model=TyreAlert)
async def reopen_alert(
    alert_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TyreAlert:
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    alert = _set_alert(context, alert_id, False)
    _audit(repository, context, "tyre.alert.reopened", alert.id, {"kind": alert.kind.value})
    return alert


# --- reports -----------------------------------------------------------------------------------


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReportKind
    period_from: date | None = None
    period_to: date | None = None


class ReportSummary(BaseModel):
    id: str
    kind: ReportKind
    title: str
    period_from: date | None
    period_to: date | None
    data_as_of: datetime | None
    created_by: str
    created_at: datetime
    source: str
    schedule_id: str | None
    highlights: list[dict[str, Any]]
    deliveries: list[dict[str, Any]]


def _summary(report: TyreReport) -> ReportSummary:
    return ReportSummary(
        id=report.id, kind=report.kind, title=report.title, period_from=report.period_from,
        period_to=report.period_to, data_as_of=report.data_as_of, created_by=report.created_by,
        created_at=report.created_at, source=report.source.value, schedule_id=report.schedule_id,
        highlights=report.content.get("highlights", []), deliveries=report.deliveries,
    )


class ReportPage(BaseModel):
    items: list[ReportSummary]
    total: int
    limit: int
    offset: int


@router.post("/reports", response_model=TyreReport, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> TyreReport:
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    try:
        report = service.generate_report(context, payload.kind, period_from=payload.period_from,
                                         period_to=payload.period_to)
    except (service.NotConnectedError, service.NotSyncedError) as exc:
        raise _conflict(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    _audit(repository, context, "tyre.report.generated", report.id, {"kind": report.kind.value})
    return report


@router.get("/reports", response_model=ReportPage)
async def list_reports(
    kind: ReportKind | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> ReportPage:
    _read(context)
    with open_tyre_store(context) as store:
        items, total = store.list_reports(context, kind=kind.value if kind else None, limit=limit, offset=offset)
    return ReportPage(items=[_summary(item) for item in items], total=total, limit=limit, offset=offset)


def _report_or_404(context: TenantContext, report_id: str) -> TyreReport:
    with open_tyre_store(context) as store:
        report = store.get_report(context, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return report


@router.get("/reports/{report_id}", response_model=TyreReport)
async def get_report(report_id: str, context: TenantContext = Depends(tenant_context)) -> TyreReport:
    _read(context)
    return _report_or_404(context, report_id)


def render_report(report: TyreReport, fmt: str) -> tuple[str, str, bytes]:
    stem = f"tyre-{report.kind.value.replace('_', '-')}-{report.created_at.date().isoformat()}"
    if fmt == "pdf":
        return f"{stem}.pdf", "application/pdf", to_pdf(report.content)
    return f"{stem}.csv", "text/csv; charset=utf-8", to_csv(report.content).encode("utf-8")


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: str,
    format: Literal["csv", "pdf"] = "pdf",
    context: TenantContext = Depends(tenant_context),
) -> Response:
    _read(context)
    filename, media_type, body = render_report(_report_or_404(context, report_id), format)
    return _download(body, media_type, filename)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    with open_tyre_store(context) as store:
        if not store.delete_report(context, report_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    _audit(repository, context, "tyre.report.deleted", report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DeliveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern=r"^[a-z_]{2,30}$")
    to: str = Field(min_length=3, max_length=200)
    format: Literal["pdf", "csv"] = "pdf"

    @field_validator("to")
    @classmethod
    def _recipient(cls, value: str) -> str:
        value = value.strip()
        email = re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", value)
        phone = re.fullmatch(r"\+?[0-9 ]{7,20}", value)
        if not (email or phone):
            raise ValueError("Enter one email address or one phone number in international format.")
        return value


class DeliveryView(BaseModel):
    status: str
    approval_id: str | None
    payload_hash: str | None
    task_id: str
    message: str
    report: ReportSummary


async def deliver(
    context: TenantContext, repository: AnumRepository, report: TyreReport, payload: DeliveryInput
) -> DeliveryView:
    reference = delivery.attachment_reference(REPORT_ATTACHMENT_KIND, report.id, payload.format, context)
    try:
        outcome = await delivery.request_delivery(
            context, repository, channel=payload.channel, to=payload.to,
            subject=f"{report.title} ({report.period_from} to {report.period_to})",
            text=summary_text(report.content), attachments=[reference],
            title=f"Send tyre report: {report.title}",
        )
    except delivery.DeliveryUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    entry = {"channel": payload.channel, "to": payload.to, "format": payload.format, "status": outcome.status,
             "approval_id": outcome.approval_id, "task_id": outcome.task_id,
             "requested_at": utc_now().isoformat(), "requested_by": context.user_id}
    with open_tyre_store(context) as store:
        current = store.get_report(context, report.id)
        if current is not None:
            current.deliveries = (current.deliveries + [entry])[-20:]
            store.save_report(current)
            report = current
    _audit(repository, context, "tyre.report.delivery_requested", report.id,
           {"channel": payload.channel, "status": outcome.status, "task_id": outcome.task_id},
           outcome="success" if outcome.status in ("sent", "waiting_approval") else "failure")
    message = {
        "waiting_approval": "Sending needs an approval: approve it in Approvals and it will be sent.",
        "sent": "Sent.",
    }.get(outcome.status, outcome.summary or "Not sent.")
    return DeliveryView(status=outcome.status, approval_id=outcome.approval_id, payload_hash=outcome.payload_hash,
                        task_id=outcome.task_id, message=message, report=_summary(report))


@router.post("/reports/{report_id}/deliver", response_model=DeliveryView)
async def deliver_report(
    report_id: str,
    payload: DeliveryInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DeliveryView:
    """Send the report through a connected email or WhatsApp integration (approval rules apply)."""
    _require(context, Permission.TYRE_OPERATE, OPERATORS_ONLY)
    return await deliver(context, repository, _report_or_404(context, report_id), payload)


def _resolve_report_attachment(context: TenantContext, report_id: str, fmt: str) -> tuple[str, str, bytes] | None:
    with open_tyre_store(context) as store:
        report = store.get_report(context, report_id)
    if report is None or fmt not in ("pdf", "csv"):
        return None
    return render_report(report, fmt)


delivery.register_attachment_resolver(REPORT_ATTACHMENT_KIND, _resolve_report_attachment)


@router.get("/delivery-channels")
async def delivery_channels(context: TenantContext = Depends(tenant_context)) -> dict[str, list[str]]:
    _read(context)
    return {"channels": sorted(delivery.channels())}


# --- report schedules (automation) -------------------------------------------------------------

from .schedules import router as schedules_router  # noqa: E402

router.include_router(schedules_router)
