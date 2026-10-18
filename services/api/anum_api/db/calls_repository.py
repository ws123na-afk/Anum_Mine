"""PostgreSQL store of phone calls (migration ``0070_calls``, docs/calls.md#storage).

The session carries the tenant and workspace RLS context (``open_calls_store``); every query
here repeats the scope as well. The provider's auth token and API key secret are stored only
as Fernet ciphertext.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Integer, String, Text, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.calls.store import (
    AiCallRequest,
    CallContact,
    CallEntry,
    CallFilter,
    OptOut,
    ProviderAccount,
)
from anum_api.maintenance import default_session_factory, discover
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class CallContactRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "call_contacts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    phone_normalized: Mapped[str] = mapped_column(String(40), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    notes: Mapped[str | None] = mapped_column(Text)
    favourite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    ai_consent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("call_contacts"),)


class CallLogRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "call_log"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(80))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_normalized: Mapped[str | None] = mapped_column(String(40))
    peer_user_id: Mapped[str | None] = mapped_column(String(160))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    follow_up: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(20))
    provider_call_id: Mapped[str | None] = mapped_column(String(80))
    recording_file_id: Mapped[str | None] = mapped_column(String(80))
    recording_consent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    recording_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript: Mapped[str | None] = mapped_column(Text)
    ai_request_id: Mapped[str | None] = mapped_column(String(80))
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("call_log"),)


class CallOptOutRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "call_opt_outs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    masked: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("call_opt_outs"),)


class CallProviderAccountRecord(Base):
    __tablename__ = "call_provider_accounts"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    route_id: Mapped[str] = mapped_column(String(40), nullable=False)
    account_sid: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_sid: Mapped[str | None] = mapped_column(String(64))
    twiml_app_sid: Mapped[str | None] = mapped_column(String(64))
    from_number: Mapped[str] = mapped_column(String(40), nullable=False)
    credentials_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_hint: Mapped[str] = mapped_column(String(40), nullable=False)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    transcription_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recording_retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_calls_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inbound_ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ai_max_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_max_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_per_minute_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    greeting: Mapped[str | None] = mapped_column(String(500))
    last_check_status: Mapped[str | None] = mapped_column(String(20))
    last_check_detail: Mapped[str | None] = mapped_column(String(500))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("call_provider_accounts"),)


class CallAiRequestRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "call_ai_requests"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_id: Mapped[str] = mapped_column(String(80), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_normalized: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    max_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    call_id: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("call_ai_requests"),)


ModelT = TypeVar("ModelT", bound=BaseModel)
_JSON_FIELDS = {"ai_consent", "follow_up", "summary_provenance", "recording_consent"}


def _values(model: BaseModel) -> dict[str, Any]:
    """Column values of a model: JSONB columns as JSON (dates as strings), the rest as Python."""
    values = model.model_dump(mode="python")
    for name in _JSON_FIELDS & set(values):
        nested = getattr(model, name)
        values[name] = nested.model_dump(mode="json") if nested is not None else None
    for name, value in list(values.items()):
        if hasattr(value, "value") and not isinstance(value, (int, str)):  # StrEnum is a str already
            values[name] = value.value
    return values


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


_ROUTE = text("select tenant_id, workspace_id from call_provider_accounts where route_id = :route_id")


def discover_route(route_id: str) -> tuple[str, str] | None:
    rows = discover(default_session_factory, _ROUTE, {"route_id": route_id})
    return (rows[0].tenant_id, rows[0].workspace_id) if rows else None


class SqlAlchemyCallsStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.tenant_id = context.tenant_id
        self.workspace_id = context.workspace_id

    def _scope(self, record: Any) -> list[Any]:
        return [record.tenant_id == self.tenant_id, record.workspace_id == self.workspace_id]

    def _one(self, record: Any, key: Any, value: str, *, lock: bool = False) -> Any:
        statement = select(record).where(*self._scope(record), key == value)
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def _save(self, record_type: Any, model: BaseModel, key: Any) -> None:
        row = self._one(record_type, record_type.id, key)
        if row is None:
            return
        for name, value in _values(model).items():
            setattr(row, name, value)
        self.session.flush()

    def _page(self, record: Any, model: type[ModelT], where: list[Any], order: list[Any], offset: int, limit: int) -> tuple[list[ModelT], int]:
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(*order).offset(offset).limit(limit)).all()
        return [model.model_validate(row) for row in rows], total

    # -- contacts
    def add_contact(self, contact: CallContact) -> CallContact:
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        self.session.add(CallContactRecord(**_values(contact)))
        self.session.flush()
        return contact

    def get_contact(self, contact_id: str) -> CallContact | None:
        row = self._one(CallContactRecord, CallContactRecord.id, contact_id)
        return CallContact.model_validate(row) if row is not None else None

    def save_contact(self, contact: CallContact) -> CallContact:
        self._save(CallContactRecord, contact, contact.id)
        return contact

    def delete_contact(self, contact_id: str) -> bool:
        result = self.session.execute(
            delete(CallContactRecord).where(*self._scope(CallContactRecord), CallContactRecord.id == contact_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def list_contacts(self, *, query: str | None, favourites: bool, offset: int, limit: int) -> tuple[list[CallContact], int]:
        where = self._scope(CallContactRecord)
        if favourites:
            where.append(CallContactRecord.favourite.is_(True))
        if query:
            pattern = _like(query)
            where.append(or_(*(column.ilike(pattern, escape="\\") for column in (
                CallContactRecord.name, CallContactRecord.phone, CallContactRecord.phone_normalized,
                CallContactRecord.company, CallContactRecord.email,
            ))))
        order = [CallContactRecord.favourite.desc(), func.lower(CallContactRecord.name), CallContactRecord.id]
        return self._page(CallContactRecord, CallContact, where, order, offset, limit)

    def contacts_by_number(self, number: str) -> list[CallContact]:
        rows = self.session.scalars(
            select(CallContactRecord).where(*self._scope(CallContactRecord), CallContactRecord.phone_normalized == number)
        ).all()
        return [CallContact.model_validate(row) for row in rows]

    # -- call log
    def add_call(self, call: CallEntry) -> CallEntry:
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        self.session.add(CallLogRecord(**_values(call)))
        self.session.flush()
        return call

    def get_call(self, call_id: str, *, lock: bool = False) -> CallEntry | None:
        row = self._one(CallLogRecord, CallLogRecord.id, call_id, lock=lock)
        return CallEntry.model_validate(row) if row is not None else None

    def save_call(self, call: CallEntry) -> CallEntry:
        self._save(CallLogRecord, call, call.id)
        return call

    def delete_call(self, call_id: str) -> bool:
        result = self.session.execute(delete(CallLogRecord).where(*self._scope(CallLogRecord), CallLogRecord.id == call_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def list_calls(self, flt: CallFilter, *, offset: int, limit: int) -> tuple[list[CallEntry], int]:
        where = self._scope(CallLogRecord)
        for column, value in (
            (CallLogRecord.direction, flt.direction), (CallLogRecord.outcome, flt.outcome), (CallLogRecord.kind, flt.kind),
            (CallLogRecord.contact_id, flt.contact_id), (CallLogRecord.user_id, flt.user_id),
        ):
            if value is not None:
                where.append(column == (value.value if hasattr(value, "value") else value))
        if flt.query:
            pattern = _like(flt.query)
            where.append(or_(*(column.ilike(pattern, escape="\\") for column in (
                CallLogRecord.contact_name, CallLogRecord.phone, CallLogRecord.phone_normalized, CallLogRecord.notes, CallLogRecord.summary,
            ))))
        return self._page(CallLogRecord, CallEntry, where, [CallLogRecord.started_at.desc(), CallLogRecord.id.desc()], offset, limit)

    def call_by_provider_id(self, provider_call_id: str) -> CallEntry | None:
        row = self._one(CallLogRecord, CallLogRecord.provider_call_id, provider_call_id)
        return CallEntry.model_validate(row) if row is not None else None

    # -- opt-outs
    def add_opt_out(self, opt_out: OptOut) -> OptOut:
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        existing = self.find_opt_out(opt_out.phone_hash)
        if existing is not None:
            return existing
        self.session.add(CallOptOutRecord(**_values(opt_out)))
        self.session.flush()
        return opt_out

    def find_opt_out(self, phone_hash: str) -> OptOut | None:
        row = self._one(CallOptOutRecord, CallOptOutRecord.phone_hash, phone_hash)
        return OptOut.model_validate(row) if row is not None else None

    def list_opt_outs(self, *, offset: int, limit: int) -> tuple[list[OptOut], int]:
        return self._page(
            CallOptOutRecord, OptOut, self._scope(CallOptOutRecord),
            [CallOptOutRecord.created_at.desc(), CallOptOutRecord.id.desc()], offset, limit,
        )

    def delete_opt_out(self, opt_out_id: str) -> OptOut | None:
        row = self._one(CallOptOutRecord, CallOptOutRecord.id, opt_out_id)
        if row is None:
            return None
        found = OptOut.model_validate(row)
        self.session.delete(row)
        self.session.flush()
        return found

    # -- provider account
    def _provider_row(self) -> CallProviderAccountRecord | None:
        return self.session.scalar(select(CallProviderAccountRecord).where(*self._scope(CallProviderAccountRecord)))

    def get_provider(self) -> ProviderAccount | None:
        row = self._provider_row()
        return ProviderAccount.model_validate(row) if row is not None else None

    def save_provider(self, account: ProviderAccount) -> ProviderAccount:
        row = self._provider_row()
        values = _values(account)
        if row is None:
            require_workspace(self.session, self.tenant_id, self.workspace_id)
            self.session.add(CallProviderAccountRecord(**values))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self.session.flush()
        return account

    def delete_provider(self) -> bool:
        result = self.session.execute(delete(CallProviderAccountRecord).where(*self._scope(CallProviderAccountRecord)))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # -- AI call requests
    def add_request(self, request: AiCallRequest) -> AiCallRequest:
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        self.session.add(CallAiRequestRecord(**_values(request)))
        self.session.flush()
        return request

    def get_request(self, request_id: str, *, lock: bool = False) -> AiCallRequest | None:
        row = self._one(CallAiRequestRecord, CallAiRequestRecord.id, request_id, lock=lock)
        return AiCallRequest.model_validate(row) if row is not None else None

    def save_request(self, request: AiCallRequest) -> AiCallRequest:
        self._save(CallAiRequestRecord, request, request.id)
        return request

    def list_requests(self, *, offset: int, limit: int) -> tuple[list[AiCallRequest], int]:
        return self._page(
            CallAiRequestRecord, AiCallRequest, self._scope(CallAiRequestRecord),
            [CallAiRequestRecord.created_at.desc(), CallAiRequestRecord.id.desc()], offset, limit,
        )
