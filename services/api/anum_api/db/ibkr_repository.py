"""PostgreSQL store for IBKR live trading (migration ``0122_finance_ibkr_live``, docs/finance-live-trading.md#storage).

The session carries the tenant, workspace and user RLS context (``open_ibkr_stores`` opens it
with ``user_scoped=True``); every table also has a restrictive ``owner_user_id = anum.user_id``
policy, and every query here repeats the scope and the owner.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.ibkr_rules import BrokerConnection, BrokerEvent, BrokerOrder, BrokerSettings, Environment
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinanceBrokerSettingsRecord(Base):
    __tablename__ = "finance_broker_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceBrokerConnectionRecord(Base, _Owned):
    __tablename__ = "finance_broker_connections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    broker: Mapped[str] = mapped_column(String(10), default="ibkr")
    environment: Mapped[str] = mapped_column(String(10))
    gateway_host: Mapped[str] = mapped_column(String(80))
    secret: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10))
    session: Mapped[str] = mapped_column(String(20))
    session_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    session_checked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    trading_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    statement: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceBrokerOrderRecord(Base, _Owned):
    __tablename__ = "finance_broker_orders"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(80))
    environment: Mapped[str] = mapped_column(String(10))
    symbol: Mapped[str] = mapped_column(String(20))
    conid: Mapped[int] = mapped_column(BigInteger)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    limit_price: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    state: Mapped[str] = mapped_column(String(20))
    broker_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceBrokerEventRecord(Base, _Owned):
    __tablename__ = "finance_broker_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    environment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(String(400))
    ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    alert: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)
    read_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


RECORDS = (FinanceBrokerEventRecord, FinanceBrokerOrderRecord, FinanceBrokerConnectionRecord, FinanceBrokerSettingsRecord)


def _mine(record: type[Any], context: TenantContext) -> tuple[Any, ...]:
    return (record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id)


def _values(model: Any) -> dict[str, Any]:
    return {key: getattr(value, "value", value) for key, value in model.__dict__.items()}


def _load(record: Any, model: Any) -> Any:
    return model.model_validate({name: getattr(record, name) for name in model.model_fields})


class SqlAlchemyIbkrStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # settings
    def get_settings(self, context: TenantContext) -> BrokerSettings | None:
        row = self.session.scalar(select(FinanceBrokerSettingsRecord).where(*_mine(FinanceBrokerSettingsRecord, context)))
        return BrokerSettings.model_validate(row.data) if row else None

    def save_settings(self, context: TenantContext, settings: BrokerSettings, *, expected_version: int | None) -> bool:
        record = FinanceBrokerSettingsRecord
        data = settings.model_dump(mode="json")
        now = settings.updated_at or datetime.now().astimezone()
        if expected_version is None:
            statement = insert(record).values(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                              data=data, version=settings.version, updated_at=now)
            self.session.execute(statement.on_conflict_do_update(
                index_elements=["tenant_id", "workspace_id", "owner_user_id"], set_={"data": data, "version": settings.version, "updated_at": now}))
            return True
        if expected_version == 0:
            inserted = self.session.execute(
                insert(record).values(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                      data=data, version=settings.version, updated_at=now).on_conflict_do_nothing().returning(record.version))
            if inserted.first() is not None:
                return True
        result = self.session.execute(update(record).where(*_mine(record, context), record.version == expected_version)
                                      .values(data=data, version=settings.version, updated_at=now))
        return bool(result.rowcount)

    # connections
    def list_connections(self, context: TenantContext) -> list[BrokerConnection]:
        record = FinanceBrokerConnectionRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.created_at.desc()).limit(50))
        return [_load(r, BrokerConnection) for r in rows]

    def get_connection(self, context: TenantContext, connection_id: str) -> BrokerConnection | None:
        record = FinanceBrokerConnectionRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == connection_id))
        return _load(row, BrokerConnection) if row else None

    def active_connection(self, context: TenantContext, environment: Environment) -> BrokerConnection | None:
        record = FinanceBrokerConnectionRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.environment == environment.value, record.status == "active"))
        return _load(row, BrokerConnection) if row else None

    def save_connection(self, connection: BrokerConnection) -> bool:
        values = _values(connection)
        existing = self.session.get(FinanceBrokerConnectionRecord, connection.id)
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            self.session.flush()
            return True
        inserted = self.session.execute(insert(FinanceBrokerConnectionRecord).values(**values, broker="ibkr")
                                        .on_conflict_do_nothing().returning(FinanceBrokerConnectionRecord.id))
        return inserted.first() is not None

    # orders
    def get_order(self, context: TenantContext, order_id: str) -> BrokerOrder | None:
        record = FinanceBrokerOrderRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == order_id))
        return _load(row, BrokerOrder) if row else None

    def list_orders(self, context: TenantContext, *, environment: str | None, limit: int, offset: int) -> tuple[list[BrokerOrder], int]:
        record = FinanceBrokerOrderRecord
        where = [*_mine(record, context)]
        if environment is not None:
            where.append(record.environment == environment)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.created_at.desc(), record.id).offset(offset).limit(limit))
        return [_load(r, BrokerOrder) for r in rows], total

    def orders_for(self, context: TenantContext, environment: Environment) -> list[BrokerOrder]:
        record = FinanceBrokerOrderRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context), record.environment == environment.value)
                                    .order_by(record.created_at).limit(5000))
        return [_load(r, BrokerOrder) for r in rows]

    def add_order(self, order: BrokerOrder) -> bool:
        inserted = self.session.execute(insert(FinanceBrokerOrderRecord).values(**_values(order)).on_conflict_do_nothing()
                                        .returning(FinanceBrokerOrderRecord.id))
        return inserted.first() is not None

    def save_order(self, order: BrokerOrder) -> None:
        existing = self.session.get(FinanceBrokerOrderRecord, order.id)
        if existing is None:
            self.add_order(order)
            return
        for key, value in _values(order).items():
            setattr(existing, key, value)
        self.session.flush()

    # events
    def add_event(self, event: BrokerEvent) -> None:
        self.session.execute(insert(FinanceBrokerEventRecord).values(**_values(event)))

    def list_events(self, context: TenantContext, *, alerts_only: bool, limit: int, offset: int) -> tuple[list[BrokerEvent], int]:
        record = FinanceBrokerEventRecord
        where = [*_mine(record, context)]
        if alerts_only:
            where.append(record.alert.is_(True))
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.created_at.desc(), record.id).offset(offset).limit(limit))
        return [_load(r, BrokerEvent) for r in rows], total

    def unread_alerts(self, context: TenantContext) -> int:
        record = FinanceBrokerEventRecord
        return int(self.session.scalar(select(func.count()).select_from(record).where(
            *_mine(record, context), record.alert.is_(True), record.read_at.is_(None))) or 0)

    def mark_read(self, context: TenantContext, now: datetime) -> int:
        record = FinanceBrokerEventRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.alert.is_(True), record.read_at.is_(None))
                                      .values(read_at=now))
        return int(result.rowcount or 0)

    # privacy
    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)


__all__ = ["RECORDS", "SqlAlchemyIbkrStore"]
