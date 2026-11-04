"""PostgreSQL vehicle store (migration ``0135_vehicles``, docs/vehicles.md#storage).

The session carries the tenant, workspace and user RLS context (``open_vehicle_store`` opens it
with ``user_scoped=True``; a finance link's unit of work is user scoped too). The restrictive
policies admit a business vehicle and its records to the workspace and a personal one only to its
owner; the queries here repeat the scope so a missing policy would still not mix workspaces up.
The odometer trigger refuses a reading that would make the odometer go down.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, SmallInteger, String, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.schemas import TenantContext
from anum_api.vehicles.models import FuelEntry, OdometerReading, Ownership, PaymentRef, Preferences, ReminderSent, ServiceRecord, Trip, Vehicle
from anum_api.vehicles.store import MAX_RECORDS_READ, DuplicateError, OdometerRefused, RecordKind, export_records

from .models import Base

TS = DateTime(timezone=True)


class _Scoped:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class _Child(_Scoped):
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(80))
    visibility: Mapped[str] = mapped_column(String(10))
    vehicle_owner_id: Mapped[str] = mapped_column(String(160))


class VehicleRecord(Base, _Scoped):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    ownership: Mapped[str] = mapped_column(String(10))
    registration: Mapped[str] = mapped_column(String(20))
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    make: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    vehicle_type: Mapped[str] = mapped_column(String(12))
    country: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    fuel_type: Mapped[str] = mapped_column(String(12))
    tank_litres: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    residual_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    useful_life_months: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    purchase_payment: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    insurance_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    inspection_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class VehicleOdometerRecord(Base, _Child):
    __tablename__ = "vehicle_odometer_readings"

    reading_km: Mapped[Decimal] = mapped_column(Numeric(10, 1))
    read_at: Mapped[datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(12))
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    correction: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)


class VehicleFuelRecord(Base, _Child):
    __tablename__ = "vehicle_fuel_entries"

    filled_at: Mapped[datetime] = mapped_column(TS)
    station: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fuel_type: Mapped[str] = mapped_column(String(12))
    litres: Mapped[Decimal] = mapped_column(Numeric(9, 3))
    price_per_litre: Mapped[Decimal | None] = mapped_column(Numeric(9, 3), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    full_tank: Mapped[bool] = mapped_column(Boolean)
    payment_method: Mapped[str | None] = mapped_column(String(14), nullable=True)
    payment: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idem_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class VehicleTripRecord(Base, _Child):
    __tablename__ = "vehicle_trips"

    status: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    start_odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    end_odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    distance_basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    purpose: Mapped[str] = mapped_column(String(10))
    purpose_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(8))
    device_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idem_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class VehicleServiceRecordRow(Base, _Child):
    __tablename__ = "vehicle_service_records"

    kind: Mapped[str] = mapped_column(String(14))
    service_on: Mapped[date] = mapped_column(Date)
    odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    workshop: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    next_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_due_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    trip_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    receipt_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    payment_method: Mapped[str | None] = mapped_column(String(14), nullable=True)
    payment: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idem_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class VehicleReminderRecord(Base, _Child):
    __tablename__ = "vehicle_reminders"

    kind: Mapped[str] = mapped_column(String(14))
    source_id: Mapped[str] = mapped_column(String(80))
    due_on: Mapped[date] = mapped_column(Date)
    step: Mapped[int] = mapped_column(SmallInteger)
    sent_at: Mapped[datetime] = mapped_column(TS)


class VehiclePreferencesRecord(Base):
    __tablename__ = "vehicle_preferences"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trip_places: Mapped[bool] = mapped_column(Boolean)
    default_vehicle_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reminder_days: Mapped[int] = mapped_column(SmallInteger)
    reminders: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


TABLES: dict[str, Any] = {"fuel": VehicleFuelRecord, "trips": VehicleTripRecord, "service": VehicleServiceRecordRow}
MODELS: dict[str, Any] = {"fuel": FuelEntry, "trips": Trip, "service": ServiceRecord}
TIME_COLUMN = {"fuel": "filled_at", "trips": "started_at", "service": "service_on"}
TEXT_COLUMNS = {"fuel": ("station", "notes", "fuel_type"), "trips": ("purpose_note", "origin", "destination", "notes", "project_id", "purpose"),
                "service": ("workshop", "description", "reference", "kind")}


def _row(record: Any) -> dict[str, Any]:
    return {column.key: getattr(record, column.key) for column in record.__table__.columns}


def _values(model: Any) -> dict[str, Any]:
    out = model.model_dump()
    for key in ("payment", "purchase_payment"):
        if key in out:
            value = getattr(model, key)
            out[key] = value.model_dump(mode="json") if value is not None else None
    for key in ("visibility", "ownership"):
        if key in out:
            out[key] = str(getattr(model, key).value if hasattr(getattr(model, key), "value") else getattr(model, key))
    return out


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlVehicleStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, table: Any, context: TenantContext) -> Any:
        return and_(table.tenant_id == context.tenant_id, table.workspace_id == context.workspace_id)

    def _visible_vehicle(self, context: TenantContext) -> Any:
        return and_(self._scope(VehicleRecord, context),
                    or_(VehicleRecord.ownership == Ownership.BUSINESS.value, VehicleRecord.owner_user_id == context.user_id))

    def _visible(self, table: Any, context: TenantContext) -> Any:
        return and_(self._scope(table, context), or_(table.visibility == Ownership.BUSINESS.value, table.vehicle_owner_id == context.user_id))

    # vehicles ----------------------------------------------------------------------------------

    def list_vehicles(self, context: TenantContext, *, query: str | None, archived: bool, limit: int, offset: int) -> tuple[list[Vehicle], int]:
        where = [self._visible_vehicle(context), VehicleRecord.archived_at.is_not(None) if archived else VehicleRecord.archived_at.is_(None)]
        if query:
            like = f"%{_escape(query)}%"
            where.append(or_(*(column.ilike(like, escape="\\") for column in (VehicleRecord.registration, VehicleRecord.nickname,
                                                                               VehicleRecord.make, VehicleRecord.model))))
        total = self.session.scalar(select(func.count()).select_from(VehicleRecord).where(*where)) or 0
        rows = self.session.scalars(select(VehicleRecord).where(*where).order_by(func.upper(VehicleRecord.registration), VehicleRecord.id)
                                    .limit(limit).offset(offset)).all()
        return [Vehicle.model_validate(_row(r)) for r in rows], int(total)

    def get_vehicle(self, context: TenantContext, vehicle_id: str) -> Vehicle | None:
        row = self.session.scalar(select(VehicleRecord).where(self._visible_vehicle(context), VehicleRecord.id == vehicle_id))
        return Vehicle.model_validate(_row(row)) if row is not None else None

    def lock_vehicle(self, context: TenantContext, vehicle_id: str) -> None:
        self.session.execute(select(VehicleRecord.id).where(self._visible_vehicle(context), VehicleRecord.id == vehicle_id).with_for_update())

    def add_vehicle(self, vehicle: Vehicle) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(VehicleRecord.__table__.insert().values(**_values(vehicle)))
        except IntegrityError as exc:
            if "uq_vehicles_registration" in str(exc.orig):
                raise DuplicateError("registration") from exc
            raise

    def save_vehicle(self, vehicle: Vehicle, expected_version: int) -> bool:
        values = _values(vehicle)
        values["version"] = expected_version + 1
        for key in ("id", "tenant_id", "workspace_id", "owner_user_id", "ownership", "created_at"):
            values.pop(key)
        try:
            with self.session.begin_nested():
                result = self.session.execute(update(VehicleRecord).where(self._visible_vehicle(self.context), VehicleRecord.id == vehicle.id,
                                                                          VehicleRecord.version == expected_version).values(**values))
        except IntegrityError as exc:
            if "uq_vehicles_registration" in str(exc.orig):
                raise DuplicateError("registration") from exc
            raise
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete_vehicle(self, context: TenantContext, vehicle_id: str) -> bool:
        result = self.session.execute(delete(VehicleRecord).where(self._visible_vehicle(context), VehicleRecord.id == vehicle_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def set_purchase_payment(self, context: TenantContext, vehicle_id: str, payment: PaymentRef | None) -> bool:
        result = self.session.execute(update(VehicleRecord).where(self._visible_vehicle(context), VehicleRecord.id == vehicle_id)
                                      .values(purchase_payment=payment.model_dump(mode="json") if payment else None))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # odometer ----------------------------------------------------------------------------------

    def readings(self, context: TenantContext, vehicle_id: str) -> list[OdometerReading]:
        rows = self.session.scalars(select(VehicleOdometerRecord).where(self._visible(VehicleOdometerRecord, context),
                                                                        VehicleOdometerRecord.vehicle_id == vehicle_id)
                                    .order_by(VehicleOdometerRecord.read_at, VehicleOdometerRecord.created_at, VehicleOdometerRecord.id)).all()
        return [OdometerReading.model_validate(_row(r)) for r in rows]

    def add_reading(self, reading: OdometerReading) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(VehicleOdometerRecord.__table__.insert().values(**_values(reading)))
        except DBAPIError as exc:
            if "odometer_decrease" in str(exc.orig):
                raise OdometerRefused("odometer_decrease") from None
            raise

    def delete_readings(self, context: TenantContext, vehicle_id: str, source_id: str) -> int:
        result = self.session.execute(delete(VehicleOdometerRecord).where(self._visible(VehicleOdometerRecord, context),
                                                                          VehicleOdometerRecord.vehicle_id == vehicle_id,
                                                                          VehicleOdometerRecord.source_id == source_id))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # records -----------------------------------------------------------------------------------

    def list_records(self, context: TenantContext, kind: RecordKind, vehicle_id: str | None, *, query: str | None, start: date | None,
                     end: date | None, limit: int, offset: int) -> tuple[list[Any], int]:
        table = TABLES[kind]
        moment = getattr(table, TIME_COLUMN[kind])
        day = moment if kind == "service" else func.date(func.timezone("UTC", moment))
        where = [self._visible(table, context)]
        if vehicle_id is not None:
            where.append(table.vehicle_id == vehicle_id)
        if start is not None:
            where.append(day >= start)
        if end is not None:
            where.append(day <= end)
        if query:
            like = f"%{_escape(query)}%"
            where.append(or_(*(getattr(table, name).ilike(like, escape="\\") for name in TEXT_COLUMNS[kind])))
        total = self.session.scalar(select(func.count()).select_from(table).where(*where)) or 0
        rows = self.session.scalars(select(table).where(*where).order_by(moment.desc(), table.created_at.desc(), table.id.desc())
                                    .limit(limit).offset(offset)).all()
        return [MODELS[kind].model_validate(_row(r)) for r in rows], int(total)

    def records_for(self, context: TenantContext, kind: RecordKind, vehicle_ids: list[str]) -> list[Any]:
        if not vehicle_ids:
            return []
        table = TABLES[kind]
        moment = getattr(table, TIME_COLUMN[kind])
        rows = self.session.scalars(select(table).where(self._visible(table, context), table.vehicle_id.in_(vehicle_ids))
                                    .order_by(moment.desc(), table.created_at.desc(), table.id.desc()).limit(MAX_RECORDS_READ)).all()
        return [MODELS[kind].model_validate(_row(r)) for r in reversed(rows)]  # the newest ones when there are more, oldest first

    def get_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> Any | None:
        table = TABLES[kind]
        row = self.session.scalar(select(table).where(self._visible(table, context), table.id == record_id))
        return MODELS[kind].model_validate(_row(row)) if row is not None else None

    def record_by_idem(self, context: TenantContext, kind: RecordKind, idem_key: str) -> Any | None:
        table = TABLES[kind]
        row = self.session.scalar(select(table).where(self._visible(table, context), table.owner_user_id == context.user_id,
                                                      table.idem_key == idem_key))
        return MODELS[kind].model_validate(_row(row)) if row is not None else None

    def add_record(self, kind: RecordKind, record: Any) -> None:
        try:
            with self.session.begin_nested():
                self.session.execute(TABLES[kind].__table__.insert().values(**_values(record)))
        except IntegrityError as exc:
            if "uq_vehicle_trips_open" in str(exc.orig):
                raise DuplicateError("open_trip") from exc
            if "_idem" in str(exc.orig):
                raise DuplicateError("idem_key") from exc
            raise

    def save_record(self, kind: RecordKind, record: Any, expected_version: int) -> bool:
        table = TABLES[kind]
        values = _values(record)
        values["version"] = expected_version + 1
        for key in ("id", "tenant_id", "workspace_id", "owner_user_id", "vehicle_id", "visibility", "vehicle_owner_id", "created_at", "idem_key"):
            values.pop(key)
        result = self.session.execute(update(table).where(self._visible(table, self.context), table.id == record.id,
                                                          table.version == expected_version).values(**values))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def delete_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> bool:
        table = TABLES[kind]
        result = self.session.execute(delete(table).where(self._visible(table, context), table.id == record_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def set_payment(self, context: TenantContext, kind: RecordKind, record_id: str, payment: PaymentRef | None) -> bool:
        table = TABLES[kind]
        if not hasattr(table, "payment"):
            return False
        result = self.session.execute(update(table).where(self._visible(table, context), table.id == record_id)
                                      .values(payment=payment.model_dump(mode="json") if payment else None))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def open_trips(self, context: TenantContext, vehicle_id: str | None) -> list[Trip]:
        where = [self._visible(VehicleTripRecord, context), VehicleTripRecord.status == "open", VehicleTripRecord.owner_user_id == context.user_id]
        if vehicle_id is not None:
            where.append(VehicleTripRecord.vehicle_id == vehicle_id)
        return [Trip.model_validate(_row(r)) for r in self.session.scalars(select(VehicleTripRecord).where(*where)).all()]

    # reminders and preferences -----------------------------------------------------------------

    def reminder_sent(self, reminder: ReminderSent) -> bool:
        table = VehicleReminderRecord
        return self.session.scalar(select(func.count()).select_from(table).where(
            table.vehicle_id == reminder.vehicle_id, table.kind == reminder.kind, table.source_id == reminder.source_id,
            table.due_on == reminder.due_on, table.step == reminder.step)) > 0

    def add_reminder(self, reminder: ReminderSent) -> bool:
        try:
            with self.session.begin_nested():
                self.session.execute(VehicleReminderRecord.__table__.insert().values(**_values(reminder)))
        except IntegrityError:
            return False
        return True

    def get_preferences(self, context: TenantContext) -> Preferences | None:
        row = self.session.get(VehiclePreferencesRecord, (context.tenant_id, context.workspace_id, context.user_id))
        return Preferences.model_validate(_row(row)) if row is not None else None

    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool:
        values = _values(preferences)
        values["version"] = expected_version + 1
        table = VehiclePreferencesRecord
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

    def _mine(self, context: TenantContext) -> dict[str, list[Any]]:
        scope = self._scope(VehicleRecord, context)
        vehicles = [Vehicle.model_validate(_row(r)) for r in self.session.scalars(
            select(VehicleRecord).where(scope, VehicleRecord.owner_user_id == context.user_id)).all()]
        personal = [v.id for v in vehicles if v.ownership == Ownership.PERSONAL]
        out: dict[str, list[Any]] = {"vehicles": vehicles}
        for name, table, model in (("odometer", VehicleOdometerRecord, OdometerReading), ("fuel", VehicleFuelRecord, FuelEntry),
                                   ("trips", VehicleTripRecord, Trip), ("service", VehicleServiceRecordRow, ServiceRecord),
                                   ("reminders", VehicleReminderRecord, ReminderSent)):
            rows = self.session.scalars(select(table).where(self._scope(table, context), or_(
                table.owner_user_id == context.user_id, table.vehicle_id.in_(personal)))).all()
            out[name] = [model.model_validate(_row(r)) for r in rows]
        preferences = self.get_preferences(context)
        out["preferences"] = [preferences] if preferences else []
        return out

    def count_everything(self, context: TenantContext) -> int:
        return sum(len(rows) for rows in self._mine(context).values())

    def delete_everything(self, context: TenantContext) -> int:
        mine = self._mine(context)
        removed = sum(len(rows) for name, rows in mine.items() if name != "vehicles")
        personal = [v.id for v in mine["vehicles"] if v.ownership == Ownership.PERSONAL]
        for table in (VehicleReminderRecord, VehicleOdometerRecord, VehicleFuelRecord, VehicleServiceRecordRow, VehicleTripRecord):
            self.session.execute(delete(table).where(self._scope(table, context), or_(table.owner_user_id == context.user_id,
                                                                                     table.vehicle_id.in_(personal))))
        self.session.execute(delete(VehiclePreferencesRecord).where(self._scope(VehiclePreferencesRecord, context),
                                                                    VehiclePreferencesRecord.owner_user_id == context.user_id))
        if personal:
            self.session.execute(delete(VehicleRecord).where(self._scope(VehicleRecord, context), VehicleRecord.id.in_(personal)))
        return removed + len(personal)

    def export(self, context: TenantContext) -> dict[str, Any]:
        return export_records(self._mine(context))


__all__ = ["SqlVehicleStore"]
