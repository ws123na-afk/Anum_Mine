"""PostgreSQL store for PDF statement templates (migration ``0139_finance_pdf_templates``).

The session carries the tenant, workspace and user RLS context (``open_template_store`` opens it with
``user_scoped=True``); the table also has a restrictive ``owner_user_id = anum.user_id`` policy, and every
query here repeats the scope and the owner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.pdf_templates import StatementTemplate
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class FinanceStatementTemplateRecord(Base):
    __tablename__ = "finance_statement_templates"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    labels: Mapped[list[Any]] = mapped_column(JSONB)
    roles: Mapped[list[Any]] = mapped_column(JSONB)
    date_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uses: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)
    last_used_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


def _mine(context: TenantContext) -> list[Any]:
    record = FinanceStatementTemplateRecord
    return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id]


def _model(row: FinanceStatementTemplateRecord) -> StatementTemplate:
    return StatementTemplate.model_validate({c.name: getattr(row, c.name) for c in FinanceStatementTemplateRecord.__table__.columns})


class SqlAlchemyTemplateStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def by_fingerprint(self, context: TenantContext, fingerprint: str) -> StatementTemplate | None:
        row = self.session.scalar(select(FinanceStatementTemplateRecord).where(*_mine(context), FinanceStatementTemplateRecord.fingerprint == fingerprint))
        return _model(row) if row is not None else None

    def list(self, context: TenantContext) -> list[StatementTemplate]:
        rows = self.session.scalars(select(FinanceStatementTemplateRecord).where(*_mine(context))
                                    .order_by(FinanceStatementTemplateRecord.updated_at.desc()).limit(200))
        return [_model(row) for row in rows]

    def save(self, template: StatementTemplate) -> StatementTemplate:
        values = template.model_dump()
        keep = ("id", "tenant_id", "workspace_id", "owner_user_id", "fingerprint", "created_at")
        self.session.execute(insert(FinanceStatementTemplateRecord).values(**values).on_conflict_do_update(
            constraint="uq_finance_statement_templates_owner_fingerprint", set_={k: v for k, v in values.items() if k not in keep}))
        return template

    def delete(self, context: TenantContext, template_id: str) -> bool:
        record = FinanceStatementTemplateRecord
        return bool(self.session.execute(delete(record).where(*_mine(context), record.id == template_id)).rowcount)  # type: ignore[attr-defined]


__all__ = ["FinanceStatementTemplateRecord", "SqlAlchemyTemplateStore"]
