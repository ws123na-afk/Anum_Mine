"""PostgreSQL store for in-app calls (migration ``0072_app_calls``).

The session's RLS context names the tenant, the workspace and the participant
(``anum.user_id``). Every statement repeats the tenant, the workspace and the participant
filter, so a call of other people is neither read nor written even if a policy were
missing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from anum_api.app_calls import (
    MISSED_OUTCOMES,
    AppCall,
    CallMedia,
    CallOutcome,
    CallSignal,
    CallStatus,
    SignalKind,
    forget,
)
from anum_api.maintenance import SessionFactory, discover, scoped_unit
from anum_api.schemas import TenantContext

from .models import AppCallRecord, AppCallSignalRecord
from .scope import require_workspace

_MUTABLE = ("callee_client_id", "status", "outcome", "ended_by_user_id", "answered_at", "ended_at", "heartbeat_at", "duration_seconds")


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _call(record: AppCallRecord) -> AppCall:
    return AppCall(
        id=record.id,
        caller_user_id=record.caller_user_id,
        callee_user_id=record.callee_user_id,
        caller_name=record.caller_name,
        callee_name=record.callee_name,
        caller_client_id=record.caller_client_id,
        callee_client_id=record.callee_client_id,
        media=CallMedia(record.media),
        status=CallStatus(record.status),
        outcome=CallOutcome(record.outcome) if record.outcome else None,
        ended_by_user_id=record.ended_by_user_id,
        created_at=record.created_at,
        answered_at=record.answered_at,
        ended_at=record.ended_at,
        heartbeat_at=record.heartbeat_at,
        duration_seconds=record.duration_seconds,
    )


def _signal(record: AppCallSignalRecord) -> CallSignal:
    return CallSignal(
        id=record.id,
        call_id=record.call_id,
        sender_user_id=record.sender_user_id,
        recipient_user_id=record.recipient_user_id,
        recipient_client_id=record.recipient_client_id,
        kind=SignalKind(record.kind),
        payload=record.payload,
        created_at=record.created_at,
    )


class SqlAlchemyCallStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _scope(context: TenantContext) -> Any:
        return and_(
            AppCallRecord.tenant_id == context.tenant_id,
            AppCallRecord.workspace_id == context.workspace_id,
            or_(AppCallRecord.caller_user_id == context.user_id, AppCallRecord.callee_user_id == context.user_id),
        )

    @staticmethod
    def _signal_scope(context: TenantContext) -> Any:
        return and_(
            AppCallSignalRecord.tenant_id == context.tenant_id,
            AppCallSignalRecord.workspace_id == context.workspace_id,
            or_(
                AppCallSignalRecord.sender_user_id == context.user_id,
                AppCallSignalRecord.recipient_user_id == context.user_id,
            ),
        )

    def _record(self, context: TenantContext, call_id: str, *, for_update: bool = False) -> AppCallRecord | None:
        statement = select(AppCallRecord).where(self._scope(context), AppCallRecord.id == call_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def insert(self, context: TenantContext, call: AppCall) -> AppCall:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        values = {name: _plain(getattr(call, name)) for name in AppCall.model_fields}
        self.session.add(AppCallRecord(tenant_id=context.tenant_id, workspace_id=context.workspace_id, **values))
        self.session.flush()
        return call

    def get(self, context: TenantContext, call_id: str, *, for_update: bool = False) -> AppCall | None:
        record = self._record(context, call_id, for_update=for_update)
        return _call(record) if record is not None else None

    def save(self, context: TenantContext, call: AppCall) -> AppCall:
        record = self._record(context, call.id)
        if record is None:
            raise LookupError(call.id)
        for name in _MUTABLE:
            setattr(record, name, _plain(getattr(call, name)))
        self.session.flush()
        return call

    def page(
        self, context: TenantContext, *, limit: int, before: tuple[datetime, str] | None, missed_only: bool
    ) -> list[AppCall]:
        statement = select(AppCallRecord).where(self._scope(context))
        if missed_only:
            statement = statement.where(
                AppCallRecord.callee_user_id == context.user_id,
                AppCallRecord.outcome.in_([outcome.value for outcome in MISSED_OUTCOMES]),
            )
        if before is not None:
            statement = statement.where(tuple_(AppCallRecord.created_at, AppCallRecord.id) < tuple_(*before))
        statement = statement.order_by(AppCallRecord.created_at.desc(), AppCallRecord.id.desc()).limit(limit)
        return [_call(record) for record in self.session.scalars(statement)]

    def live(self, context: TenantContext) -> list[AppCall]:
        statement = select(AppCallRecord).where(self._scope(context), AppCallRecord.status != CallStatus.ENDED.value)
        return [_call(record) for record in self.session.scalars(statement.order_by(AppCallRecord.created_at))]

    def recent(self, context: TenantContext, since: datetime) -> list[AppCall]:
        statement = select(AppCallRecord).where(
            self._scope(context),
            or_(AppCallRecord.status != CallStatus.ENDED.value, AppCallRecord.ended_at >= since),
        )
        ordered = statement.order_by(AppCallRecord.created_at, AppCallRecord.id)
        return [_call(record) for record in self.session.scalars(ordered)]

    def add_signal(self, context: TenantContext, signal: CallSignal) -> CallSignal:
        self.session.add(
            AppCallSignalRecord(
                id=signal.id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                call_id=signal.call_id,
                sender_user_id=signal.sender_user_id,
                recipient_user_id=signal.recipient_user_id,
                recipient_client_id=signal.recipient_client_id,
                kind=signal.kind.value,
                payload=signal.payload,
                created_at=signal.created_at,
            )
        )
        self.session.flush()
        return signal

    def count_sent(self, context: TenantContext, call_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(AppCallSignalRecord)
            .where(
                self._signal_scope(context),
                AppCallSignalRecord.call_id == call_id,
                AppCallSignalRecord.sender_user_id == context.user_id,
            )
        )
        return int(self.session.scalar(statement) or 0)

    def signals_for(self, context: TenantContext, client_id: str, call_ids: list[str]) -> list[CallSignal]:
        statement = (
            select(AppCallSignalRecord)
            .where(
                self._signal_scope(context),
                AppCallSignalRecord.recipient_user_id == context.user_id,
                AppCallSignalRecord.recipient_client_id == client_id,
                AppCallSignalRecord.call_id.in_(call_ids),
            )
            .order_by(AppCallSignalRecord.created_at, AppCallSignalRecord.id)
        )
        return [_signal(record) for record in self.session.scalars(statement)]

    def delete_signals(self, context: TenantContext, call_id: str) -> int:
        result = self.session.execute(
            delete(AppCallSignalRecord).where(self._signal_scope(context), AppCallSignalRecord.call_id == call_id)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def all_mine(self, context: TenantContext) -> list[AppCall]:
        statement = select(AppCallRecord).where(self._scope(context)).order_by(AppCallRecord.created_at, AppCallRecord.id)
        return [_call(record) for record in self.session.scalars(statement)]

    def count_signals(self, context: TenantContext) -> int:
        statement = select(func.count()).select_from(AppCallSignalRecord).where(self._signal_scope(context))
        return int(self.session.scalar(statement) or 0)

    def delete_my_signals(self, context: TenantContext) -> int:
        result = self.session.execute(delete(AppCallSignalRecord).where(self._signal_scope(context)))
        return int(getattr(result, "rowcount", 0) or 0)

    def delete_call(self, context: TenantContext, call_id: str) -> bool:
        result = self.session.execute(delete(AppCallRecord).where(self._scope(context), AppCallRecord.id == call_id))
        return bool(getattr(result, "rowcount", 0))

    def forget_side(self, context: TenantContext, call_id: str, user_id: str, tombstone: str) -> bool:
        record = self._record(context, call_id, for_update=True)
        if record is None:
            return False
        forgotten = forget(_call(record), user_id, tombstone)
        for name in ("caller_user_id", "caller_name", "caller_client_id", "callee_user_id", "callee_name", *_MUTABLE):
            setattr(record, name, _plain(getattr(forgotten, name)))
        self.session.flush()
        return True

    def purge(self, context: TenantContext, before: datetime) -> int:
        result = self.session.execute(
            delete(AppCallRecord).where(
                self._scope(context), AppCallRecord.created_at < before, AppCallRecord.status == CallStatus.ENDED.value
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


# -- retention (the daily job, python -m anum_api.voice_retention) -------------------------

_OLD_CALLS = text(
    """
    select id, tenant_id, workspace_id, caller_user_id, ended_at
    from app_calls
    where ended_at <= now() - make_interval(days => :days)
      and (cast(:after_ended_at as timestamptz) is null
           or (ended_at, id) > (cast(:after_ended_at as timestamptz), :after_id))
    order by ended_at, id
    limit :limit
    """
)
# Re-checks the age; signals go with their call (ON DELETE CASCADE).
_DELETE_OLD = text(
    """
    with doomed as (
      delete from app_calls
      where tenant_id = :tenant_id and workspace_id = :workspace_id and id = any(:ids)
        and status = 'ended' and ended_at <= now() - make_interval(days => :days)
      returning id
    )
    select count(*) as calls from doomed
    """
)
_COUNT_OLD = text(
    """
    select count(*) as calls from app_calls
    where tenant_id = :tenant_id and workspace_id = :workspace_id and id = any(:ids)
      and status = 'ended' and ended_at <= now() - make_interval(days => :days)
    """
)


@dataclass
class CallPurgeResult:
    retention_days: int
    dry_run: bool
    calls: int = 0


def purge_old_calls(
    session_factory: SessionFactory,
    *,
    retention_days: int,
    maintenance_session_factory: SessionFactory | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> CallPurgeResult:
    """Delete calls that ended ``retention_days`` or more ago (ANUM_CALLS_RETENTION_DAYS).

    Discovery runs as ``anum_maintenance`` (ids, scope, the caller and ``ended_at`` only);
    each delete runs as the application role inside the caller's RLS context.
    """
    if retention_days < 7:
        raise ValueError("retention_days must be 7 or more")
    result = CallPurgeResult(retention_days=retention_days, dry_run=dry_run)
    after_ended_at: datetime | None = None
    after_id = ""
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _OLD_CALLS,
            {"days": retention_days, "after_ended_at": after_ended_at, "after_id": after_id, "limit": batch_size},
        )
        if not rows:
            return result
        after_ended_at, after_id = rows[-1].ended_at, rows[-1].id
        scopes: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for row in rows:
            scopes[(row.tenant_id, row.workspace_id, row.caller_user_id)].append(row.id)
        for (tenant_id, workspace_id, user_id), ids in scopes.items():
            params = {"tenant_id": tenant_id, "workspace_id": workspace_id, "ids": ids, "days": retention_days}
            with scoped_unit(session_factory, tenant_id, workspace_id, user_id=user_id) as session:
                counted = session.execute(_COUNT_OLD if dry_run else _DELETE_OLD, params).one()
                if dry_run:
                    session.rollback()
            result.calls += int(counted.calls)
        if len(rows) < batch_size:
            return result
