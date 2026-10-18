"""PostgreSQL store for the demo tag (migration ``0075_demo_records``, docs/demo-workspace.md).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every
query also filters on the scope explicitly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.schemas import TenantContext, new_id, utc_now

from .models import Base
from .scope import require_workspace


class DemoRecordRow(Base):
    __tablename__ = "demo_records"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    workspace_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SqlAlchemyDemoStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            DemoRecordRow.tenant_id == self.context.tenant_id,
            DemoRecordRow.workspace_id == self.context.workspace_id,
        )

    def tag(self, kind: str, record_id: str) -> None:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        exists = self.session.scalar(
            select(DemoRecordRow.id).where(*self._scope(), DemoRecordRow.kind == kind, DemoRecordRow.record_id == record_id)
        )
        if exists is None:
            self.session.add(
                DemoRecordRow(
                    id=new_id("demo"),
                    tenant_id=self.context.tenant_id,
                    workspace_id=self.context.workspace_id,
                    kind=kind,
                    record_id=record_id,
                    created_at=utc_now(),
                )
            )
            self.session.flush()

    def tagged(self) -> list[tuple[str, str, datetime]]:
        rows = self.session.scalars(select(DemoRecordRow).where(*self._scope()).order_by(DemoRecordRow.created_at))
        return [(row.kind, row.record_id, row.created_at) for row in rows]

    def untag(self, kind: str, record_id: str) -> None:
        self.session.execute(
            delete(DemoRecordRow).where(*self._scope(), DemoRecordRow.kind == kind, DemoRecordRow.record_id == record_id)
        )
