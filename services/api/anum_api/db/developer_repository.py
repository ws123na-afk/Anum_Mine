"""PostgreSQL developer-mode requests (migrations 0022_developer_requests, 0023_developer_one_active).

The session carries the tenant and workspace RLS context (``open_scoped_store``); the
queries filter on the scope explicitly as well. Rows are shared by the workspace's owners.

Admission (``admit``) serialises creates per tenant and workspace with a transaction-scoped
advisory lock, then counts and inserts in the same transaction; the partial unique index
``uq_developer_requests_one_active`` backs the "one queued or coding request" rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.developer import (
    ACTIVE,
    ChecksSummary,
    DeveloperAdmissionError,
    DeveloperRequest,
    DevRequestSource,
    DevRequestStatus,
)
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class DeveloperRequestRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "developer_requests"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    allow_protected_paths: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    branch: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    run_url: Mapped[str | None] = mapped_column(String(500))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    pr_title: Mapped[str | None] = mapped_column(String(300))
    pr_draft: Mapped[bool | None] = mapped_column(Boolean)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    checks: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    merge_error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    merge_task_id: Mapped[str | None] = mapped_column(String(80))
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (_workspace_fk("developer_requests"),)


# pg_advisory_xact_lock(key1, key2): key1 names developer-mode admission, key2 is the scope's
# hash. A hash collision only makes two scopes wait for each other, never admits more.
ADMISSION_LOCK_CLASS = 0x414E4456  # "ANDV"
ONE_ACTIVE_INDEX = "uq_developer_requests_one_active"

_FIELDS = (
    "created_by", "text", "source", "status", "allow_protected_paths", "branch", "run_id", "run_url",
    "pr_number", "pr_url", "pr_title", "pr_draft", "head_sha", "failure_reason", "merge_error",
    "approval_id", "merge_task_id", "merge_commit_sha", "created_at", "updated_at", "merged_at", "synced_at",
)


def _model(record: DeveloperRequestRecord) -> DeveloperRequest:
    return DeveloperRequest(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        created_by=record.created_by,
        text=record.text,
        source=DevRequestSource(record.source),
        status=DevRequestStatus(record.status),
        allow_protected_paths=record.allow_protected_paths,
        branch=record.branch,
        run_id=record.run_id,
        run_url=record.run_url,
        pr_number=record.pr_number,
        pr_url=record.pr_url,
        pr_title=record.pr_title,
        pr_draft=record.pr_draft,
        head_sha=record.head_sha,
        checks=ChecksSummary(**record.checks) if record.checks else None,
        failure_reason=record.failure_reason,
        merge_error=record.merge_error,
        approval_id=record.approval_id,
        merge_task_id=record.merge_task_id,
        merge_commit_sha=record.merge_commit_sha,
        created_at=record.created_at,
        updated_at=record.updated_at,
        merged_at=record.merged_at,
        synced_at=record.synced_at,
    )


def _copy(request: DeveloperRequest, record: DeveloperRequestRecord) -> None:
    for name in _FIELDS:
        value = getattr(request, name)
        setattr(record, name, value.value if isinstance(value, (DevRequestSource, DevRequestStatus)) else value)
    record.checks = request.checks.model_dump() if request.checks else None


class SqlAlchemyDeveloperStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            DeveloperRequestRecord.tenant_id == self.context.tenant_id,
            DeveloperRequestRecord.workspace_id == self.context.workspace_id,
        )

    def add(self, request: DeveloperRequest) -> DeveloperRequest:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = DeveloperRequestRecord(
            id=request.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id
        )
        _copy(request, record)
        self.session.add(record)
        self.session.flush()
        return request

    def admit(self, request: DeveloperRequest, *, since: datetime, limit: int) -> DeveloperRequest:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        scope_key = f"{self.context.tenant_id}\x1f{self.context.workspace_id}"
        # Held until this unit of work commits or rolls back: a concurrent create for the same
        # scope waits here and then sees this request (READ COMMITTED re-reads per statement).
        self.session.execute(
            text("select pg_advisory_xact_lock(cast(:lock_class as integer), hashtext(:scope_key))"),
            {"lock_class": ADMISSION_LOCK_CLASS, "scope_key": scope_key},
        )
        if self.active(self.context):
            raise DeveloperAdmissionError("active")
        if self.count_since(self.context, since) >= limit:
            raise DeveloperAdmissionError("daily_limit")
        try:
            return self.add(request)
        except IntegrityError as exc:
            # A writer that does not take the lock (an older release during a rollout).
            if ONE_ACTIVE_INDEX in str(exc.orig):
                raise DeveloperAdmissionError("active") from None
            raise

    def _record(self, request_id: str, *, for_update: bool = False) -> DeveloperRequestRecord | None:
        statement = select(DeveloperRequestRecord).where(*self._scope(), DeveloperRequestRecord.id == request_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get(self, request_id: str, context: TenantContext) -> DeveloperRequest | None:
        record = self._record(request_id)
        return _model(record) if record is not None else None

    def save(self, request: DeveloperRequest) -> DeveloperRequest:
        record = self._record(request.id, for_update=True)
        if record is None:
            raise KeyError(request.id)
        _copy(request, record)
        self.session.flush()
        return request

    def list(
        self, context: TenantContext, *, limit: int, before: DeveloperRequest | None, query: str | None
    ) -> list[DeveloperRequest]:
        statement = select(DeveloperRequestRecord).where(*self._scope())
        if before is not None:
            statement = statement.where(
                or_(
                    DeveloperRequestRecord.created_at < before.created_at,
                    and_(
                        DeveloperRequestRecord.created_at == before.created_at,
                        DeveloperRequestRecord.id < before.id,
                    ),
                )
            )
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            statement = statement.where(
                or_(
                    DeveloperRequestRecord.text.ilike(pattern, escape="\\"),
                    DeveloperRequestRecord.pr_title.ilike(pattern, escape="\\"),
                )
            )
        records = self.session.scalars(
            statement.order_by(DeveloperRequestRecord.created_at.desc(), DeveloperRequestRecord.id.desc()).limit(limit)
        ).all()
        return [_model(record) for record in records]

    def count_since(self, context: TenantContext, since: datetime) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(DeveloperRequestRecord)
                .where(*self._scope(), DeveloperRequestRecord.created_at >= since)
            )
            or 0
        )

    def active(self, context: TenantContext) -> list[DeveloperRequest]:
        records = self.session.scalars(
            select(DeveloperRequestRecord).where(
                *self._scope(), DeveloperRequestRecord.status.in_([status.value for status in ACTIVE])
            )
        ).all()
        return [_model(record) for record in records]
