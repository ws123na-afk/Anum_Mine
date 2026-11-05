"""PostgreSQL purchase store (migration ``0136_purchases``, docs/purchases.md#storage).

The session carries the tenant, workspace and user RLS context (``open_purchase_store`` opens it
with ``user_scoped=True``; a finance link's unit of work is user scoped too). The restrictive
policies admit a business purchase and its rows to the workspace and a personal one only to its
owner; the queries here repeat the scope so a missing policy would still not mix workspaces up.
The history (``purchase_events``) is append-only: the database refuses every update.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, SmallInteger, String, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.purchases.models import OPEN_RETURN, Event, Item, Ownership, PaymentRef, Preferences, Purchase, ReminderSent, Return, Shipment
from anum_api.purchases.store import GROUPS, MAX_PURCHASES, MERCHANT_LIMIT, DuplicateError, ListFilters, export_records
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class _Scoped:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class _Child(_Scoped):
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    purchase_id: Mapped[str] = mapped_column(String(80))
    visibility: Mapped[str] = mapped_column(String(10))
    purchase_owner_id: Mapped[str] = mapped_column(String(160))


class PurchaseRecord(Base, _Scoped):
    __tablename__ = "purchases"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    ownership: Mapped[str] = mapped_column(String(10))
    order_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    merchant: Mapped[str] = mapped_column(String(120))
    ordered_on: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    shipping_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expected_delivery_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivered_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    return_window_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(8))
    needs_review: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    idem_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class PurchaseItemRecord(Base, _Child):
    __tablename__ = "purchase_items"

    position: Mapped[int] = mapped_column(SmallInteger)
    description: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(60), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)


class PurchaseEventRecord(Base, _Child):
    __tablename__ = "purchase_events"

    kind: Mapped[str] = mapped_column(String(10))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(8))
    occurred_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS)


class PurchaseShipmentRecord(Base, _Child):
    __tablename__ = "purchase_shipments"

    carrier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    shipped_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivered_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class PurchaseReturnRecord(Base, _Child):
    __tablename__ = "purchase_returns"

    status: Mapped[str] = mapped_column(String(20))
    requested_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(20))
    reason_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    items: Mapped[list[Any]] = mapped_column(JSONB)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    deadline_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    pickup_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    courier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    proof_document_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    refund_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    returned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_refund_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    refunded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    dispute_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refunds: Mapped[list[Any]] = mapped_column(JSONB)
    idem_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class PurchaseReminderRecord(Base, _Child):
    __tablename__ = "purchase_reminders"

    kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(80))
    due_on: Mapped[date] = mapped_column(Date)
    step: Mapped[int] = mapped_column(SmallInteger)
    sent_at: Mapped[datetime] = mapped_column(TS)


class PurchasePreferencesRecord(Base):
    __tablename__ = "purchase_preferences"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    reminders: Mapped[bool] = mapped_column(Boolean)
    lead_days: Mapped[int] = mapped_column(SmallInteger)
    warranty_days: Mapped[int] = mapped_column(SmallInteger)
    default_currency: Mapped[str] = mapped_column(String(3))
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


def _row(record: Any) -> dict[str, Any]:
    return {column.key: getattr(record, column.key) for column in record.__table__.columns}


def _values(model: Any) -> dict[str, Any]:
    out = model.model_dump(mode="python")
    if "payment" in out:
        out["payment"] = model.payment.model_dump(mode="json") if model.payment is not None else None
    if isinstance(model, Return):
        out["items"] = [line.model_dump(mode="json") for line in model.items]
        out["refunds"] = [ref.model_dump(mode="json") for ref in model.refunds]
    for key in ("visibility", "ownership"):
        if key in out:
            value = getattr(model, key)
            out[key] = str(value.value if hasattr(value, "value") else value)
    return out


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlPurchaseStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, table: Any, context: TenantContext) -> Any:
        return and_(table.tenant_id == context.tenant_id, table.workspace_id == context.workspace_id)

    def _visible_purchase(self, context: TenantContext) -> Any:
        return and_(self._scope(PurchaseRecord, context),
                    or_(PurchaseRecord.ownership == Ownership.BUSINESS.value, PurchaseRecord.owner_user_id == context.user_id))

    def _visible(self, table: Any, context: TenantContext) -> Any:
        return and_(self._scope(table, context), or_(table.visibility == Ownership.BUSINESS.value, table.purchase_owner_id == context.user_id))

    # purchases ---------------------------------------------------------------------------------

    def _filters(self, context: TenantContext, query: str | None, status: str | None, archived: bool) -> list[Any]:
        where = [self._visible_purchase(context), PurchaseRecord.archived_at.is_not(None) if archived else PurchaseRecord.archived_at.is_(None)]
        if query:
            like = f"%{_escape(query)}%"
            where.append(or_(*(column.ilike(like, escape="\\") for column in (PurchaseRecord.merchant, PurchaseRecord.order_ref,
                                                                               PurchaseRecord.notes, PurchaseRecord.address_ref))))
        if status:
            group = GROUPS.get(status)
            where.append(PurchaseRecord.status.in_(sorted(group)) if group is not None else PurchaseRecord.status == status)
        return where

    def _extra(self, context: TenantContext, filters: ListFilters | None) -> list[Any]:
        if filters is None:
            return []
        where: list[Any] = []
        if filters.merchant:
            where.append(func.lower(PurchaseRecord.merchant) == filters.merchant.lower())
        if filters.ordered_from:
            where.append(PurchaseRecord.ordered_on >= filters.ordered_from)
        if filters.ordered_to:
            where.append(PurchaseRecord.ordered_on <= filters.ordered_to)
        if filters.source:
            where.append(PurchaseRecord.source == filters.source)
        if filters.payment:
            active = PurchaseRecord.payment["status"].astext == "active"
            if filters.payment == "linked":
                where.append(and_(PurchaseRecord.payment.is_not(None), active))
            elif filters.payment == "unlinked":
                where.append(or_(PurchaseRecord.payment.is_(None), PurchaseRecord.payment["status"].astext == "void"))
            else:
                where.append(PurchaseRecord.payment_method == filters.payment)
        if filters.warranty_on is not None:
            item_under_warranty = select(PurchaseItemRecord.id).where(
                self._visible(PurchaseItemRecord, context), PurchaseItemRecord.purchase_id == PurchaseRecord.id,
                PurchaseItemRecord.warranty_until >= filters.warranty_on).exists()
            where.append(or_(PurchaseRecord.warranty_until >= filters.warranty_on, item_under_warranty))
        return where

    def list_purchases(self, context: TenantContext, *, query: str | None, status: str | None, archived: bool, limit: int,
                       offset: int, filters: ListFilters | None = None) -> tuple[list[Purchase], int]:
        where = [*self._filters(context, query, status, archived), *self._extra(context, filters)]
        total = self.session.scalar(select(func.count()).select_from(PurchaseRecord).where(*where)) or 0
        rows = self.session.scalars(select(PurchaseRecord).where(*where).order_by(
            PurchaseRecord.ordered_on.desc(), PurchaseRecord.created_at.desc(), PurchaseRecord.id.desc()).limit(limit).offset(offset)).all()
        return [Purchase.model_validate(_row(r)) for r in rows], int(total)

    def merchants(self, context: TenantContext) -> list[str]:
        names = self.session.scalars(select(PurchaseRecord.merchant).where(self._visible_purchase(context), PurchaseRecord.archived_at.is_(None))
                                     .group_by(PurchaseRecord.merchant).order_by(func.lower(PurchaseRecord.merchant)).limit(MERCHANT_LIMIT)).all()
        return list(names)

    def all_purchases(self, context: TenantContext) -> list[Purchase]:
        rows, _ = self.list_purchases(context, query=None, status=None, archived=False, limit=MAX_PURCHASES, offset=0)
        return rows

    def get_purchase(self, context: TenantContext, purchase_id: str) -> Purchase | None:
        row = self.session.scalar(select(PurchaseRecord).where(self._visible_purchase(context), PurchaseRecord.id == purchase_id))
        return Purchase.model_validate(_row(row)) if row is not None else None

    def lock_purchase(self, context: TenantContext, purchase_id: str) -> None:
        """Serialise status changes, returns and refunds of one purchase (the row lock is released at commit)."""
        self.session.execute(select(PurchaseRecord.id).where(self._visible_purchase(context), PurchaseRecord.id == purchase_id).with_for_update())

    def purchase_by_idem(self, context: TenantContext, idem_key: str) -> Purchase | None:
        row = self.session.scalar(select(PurchaseRecord).where(self._scope(PurchaseRecord, context), PurchaseRecord.owner_user_id == context.user_id,
                                                               PurchaseRecord.idem_key == idem_key))
        return Purchase.model_validate(_row(row)) if row is not None else None

    def find_by_order_ref(self, context: TenantContext, order_ref: str) -> list[Purchase]:
        rows = self.session.scalars(select(PurchaseRecord).where(self._visible_purchase(context),
                                                                 func.upper(PurchaseRecord.order_ref) == order_ref.upper())
                                    .order_by(PurchaseRecord.ordered_on.desc(), PurchaseRecord.created_at.desc()).limit(20)).all()
        return [Purchase.model_validate(_row(r)) for r in rows]

    def add_purchase(self, purchase: Purchase) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(PurchaseRecord.__table__.insert().values(**_values(purchase)))
        except IntegrityError as exc:
            if "uq_purchases_idem" in str(exc.orig):
                raise DuplicateError("idem_key") from exc
            raise

    def save_purchase(self, purchase: Purchase, expected_version: int) -> bool:
        values = _values(purchase)
        values["version"] = expected_version + 1
        # The payment's read copy is the link's to keep: an edit never writes it back.
        for key in ("id", "tenant_id", "workspace_id", "owner_user_id", "ownership", "created_at", "idem_key", "payment"):
            values.pop(key)
        result = self.session.execute(update(PurchaseRecord).where(self._visible_purchase(self.context), PurchaseRecord.id == purchase.id,
                                                                   PurchaseRecord.version == expected_version).values(**values))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete_purchase(self, context: TenantContext, purchase_id: str) -> bool:
        result = self.session.execute(delete(PurchaseRecord).where(self._visible_purchase(context), PurchaseRecord.id == purchase_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def set_payment(self, context: TenantContext, purchase_id: str, payment: PaymentRef | None) -> bool:
        result = self.session.execute(update(PurchaseRecord).where(self._visible_purchase(context), PurchaseRecord.id == purchase_id)
                                      .values(payment=payment.model_dump(mode="json") if payment else None))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # items -------------------------------------------------------------------------------------

    def items(self, context: TenantContext, purchase_ids: list[str]) -> list[Item]:
        if not purchase_ids:
            return []
        table = PurchaseItemRecord
        rows = self.session.scalars(select(table).where(self._visible(table, context), table.purchase_id.in_(purchase_ids))
                                    .order_by(table.purchase_id, table.position, table.id)).all()
        return [Item.model_validate(_row(r)) for r in rows]

    def replace_items(self, context: TenantContext, purchase_id: str, items: list[Item]) -> None:
        table = PurchaseItemRecord
        self.session.execute(delete(table).where(self._visible(table, context), table.purchase_id == purchase_id))
        for item in items:
            self.session.execute(table.__table__.insert().values(**_values(item)))

    # events ------------------------------------------------------------------------------------

    def events(self, context: TenantContext, purchase_id: str, *, limit: int, offset: int) -> tuple[list[Event], int]:
        table = PurchaseEventRecord
        where = [self._visible(table, context), table.purchase_id == purchase_id]
        total = self.session.scalar(select(func.count()).select_from(table).where(*where)) or 0
        rows = self.session.scalars(select(table).where(*where).order_by(table.occurred_at.desc(), table.created_at.desc(), table.id.desc())
                                    .limit(limit).offset(offset)).all()
        return [Event.model_validate(_row(r)) for r in rows], int(total)

    def add_event(self, event: Event) -> None:
        self.session.execute(PurchaseEventRecord.__table__.insert().values(**_values(event)))

    def share_seen(self, context: TenantContext, marker: str) -> str | None:
        table = PurchaseEventRecord
        return self.session.scalar(select(table.purchase_id).where(self._scope(table, context), table.owner_user_id == context.user_id,
                                                                   table.kind == "share", table.subject_id == marker).limit(1))

    # shipments ---------------------------------------------------------------------------------

    def shipments(self, context: TenantContext, purchase_ids: list[str]) -> list[Shipment]:
        if not purchase_ids:
            return []
        table = PurchaseShipmentRecord
        rows = self.session.scalars(select(table).where(self._visible(table, context), table.purchase_id.in_(purchase_ids))
                                    .order_by(table.purchase_id, table.created_at, table.id)).all()
        return [Shipment.model_validate(_row(r)) for r in rows]

    def get_shipment(self, context: TenantContext, shipment_id: str) -> Shipment | None:
        table = PurchaseShipmentRecord
        row = self.session.scalar(select(table).where(self._visible(table, context), table.id == shipment_id))
        return Shipment.model_validate(_row(row)) if row is not None else None

    def find_by_tracking(self, context: TenantContext, tracking_number: str) -> list[Shipment]:
        table = PurchaseShipmentRecord
        rows = self.session.scalars(select(table).where(self._visible(table, context), func.upper(table.tracking_number) == tracking_number.upper())
                                    .limit(20)).all()
        return [Shipment.model_validate(_row(r)) for r in rows]

    def add_shipment(self, shipment: Shipment) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(PurchaseShipmentRecord.__table__.insert().values(**_values(shipment)))
        except IntegrityError as exc:
            if "uq_purchase_shipments_tracking" in str(exc.orig):
                raise DuplicateError("tracking_number") from exc
            raise

    def save_shipment(self, shipment: Shipment, expected_version: int) -> bool:
        table = PurchaseShipmentRecord
        values = _values(shipment)
        values["version"] = expected_version + 1
        for key in ("id", "tenant_id", "workspace_id", "owner_user_id", "purchase_id", "visibility", "purchase_owner_id", "created_at"):
            values.pop(key)
        try:
            with self.session.begin_nested():
                result = self.session.execute(update(table).where(self._visible(table, self.context), table.id == shipment.id,
                                                                  table.version == expected_version).values(**values))
        except IntegrityError as exc:
            if "uq_purchase_shipments_tracking" in str(exc.orig):
                raise DuplicateError("tracking_number") from exc
            raise
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete_shipment(self, context: TenantContext, shipment_id: str) -> bool:
        table = PurchaseShipmentRecord
        result = self.session.execute(delete(table).where(self._visible(table, context), table.id == shipment_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # returns -----------------------------------------------------------------------------------

    def returns(self, context: TenantContext, purchase_ids: list[str]) -> list[Return]:
        if not purchase_ids:
            return []
        table = PurchaseReturnRecord
        rows = self.session.scalars(select(table).where(self._visible(table, context), table.purchase_id.in_(purchase_ids))
                                    .order_by(table.purchase_id, table.created_at, table.id)).all()
        return [Return.model_validate(_row(r)) for r in rows]

    def list_returns(self, context: TenantContext, *, open_only: bool, limit: int, offset: int) -> tuple[list[Return], int]:
        table = PurchaseReturnRecord
        where = [self._visible(table, context)]
        if open_only:
            where.append(table.status.in_(sorted(OPEN_RETURN)))
        total = self.session.scalar(select(func.count()).select_from(table).where(*where)) or 0
        rows = self.session.scalars(select(table).where(*where).order_by(table.requested_on.desc(), table.created_at.desc(), table.id.desc())
                                    .limit(limit).offset(offset)).all()
        return [Return.model_validate(_row(r)) for r in rows], int(total)

    def get_return(self, context: TenantContext, return_id: str) -> Return | None:
        table = PurchaseReturnRecord
        row = self.session.scalar(select(table).where(self._visible(table, context), table.id == return_id))
        return Return.model_validate(_row(row)) if row is not None else None

    def return_by_idem(self, context: TenantContext, idem_key: str) -> Return | None:
        table = PurchaseReturnRecord
        row = self.session.scalar(select(table).where(self._scope(table, context), table.owner_user_id == context.user_id, table.idem_key == idem_key))
        return Return.model_validate(_row(row)) if row is not None else None

    def add_return(self, item: Return) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(PurchaseReturnRecord.__table__.insert().values(**_values(item)))
        except IntegrityError as exc:
            if "uq_purchase_returns_idem" in str(exc.orig):
                raise DuplicateError("idem_key") from exc
            raise

    def save_return(self, item: Return, expected_version: int) -> bool:
        table = PurchaseReturnRecord
        values = _values(item)
        values["version"] = expected_version + 1
        # The refunds' read copy is the links' to keep: an edit never writes it back.
        for key in ("id", "tenant_id", "workspace_id", "owner_user_id", "purchase_id", "visibility", "purchase_owner_id", "created_at", "idem_key",
                    "refunds"):
            values.pop(key)
        result = self.session.execute(update(table).where(self._visible(table, self.context), table.id == item.id,
                                                          table.version == expected_version).values(**values))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete_return(self, context: TenantContext, return_id: str) -> bool:
        table = PurchaseReturnRecord
        result = self.session.execute(delete(table).where(self._visible(table, context), table.id == return_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def set_refunds(self, context: TenantContext, return_id: str, refunds: list[PaymentRef]) -> bool:
        table = PurchaseReturnRecord
        result = self.session.execute(update(table).where(self._visible(table, context), table.id == return_id)
                                      .values(refunds=[r.model_dump(mode="json") for r in refunds]))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # reminders and preferences -----------------------------------------------------------------

    def reminder_sent(self, reminder: ReminderSent) -> bool:
        table = PurchaseReminderRecord
        return (self.session.scalar(select(func.count()).select_from(table).where(
            table.purchase_id == reminder.purchase_id, table.kind == reminder.kind, table.source_id == reminder.source_id,
            table.due_on == reminder.due_on, table.step == reminder.step)) or 0) > 0

    def add_reminder(self, reminder: ReminderSent) -> bool:
        try:
            with self.session.begin_nested():
                self.session.execute(PurchaseReminderRecord.__table__.insert().values(**_values(reminder)))
        except IntegrityError:
            return False
        return True

    def get_preferences(self, context: TenantContext) -> Preferences | None:
        row = self.session.get(PurchasePreferencesRecord, (context.tenant_id, context.workspace_id, context.user_id))
        return Preferences.model_validate(_row(row)) if row is not None else None

    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool:
        values = _values(preferences)
        values["version"] = expected_version + 1
        table = PurchasePreferencesRecord
        if expected_version == 0:
            try:
                with self.session.begin_nested():
                    self.session.execute(table.__table__.insert().values(**values))
            except IntegrityError:
                return False
            return True
        result = self.session.execute(update(table).where(table.tenant_id == preferences.tenant_id, table.workspace_id == preferences.workspace_id,
                                                          table.owner_user_id == preferences.owner_user_id, table.version == expected_version)
                                      .values(**values))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # privacy -----------------------------------------------------------------------------------

    CHILD_TABLES = (("items", PurchaseItemRecord, Item), ("events", PurchaseEventRecord, Event), ("shipments", PurchaseShipmentRecord, Shipment),
                    ("returns", PurchaseReturnRecord, Return), ("reminders", PurchaseReminderRecord, ReminderSent))

    @staticmethod
    def _theirs(table: Any, context: TenantContext, personal: list[str], name: str, deleting: bool = False) -> Any:
        """Rows of the person's personal purchases, and the rows they wrote on business purchases. Items are part of a purchase:
        a business purchase keeps them."""
        if name == "items":
            return table.purchase_id.in_(personal)
        if name == "returns" and deleting:
            # A return carrying refunds is part of the purchase owner's money record: it stays with the business purchase.
            return or_(table.purchase_id.in_(personal), and_(table.owner_user_id == context.user_id, func.jsonb_array_length(table.refunds) == 0))
        return or_(table.owner_user_id == context.user_id, table.purchase_id.in_(personal))

    def _mine(self, context: TenantContext, *, deleting: bool = False) -> dict[str, list[Any]]:
        purchases = [Purchase.model_validate(_row(r)) for r in self.session.scalars(
            select(PurchaseRecord).where(self._scope(PurchaseRecord, context), PurchaseRecord.owner_user_id == context.user_id)).all()]
        personal = [p.id for p in purchases if p.ownership == Ownership.PERSONAL]
        out: dict[str, list[Any]] = {"purchases": purchases}
        for name, table, model in self.CHILD_TABLES:
            rows = self.session.scalars(select(table).where(self._scope(table, context), self._theirs(table, context, personal, name, deleting))).all()
            out[name] = [model.model_validate(_row(r)) for r in rows]
        preferences = self.get_preferences(context)
        out["preferences"] = [preferences] if preferences else []
        return out

    def count_everything(self, context: TenantContext) -> int:
        return sum(len(rows) for rows in self._mine(context).values())

    def delete_everything(self, context: TenantContext) -> int:
        mine = self._mine(context, deleting=True)
        removed = sum(len(rows) for name, rows in mine.items() if name != "purchases")
        personal = [p.id for p in mine["purchases"] if p.ownership == Ownership.PERSONAL]
        for name, table, _model in reversed(self.CHILD_TABLES):
            self.session.execute(delete(table).where(self._scope(table, context), self._theirs(table, context, personal, name, deleting=True)))
        self.session.execute(delete(PurchasePreferencesRecord).where(self._scope(PurchasePreferencesRecord, context),
                                                                     PurchasePreferencesRecord.owner_user_id == context.user_id))
        if personal:
            self.session.execute(delete(PurchaseRecord).where(self._scope(PurchaseRecord, context), PurchaseRecord.id.in_(personal)))
        return removed + len(personal)

    def export(self, context: TenantContext) -> dict[str, Any]:
        return export_records(self._mine(context))


__all__ = ["SqlPurchaseStore"]
