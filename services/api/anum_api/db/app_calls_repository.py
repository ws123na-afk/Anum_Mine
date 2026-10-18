"""PostgreSQL store for in-app calls (migration ``0072_app_calls``).

The session's RLS context names the tenant, the workspace and the participant
(``anum.user_id``). Every statement repeats the tenant, the workspace and the participant
filter, so a call of other people is neither read nor written even if a policy were
missing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, tuple_
from sqlalchemy.orm import Session

from anum_api.app_calls import (
    MISSED_OUTCOMES,
    AppCall,
    CallMedia,
    CallOutcome,
    CallSignal,
    CallStatus,
    SignalKind,
)
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

    def purge(self, context: TenantContext, before: datetime) -> int:
        result = self.session.execute(
            delete(AppCallRecord).where(
                self._scope(context), AppCallRecord.created_at < before, AppCallRecord.status == CallStatus.ENDED.value
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)
