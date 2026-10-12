"""PostgreSQL store of the workspace calendar (migration 0036_reports_calendar, docs/calendar.md).

``calendar_events`` rows are scoped to a tenant and workspace under forced RLS; the session
already carries that context (``open_scoped_store``) and every query filters on it as well.
Attendees are a JSONB list on the event, validated by the API models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.calendar_api import CalendarEvent, EventStatus
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class CalendarEventRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    conference_url: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(40))
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("calendar_events"),)


_FIELDS = (
    "title", "description", "location", "kind", "status", "starts_at", "ends_at", "conference_url", "phone",
    "source", "source_ref", "created_by", "updated_by", "created_at", "updated_at",
)


def _event(record: CalendarEventRecord) -> CalendarEvent:
    return CalendarEvent.model_validate(
        {
            "id": record.id,
            "tenant_id": record.tenant_id,
            "workspace_id": record.workspace_id,
            "attendees": list(record.attendees or []),
            **{name: getattr(record, name) for name in _FIELDS},
        }
    )


def _apply(event: CalendarEvent, record: CalendarEventRecord) -> None:
    for name in _FIELDS:
        value = getattr(event, name)
        setattr(record, name, value.value if hasattr(value, "value") else value)
    record.attendees = [a.model_dump(mode="json") for a in event.attendees]


class SqlAlchemyCalendarStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self) -> tuple[Any, Any]:
        e = CalendarEventRecord
        return (e.tenant_id == self.context.tenant_id, e.workspace_id == self.context.workspace_id)

    def _record(self, event_id: str) -> CalendarEventRecord | None:
        return self.session.scalar(select(CalendarEventRecord).where(*self._scope(), CalendarEventRecord.id == event_id))

    def add(self, event: CalendarEvent) -> CalendarEvent:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = CalendarEventRecord(id=event.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _apply(event, record)
        self.session.add(record)
        self.session.flush()
        return event

    def get(self, event_id: str) -> CalendarEvent | None:
        record = self._record(event_id)
        return _event(record) if record is not None else None

    def save(self, event: CalendarEvent) -> CalendarEvent:
        record = self._record(event.id)
        if record is None:
            raise KeyError(event.id)
        _apply(event, record)
        self.session.flush()
        return event

    def delete(self, event_id: str) -> bool:
        result = self.session.execute(delete(CalendarEventRecord).where(*self._scope(), CalendarEventRecord.id == event_id))
        return bool(getattr(result, "rowcount", 0))

    def between(self, start: datetime, end: datetime, *, include_cancelled: bool, limit: int) -> list[CalendarEvent]:
        e = CalendarEventRecord
        statement = select(e).where(*self._scope(), e.starts_at < end, e.ends_at > start)
        if not include_cancelled:
            statement = statement.where(e.status == EventStatus.SCHEDULED.value)
        records = self.session.scalars(statement.order_by(e.starts_at, e.id).limit(limit)).all()
        return [_event(record) for record in records]
