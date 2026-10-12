"""Where tyre planning keeps a workspace's connection, data snapshot, alerts and reports.

Two implementations of one protocol: in memory (``ANUM_REPOSITORY_BACKEND=memory``) and
PostgreSQL (``anum_api.db.tyre_repository``, migration ``0037_tyre_planning``), where
every table is under forced tenant and workspace RLS. ``open_tyre_store`` picks one for a
unit of work, like the other control-plane stores.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext, new_id
from ..scoped_store import open_scoped_store
from .analytics import AlertCandidate, AlertKind, Severity
from .mapping import SourceTable
from .model import Assumptions, SourceKind, SyncStats, TyreDataset, TyreMapping


class ConnectionStatus(StrEnum):
    UNTESTED = "untested"
    OK = "ok"
    FAILED = "failed"


class TyreConnection(BaseModel):
    tenant_id: str
    workspace_id: str
    kind: SourceKind
    label: str = Field(default="TyrePulse", max_length=120)
    # Supabase REST: project URL; PostgreSQL: user@host:port/db (display only); Firestore: database id.
    endpoint: str = Field(default="", max_length=500)
    schema_name: str = Field(default="public", max_length=63)
    secret_ciphertext: str | None = None
    secret_hint: str | None = None
    mapping: TyreMapping | None = None
    assumptions: Assumptions = Field(default_factory=Assumptions)
    discovery: list[SourceTable] | None = None
    discovered_at: datetime | None = None
    status: ConnectionStatus = ConnectionStatus.UNTESTED
    last_tested_at: datetime | None = None
    last_error: str | None = None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class TyreSnapshot(BaseModel):
    tenant_id: str
    workspace_id: str
    dataset: TyreDataset
    stats: SyncStats
    synced_by: str


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class TyreAlert(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    key: str
    kind: AlertKind
    severity: Severity
    subject_type: str
    subject_id: str
    subject_label: str
    message: str
    value: dict[str, Any] = Field(default_factory=dict)
    status: AlertStatus = AlertStatus.OPEN
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None


class ReportKind(StrEnum):
    FLEET_HEALTH = "fleet_health"
    WEAR_RATES = "wear_rates"
    PRESSURE_COMPLIANCE = "pressure_compliance"
    COST_PER_DISTANCE = "cost_per_distance"
    SCRAP_REASONS = "scrap_reasons"
    BRAND_PERFORMANCE = "brand_performance"
    REMOVAL_FORECAST = "removal_forecast"
    PURCHASE_PLAN = "purchase_plan"


class ReportSource(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"


class TyreReport(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    kind: ReportKind
    title: str
    period_from: date | None = None
    period_to: date | None = None
    data_as_of: datetime | None = None
    created_by: str
    created_at: datetime
    source: ReportSource = ReportSource.MANUAL
    schedule_id: str | None = None
    idempotency_key: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    # Delivery requests: {"channel", "to", "approval_id", "status", "requested_at", "requested_by"}.
    deliveries: list[dict[str, Any]] = Field(default_factory=list)


class TyreStore(Protocol):
    def get_connection(self, context: TenantContext) -> TyreConnection | None: ...
    def save_connection(self, connection: TyreConnection) -> TyreConnection: ...
    def delete_connection(self, context: TenantContext) -> bool: ...
    def get_snapshot(self, context: TenantContext) -> TyreSnapshot | None: ...
    def save_snapshot(self, snapshot: TyreSnapshot) -> TyreSnapshot: ...
    def active_alerts(self, context: TenantContext) -> list[TyreAlert]: ...
    def save_alert(self, alert: TyreAlert) -> TyreAlert: ...
    def get_alert(self, context: TenantContext, alert_id: str) -> TyreAlert | None: ...
    def list_alerts(
        self, context: TenantContext, *, status: str | None, kind: str | None, query: str | None,
        limit: int, offset: int,
    ) -> tuple[list[TyreAlert], int]: ...
    def alert_counts(self, context: TenantContext) -> dict[str, int]: ...
    def purge_resolved(self, context: TenantContext, before: datetime) -> int: ...
    def save_report(self, report: TyreReport) -> TyreReport: ...
    def get_report(self, context: TenantContext, report_id: str) -> TyreReport | None: ...
    def find_report(self, context: TenantContext, idempotency_key: str) -> TyreReport | None: ...
    def delete_report(self, context: TenantContext, report_id: str) -> bool: ...
    def list_reports(
        self, context: TenantContext, *, kind: str | None, limit: int, offset: int
    ) -> tuple[list[TyreReport], int]: ...


# Resolved alerts stay listed (history) for this long, then are deleted.
RESOLVED_RETENTION = timedelta(days=90)

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


def alert_sort_key(alert: TyreAlert) -> tuple[int, int, float, str]:
    return (
        alert.status != AlertStatus.OPEN,
        _SEVERITY_ORDER[alert.severity],
        -alert.last_seen_at.timestamp(),
        alert.id,
    )


def _matches(alert: TyreAlert, status: str | None, kind: str | None, query: str | None) -> bool:
    if status == "active":
        if alert.status == AlertStatus.RESOLVED:
            return False
    elif status and alert.status.value != status:
        return False
    if kind and alert.kind.value != kind:
        return False
    if query:
        needle = query.lower()
        return needle in alert.subject_label.lower() or needle in alert.message.lower()
    return True


class InMemoryTyreStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._connections: dict[tuple[str, str], TyreConnection] = {}
        self._snapshots: dict[tuple[str, str], TyreSnapshot] = {}
        self._alerts: dict[str, TyreAlert] = {}
        self._reports: dict[str, TyreReport] = {}

    @staticmethod
    def _scope(context: TenantContext) -> tuple[str, str]:
        return context.tenant_id, context.workspace_id

    @staticmethod
    def _in(item: Any, context: TenantContext) -> bool:
        return item.tenant_id == context.tenant_id and item.workspace_id == context.workspace_id

    def clear(self) -> None:
        with self._lock:
            self._connections.clear()
            self._snapshots.clear()
            self._alerts.clear()
            self._reports.clear()

    def get_connection(self, context: TenantContext) -> TyreConnection | None:
        with self._lock:
            found = self._connections.get(self._scope(context))
            return found.model_copy(deep=True) if found else None

    def save_connection(self, connection: TyreConnection) -> TyreConnection:
        with self._lock:
            self._connections[(connection.tenant_id, connection.workspace_id)] = connection.model_copy(deep=True)
        return connection

    def delete_connection(self, context: TenantContext) -> bool:
        with self._lock:
            found = self._connections.pop(self._scope(context), None)
            self._snapshots.pop(self._scope(context), None)
            for alert_id in [key for key, alert in self._alerts.items() if self._in(alert, context)]:
                del self._alerts[alert_id]
            return found is not None

    def get_snapshot(self, context: TenantContext) -> TyreSnapshot | None:
        with self._lock:
            return self._snapshots.get(self._scope(context))

    def save_snapshot(self, snapshot: TyreSnapshot) -> TyreSnapshot:
        with self._lock:
            self._snapshots[(snapshot.tenant_id, snapshot.workspace_id)] = snapshot
        return snapshot

    def active_alerts(self, context: TenantContext) -> list[TyreAlert]:
        with self._lock:
            return [
                alert.model_copy() for alert in self._alerts.values()
                if self._in(alert, context) and alert.status != AlertStatus.RESOLVED
            ]

    def save_alert(self, alert: TyreAlert) -> TyreAlert:
        with self._lock:
            self._alerts[alert.id] = alert.model_copy()
        return alert

    def get_alert(self, context: TenantContext, alert_id: str) -> TyreAlert | None:
        with self._lock:
            alert = self._alerts.get(alert_id)
            return alert.model_copy() if alert and self._in(alert, context) else None

    def list_alerts(self, context, *, status, kind, query, limit, offset):  # type: ignore[no-untyped-def]
        with self._lock:
            items = sorted(
                (a for a in self._alerts.values() if self._in(a, context) and _matches(a, status, kind, query)),
                key=alert_sort_key,
            )
        return [item.model_copy() for item in items[offset: offset + limit]], len(items)

    def alert_counts(self, context: TenantContext) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for alert in self._alerts.values():
                if self._in(alert, context) and alert.status == AlertStatus.OPEN:
                    counts[alert.severity.value] = counts.get(alert.severity.value, 0) + 1
        return counts

    def purge_resolved(self, context: TenantContext, before: datetime) -> int:
        with self._lock:
            stale = [
                key for key, alert in self._alerts.items()
                if self._in(alert, context) and alert.status == AlertStatus.RESOLVED
                and alert.resolved_at is not None and alert.resolved_at < before
            ]
            for key in stale:
                del self._alerts[key]
        return len(stale)

    def save_report(self, report: TyreReport) -> TyreReport:
        with self._lock:
            self._reports[report.id] = report.model_copy(deep=True)
        return report

    def get_report(self, context: TenantContext, report_id: str) -> TyreReport | None:
        with self._lock:
            report = self._reports.get(report_id)
            return report.model_copy(deep=True) if report and self._in(report, context) else None

    def find_report(self, context: TenantContext, idempotency_key: str) -> TyreReport | None:
        with self._lock:
            for report in self._reports.values():
                if self._in(report, context) and report.idempotency_key == idempotency_key:
                    return report.model_copy(deep=True)
        return None

    def delete_report(self, context: TenantContext, report_id: str) -> bool:
        with self._lock:
            report = self._reports.get(report_id)
            if report is None or not self._in(report, context):
                return False
            del self._reports[report_id]
            return True

    def list_reports(self, context, *, kind, limit, offset):  # type: ignore[no-untyped-def]
        with self._lock:
            items = sorted(
                (r for r in self._reports.values() if self._in(r, context) and (not kind or r.kind.value == kind)),
                key=lambda r: (-r.created_at.timestamp(), r.id),
            )
        return [item.model_copy(deep=True) for item in items[offset: offset + limit]], len(items)


memory_tyre_store = InMemoryTyreStore()


@contextmanager
def open_tyre_store(context: TenantContext) -> Iterator[TyreStore]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.tyre_repository import SqlAlchemyTyreStore

        return SqlAlchemyTyreStore(session)

    with open_scoped_store(context, memory_tyre_store, sql_store) as store:
        yield store


def reconcile_alerts(
    store: TyreStore, context: TenantContext, candidates: list[AlertCandidate], now: datetime
) -> list[TyreAlert]:
    """Open new alerts, refresh the ones still true, resolve the ones that cleared.

    Returns the alerts that are new (or re-opened) in this pass.
    """
    active = {alert.key: alert for alert in store.active_alerts(context)}
    seen: set[str] = set()
    raised: list[TyreAlert] = []
    for candidate in candidates:
        if candidate.key in seen:
            continue
        seen.add(candidate.key)
        current = active.get(candidate.key)
        if current is None:
            alert = TyreAlert(
                id=new_id("tyre_alert"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                first_seen_at=now,
                last_seen_at=now,
                **candidate.model_dump(),
            )
            store.save_alert(alert)
            raised.append(alert)
            continue
        escalated = _SEVERITY_ORDER[candidate.severity] < _SEVERITY_ORDER[current.severity]
        current.severity = candidate.severity
        current.message = candidate.message
        current.value = dict(candidate.value)
        current.subject_label = candidate.subject_label
        current.last_seen_at = now
        if escalated and current.status == AlertStatus.ACKNOWLEDGED:
            # Worse than when it was acknowledged: it needs attention again.
            current.status = AlertStatus.OPEN
            current.acknowledged_at = current.acknowledged_by = None
            raised.append(current)
        store.save_alert(current)
    for key, alert in active.items():
        if key not in seen:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = now
            store.save_alert(alert)
    store.purge_resolved(context, now - RESOLVED_RETENTION)
    return raised
