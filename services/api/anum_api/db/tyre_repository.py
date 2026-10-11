"""PostgreSQL store for tyre planning (migration ``0037_tyre_planning``).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every
query also filters on the scope explicitly. JSONB columns hold the pydantic models.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, String, Text, and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.schemas import TenantContext
from anum_api.tyre.mapping import SourceTable
from anum_api.tyre.model import Assumptions, SyncStats, TyreDataset, TyreMapping
from anum_api.tyre.store import (
    AlertStatus,
    TyreAlert,
    TyreConnection,
    TyreReport,
    TyreSnapshot,
    alert_sort_key,
)

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class TyreConnectionRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "tyre_connections"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    schema_name: Mapped[str] = mapped_column(String(63), nullable=False, default="public")
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(200))
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    discovery: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("tyre_connections"),)


class TyreSnapshotRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "tyre_snapshots"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_by: Mapped[str] = mapped_column(String(160), nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (_workspace_fk("tyre_snapshots"),)


class TyreAlertRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "tyre_alerts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_label: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(160))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (_workspace_fk("tyre_alerts"),)


class TyreReportRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "tyre_reports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule_id: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    deliveries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (_workspace_fk("tyre_reports"),)


def _scope(model: Any, context: TenantContext) -> Any:
    return and_(model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id)


def _connection(record: TyreConnectionRecord) -> TyreConnection:
    return TyreConnection(
        tenant_id=record.tenant_id, workspace_id=record.workspace_id, kind=record.kind, label=record.label,
        endpoint=record.endpoint, schema_name=record.schema_name, secret_ciphertext=record.secret_ciphertext,
        secret_hint=record.secret_hint,
        mapping=TyreMapping.model_validate(record.mapping) if record.mapping else None,
        assumptions=Assumptions.model_validate(record.assumptions or {}),
        discovery=[SourceTable.model_validate(item) for item in record.discovery] if record.discovery is not None else None,
        discovered_at=record.discovered_at, status=record.status, last_tested_at=record.last_tested_at,
        last_error=record.last_error, created_by=record.created_by, updated_by=record.updated_by,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _alert(record: TyreAlertRecord) -> TyreAlert:
    return TyreAlert(
        id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, key=record.key,
        kind=record.kind, severity=record.severity, subject_type=record.subject_type, subject_id=record.subject_id,
        subject_label=record.subject_label, message=record.message, value=dict(record.value or {}),
        status=record.status, first_seen_at=record.first_seen_at, last_seen_at=record.last_seen_at,
        resolved_at=record.resolved_at, acknowledged_by=record.acknowledged_by, acknowledged_at=record.acknowledged_at,
    )


def _report(record: TyreReportRecord) -> TyreReport:
    return TyreReport(
        id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, kind=record.kind,
        title=record.title, period_from=record.period_from, period_to=record.period_to,
        data_as_of=record.data_as_of, created_by=record.created_by, created_at=record.created_at,
        source=record.source, schedule_id=record.schedule_id, idempotency_key=record.idempotency_key,
        content=dict(record.content or {}), deliveries=list(record.deliveries or []),
    )


class SqlAlchemyTyreStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # connection --------------------------------------------------------------------------

    def get_connection(self, context: TenantContext) -> TyreConnection | None:
        record = self.session.get(TyreConnectionRecord, (context.tenant_id, context.workspace_id))
        return _connection(record) if record else None

    def save_connection(self, connection: TyreConnection) -> TyreConnection:
        require_workspace(self.session, connection.tenant_id, connection.workspace_id)
        record = self.session.get(TyreConnectionRecord, (connection.tenant_id, connection.workspace_id))
        if record is None:
            record = TyreConnectionRecord(tenant_id=connection.tenant_id, workspace_id=connection.workspace_id)
            self.session.add(record)
        record.kind = connection.kind.value
        record.label = connection.label
        record.endpoint = connection.endpoint
        record.schema_name = connection.schema_name
        record.secret_ciphertext = connection.secret_ciphertext
        record.secret_hint = connection.secret_hint
        record.mapping = connection.mapping.model_dump(mode="json") if connection.mapping else None
        record.assumptions = connection.assumptions.model_dump(mode="json")
        record.discovery = (
            [table.model_dump(mode="json") for table in connection.discovery] if connection.discovery is not None else None
        )
        record.discovered_at = connection.discovered_at
        record.status = connection.status.value
        record.last_tested_at = connection.last_tested_at
        record.last_error = connection.last_error
        record.created_by = connection.created_by
        record.updated_by = connection.updated_by
        record.created_at = connection.created_at
        record.updated_at = connection.updated_at
        self.session.flush()
        return connection

    def delete_connection(self, context: TenantContext) -> bool:
        deleted = self.session.execute(delete(TyreConnectionRecord).where(_scope(TyreConnectionRecord, context))).rowcount
        self.session.execute(delete(TyreSnapshotRecord).where(_scope(TyreSnapshotRecord, context)))
        self.session.execute(delete(TyreAlertRecord).where(_scope(TyreAlertRecord, context)))
        return bool(deleted)

    # snapshot ----------------------------------------------------------------------------

    def get_snapshot(self, context: TenantContext) -> TyreSnapshot | None:
        record = self.session.get(TyreSnapshotRecord, (context.tenant_id, context.workspace_id))
        if record is None:
            return None
        return TyreSnapshot(
            tenant_id=record.tenant_id, workspace_id=record.workspace_id,
            dataset=TyreDataset.model_validate(record.dataset), stats=SyncStats.model_validate(record.stats),
            synced_by=record.synced_by,
        )

    def save_snapshot(self, snapshot: TyreSnapshot) -> TyreSnapshot:
        require_workspace(self.session, snapshot.tenant_id, snapshot.workspace_id)
        record = self.session.get(TyreSnapshotRecord, (snapshot.tenant_id, snapshot.workspace_id))
        if record is None:
            record = TyreSnapshotRecord(tenant_id=snapshot.tenant_id, workspace_id=snapshot.workspace_id)
            self.session.add(record)
        record.synced_at = snapshot.stats.synced_at
        record.synced_by = snapshot.synced_by
        record.stats = snapshot.stats.model_dump(mode="json")
        record.dataset = snapshot.dataset.model_dump(mode="json")
        self.session.flush()
        return snapshot

    # alerts ----------------------------------------------------------------------------------

    def active_alerts(self, context: TenantContext) -> list[TyreAlert]:
        records = self.session.scalars(
            select(TyreAlertRecord).where(_scope(TyreAlertRecord, context), TyreAlertRecord.status != AlertStatus.RESOLVED.value)
        )
        return [_alert(record) for record in records]

    def save_alert(self, alert: TyreAlert) -> TyreAlert:
        record = self.session.get(TyreAlertRecord, alert.id)
        if record is None:
            record = TyreAlertRecord(id=alert.id, tenant_id=alert.tenant_id, workspace_id=alert.workspace_id)
            self.session.add(record)
        for field in ("key", "subject_type", "subject_id", "subject_label", "message", "first_seen_at",
                      "last_seen_at", "resolved_at", "acknowledged_by", "acknowledged_at"):
            setattr(record, field, getattr(alert, field))
        record.kind, record.severity, record.status = alert.kind.value, alert.severity.value, alert.status.value
        record.value = dict(alert.value)
        self.session.flush()
        return alert

    def get_alert(self, context: TenantContext, alert_id: str) -> TyreAlert | None:
        record = self.session.scalar(
            select(TyreAlertRecord).where(_scope(TyreAlertRecord, context), TyreAlertRecord.id == alert_id)
        )
        return _alert(record) if record else None

    def list_alerts(self, context, *, status, kind, query, limit, offset):  # type: ignore[no-untyped-def]
        conditions = [_scope(TyreAlertRecord, context)]
        if status == "active":
            conditions.append(TyreAlertRecord.status != AlertStatus.RESOLVED.value)
        elif status:
            conditions.append(TyreAlertRecord.status == status)
        if kind:
            conditions.append(TyreAlertRecord.kind == kind)
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(or_(TyreAlertRecord.subject_label.ilike(pattern, escape="\\"),
                                  TyreAlertRecord.message.ilike(pattern, escape="\\")))
        total = int(self.session.scalar(select(func.count()).select_from(TyreAlertRecord).where(*conditions)) or 0)
        # Ordered in Python by the shared key so both stores page identically; bounded by the scope.
        records = [_alert(record) for record in self.session.scalars(select(TyreAlertRecord).where(*conditions))]
        records.sort(key=alert_sort_key)
        return records[offset: offset + limit], total

    def alert_counts(self, context: TenantContext) -> dict[str, int]:
        rows = self.session.execute(
            select(TyreAlertRecord.severity, func.count())
            .where(_scope(TyreAlertRecord, context), TyreAlertRecord.status == AlertStatus.OPEN.value)
            .group_by(TyreAlertRecord.severity)
        ).all()
        return {severity: int(count) for severity, count in rows}

    def purge_resolved(self, context: TenantContext, before: datetime) -> int:
        result = self.session.execute(
            delete(TyreAlertRecord).where(
                _scope(TyreAlertRecord, context), TyreAlertRecord.status == AlertStatus.RESOLVED.value,
                TyreAlertRecord.resolved_at < before,
            )
        )
        return int(result.rowcount or 0)

    # reports ---------------------------------------------------------------------------------

    def save_report(self, report: TyreReport) -> TyreReport:
        require_workspace(self.session, report.tenant_id, report.workspace_id)
        record = self.session.get(TyreReportRecord, report.id)
        if record is None:
            record = TyreReportRecord(id=report.id, tenant_id=report.tenant_id, workspace_id=report.workspace_id)
            self.session.add(record)
        for field in ("title", "period_from", "period_to", "data_as_of", "created_by", "created_at",
                      "schedule_id", "idempotency_key"):
            setattr(record, field, getattr(report, field))
        record.kind, record.source = report.kind.value, report.source.value
        record.content = report.content
        record.deliveries = list(report.deliveries)
        self.session.flush()
        return report

    def get_report(self, context: TenantContext, report_id: str) -> TyreReport | None:
        record = self.session.scalar(
            select(TyreReportRecord).where(_scope(TyreReportRecord, context), TyreReportRecord.id == report_id)
        )
        return _report(record) if record else None

    def find_report(self, context: TenantContext, idempotency_key: str) -> TyreReport | None:
        record = self.session.scalar(
            select(TyreReportRecord).where(_scope(TyreReportRecord, context),
                                           TyreReportRecord.idempotency_key == idempotency_key)
        )
        return _report(record) if record else None

    def delete_report(self, context: TenantContext, report_id: str) -> bool:
        result = self.session.execute(
            delete(TyreReportRecord).where(_scope(TyreReportRecord, context), TyreReportRecord.id == report_id)
        )
        return bool(result.rowcount)

    def list_reports(self, context, *, kind, limit, offset):  # type: ignore[no-untyped-def]
        conditions = [_scope(TyreReportRecord, context)]
        if kind:
            conditions.append(TyreReportRecord.kind == kind)
        total = int(self.session.scalar(select(func.count()).select_from(TyreReportRecord).where(*conditions)) or 0)
        records = self.session.scalars(
            select(TyreReportRecord).where(*conditions)
            .order_by(TyreReportRecord.created_at.desc(), TyreReportRecord.id).limit(limit).offset(offset)
        )
        return [_report(record) for record in records], total
