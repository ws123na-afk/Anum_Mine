"""Vehicle storage: the protocol, the in-memory store and ``open_vehicle_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.vehicles_repository.SqlVehicleStore`` in a session whose tenant, workspace **and
user** RLS context is set (migration ``0135_vehicles``). A business vehicle and its records are
visible to the workspace; a personal vehicle and its records only to its owner. The in-memory
store applies the same rule for local development and tests.

Records are ``fuel`` (``vehicle_fuel_entries``), ``trips`` (``vehicle_trips``) and ``service``
(``vehicle_service_records``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from threading import RLock
from typing import Any, Literal, Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import FuelEntry, OdometerReading, Ownership, PaymentRef, Preferences, ReminderSent, ServiceRecord, Trip, Vehicle

RecordKind = Literal["fuel", "trips", "service"]
Record = FuelEntry | Trip | ServiceRecord
RECORD_MODELS: dict[str, type[Record]] = {"fuel": FuelEntry, "trips": Trip, "service": ServiceRecord}
MAX_VEHICLES = 500
MAX_RECORDS_READ = 20_000


class DuplicateError(ValueError):
    """A unique key (registration, idempotency key, open trip) is already taken."""


class OdometerRefused(ValueError):
    """The database refused a reading that would make the odometer go down."""


def record_time(kind: str, record: Any) -> datetime | date:
    return {"fuel": lambda r: r.filled_at, "trips": lambda r: r.started_at, "service": lambda r: r.service_on}[kind](record)


def record_text(kind: str, record: Any) -> str:
    if kind == "fuel":
        parts = [record.station, record.notes, record.fuel_type]
    elif kind == "trips":
        parts = [record.purpose_note, record.origin, record.destination, record.notes, record.project_id, record.purpose]
    else:
        parts = [record.workshop, record.description, record.reference, record.kind]
    return " ".join(p for p in parts if p).casefold()


class VehicleStore(Protocol):
    def list_vehicles(self, context: TenantContext, *, query: str | None, archived: bool, limit: int, offset: int) -> tuple[list[Vehicle], int]: ...
    def get_vehicle(self, context: TenantContext, vehicle_id: str) -> Vehicle | None: ...
    def add_vehicle(self, vehicle: Vehicle) -> None: ...
    def save_vehicle(self, vehicle: Vehicle, expected_version: int) -> bool: ...
    def delete_vehicle(self, context: TenantContext, vehicle_id: str) -> bool: ...
    def set_purchase_payment(self, context: TenantContext, vehicle_id: str, payment: PaymentRef | None) -> bool: ...

    def readings(self, context: TenantContext, vehicle_id: str) -> list[OdometerReading]: ...
    def add_reading(self, reading: OdometerReading) -> None: ...
    def delete_readings(self, context: TenantContext, vehicle_id: str, source_id: str) -> int: ...

    def list_records(self, context: TenantContext, kind: RecordKind, vehicle_id: str | None, *, query: str | None, start: date | None,
                     end: date | None, limit: int, offset: int) -> tuple[list[Any], int]: ...
    def records_for(self, context: TenantContext, kind: RecordKind, vehicle_ids: list[str]) -> list[Any]: ...
    def get_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> Any | None: ...
    def record_by_idem(self, context: TenantContext, kind: RecordKind, idem_key: str) -> Any | None: ...
    def add_record(self, kind: RecordKind, record: Any) -> None: ...
    def save_record(self, kind: RecordKind, record: Any, expected_version: int) -> bool: ...
    def delete_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> bool: ...
    def set_payment(self, context: TenantContext, kind: RecordKind, record_id: str, payment: PaymentRef | None) -> bool: ...
    def open_trips(self, context: TenantContext, vehicle_id: str | None) -> list[Trip]: ...

    def reminder_sent(self, reminder: ReminderSent) -> bool: ...
    def add_reminder(self, reminder: ReminderSent) -> bool: ...

    def get_preferences(self, context: TenantContext) -> Preferences | None: ...
    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool: ...

    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...
    def export(self, context: TenantContext) -> dict[str, Any]: ...


def _scope(item: Any, context: TenantContext) -> bool:
    return item.tenant_id == context.tenant_id and item.workspace_id == context.workspace_id


def vehicle_visible(vehicle: Vehicle, context: TenantContext) -> bool:
    return _scope(vehicle, context) and (vehicle.ownership == Ownership.BUSINESS or vehicle.owner_user_id == context.user_id)


def child_visible(item: Any, context: TenantContext) -> bool:
    return _scope(item, context) and (item.visibility == Ownership.BUSINESS or item.vehicle_owner_id == context.user_id)


def vehicle_matches(vehicle: Vehicle, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in (text or "").casefold() for text in (vehicle.registration, vehicle.nickname, vehicle.make, vehicle.model,
                                                              str(vehicle.year or "")))


def check_odometer(readings: list[OdometerReading], new: OdometerReading) -> tuple[OdometerReading | None, OdometerReading | None]:
    """The neighbours a non-correction reading breaks (before, after), or (None, None). Same rule as the database trigger."""
    if new.correction:
        return None, None
    key = (new.read_at, new.created_at, new.id)
    ordered = sorted(readings, key=lambda r: (r.read_at, r.created_at, r.id))
    before = next((r for r in reversed(ordered) if (r.read_at, r.created_at, r.id) < key), None)
    after = next((r for r in ordered if (r.read_at, r.created_at, r.id) > key), None)
    bad_before = before if before is not None and new.reading_km < before.reading_km else None
    bad_after = after if after is not None and not after.correction and new.reading_km > after.reading_km else None
    return bad_before, bad_after


class InMemoryVehicleStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        with getattr(self, "_lock", RLock()):
            self.vehicles: dict[str, Vehicle] = {}
            self.readings_by_id: dict[str, OdometerReading] = {}
            self.records: dict[str, dict[str, Any]] = {"fuel": {}, "trips": {}, "service": {}}
            self.reminders: dict[tuple[str, str, str, date, int], ReminderSent] = {}
            self.preferences: dict[tuple[str, str, str], Preferences] = {}

    # vehicles ----------------------------------------------------------------------------------

    def list_vehicles(self, context: TenantContext, *, query: str | None, archived: bool, limit: int, offset: int) -> tuple[list[Vehicle], int]:
        with self._lock:
            rows = [v for v in self.vehicles.values() if vehicle_visible(v, context) and (v.archived_at is not None) == archived
                    and vehicle_matches(v, query)]
        rows.sort(key=lambda v: (v.registration.upper(), v.id))
        return [v.model_copy(deep=True) for v in rows[offset:offset + limit]], len(rows)

    def get_vehicle(self, context: TenantContext, vehicle_id: str) -> Vehicle | None:
        found = self.vehicles.get(vehicle_id)
        return found.model_copy(deep=True) if found is not None and vehicle_visible(found, context) else None

    def add_vehicle(self, vehicle: Vehicle) -> None:
        with self._lock:
            self._unique_registration(vehicle)
            self.vehicles[vehicle.id] = vehicle.model_copy(deep=True)

    def _unique_registration(self, vehicle: Vehicle) -> None:
        if vehicle.archived_at is not None:
            return
        for other in self.vehicles.values():
            if (other.id != vehicle.id and _scope(other, vehicle) and other.archived_at is None and other.country == vehicle.country  # type: ignore[arg-type]
                    and other.registration.upper() == vehicle.registration.upper()):
                raise DuplicateError("registration")

    def save_vehicle(self, vehicle: Vehicle, expected_version: int) -> bool:
        with self._lock:
            current = self.vehicles.get(vehicle.id)
            if current is None or current.version != expected_version:
                return False
            if current.ownership != vehicle.ownership or current.owner_user_id != vehicle.owner_user_id:
                raise ValueError("a vehicle keeps its ownership and owner")
            self._unique_registration(vehicle)
            self.vehicles[vehicle.id] = vehicle.model_copy(deep=True, update={"version": expected_version + 1})
            return True

    def delete_vehicle(self, context: TenantContext, vehicle_id: str) -> bool:
        with self._lock:
            found = self.vehicles.get(vehicle_id)
            if found is None or not vehicle_visible(found, context):
                return False
            del self.vehicles[vehicle_id]
            for table in (self.readings_by_id, *self.records.values()):
                for key in [k for k, r in table.items() if r.vehicle_id == vehicle_id]:
                    del table[key]
            for key in [k for k in self.reminders if k[0] == vehicle_id]:
                del self.reminders[key]
            return True

    def set_purchase_payment(self, context: TenantContext, vehicle_id: str, payment: PaymentRef | None) -> bool:
        with self._lock:
            found = self.vehicles.get(vehicle_id)
            if found is None or not vehicle_visible(found, context):
                return False
            found.purchase_payment = payment.model_copy() if payment else None
            return True

    # odometer ----------------------------------------------------------------------------------

    def readings(self, context: TenantContext, vehicle_id: str) -> list[OdometerReading]:
        rows = [r for r in list(self.readings_by_id.values()) if r.vehicle_id == vehicle_id and child_visible(r, context)]
        return [r.model_copy() for r in sorted(rows, key=lambda r: (r.read_at, r.created_at, r.id))]

    def add_reading(self, reading: OdometerReading) -> None:
        with self._lock:
            existing = [r for r in self.readings_by_id.values() if r.vehicle_id == reading.vehicle_id]
            before, after = check_odometer(existing, reading)
            if before is not None or after is not None:
                raise OdometerRefused("odometer_decrease")
            self.readings_by_id[reading.id] = reading.model_copy()

    def delete_readings(self, context: TenantContext, vehicle_id: str, source_id: str) -> int:
        with self._lock:
            doomed = [k for k, r in self.readings_by_id.items() if r.vehicle_id == vehicle_id and r.source_id == source_id and child_visible(r, context)]
            for key in doomed:
                del self.readings_by_id[key]
            return len(doomed)

    # records -----------------------------------------------------------------------------------

    def _rows(self, context: TenantContext, kind: str) -> list[Any]:
        return [r for r in list(self.records[kind].values()) if child_visible(r, context)]

    def list_records(self, context: TenantContext, kind: RecordKind, vehicle_id: str | None, *, query: str | None, start: date | None,
                     end: date | None, limit: int, offset: int) -> tuple[list[Any], int]:
        needle = (query or "").casefold()

        def day(record: Any) -> date:
            moment = record_time(kind, record)
            return moment.date() if isinstance(moment, datetime) else moment

        rows = [r for r in self._rows(context, kind) if (vehicle_id is None or r.vehicle_id == vehicle_id) and (not needle or needle in record_text(kind, r))
                and (start is None or day(r) >= start) and (end is None or day(r) <= end)]
        rows.sort(key=lambda r: (str(record_time(kind, r)), r.created_at, r.id), reverse=True)
        return [r.model_copy(deep=True) for r in rows[offset:offset + limit]], len(rows)

    def records_for(self, context: TenantContext, kind: RecordKind, vehicle_ids: list[str]) -> list[Any]:
        wanted = set(vehicle_ids)
        rows = [r for r in self._rows(context, kind) if r.vehicle_id in wanted]
        return [r.model_copy(deep=True) for r in sorted(rows, key=lambda r: (str(record_time(kind, r)), r.created_at, r.id))][:MAX_RECORDS_READ]

    def get_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> Any | None:
        found = self.records[kind].get(record_id)
        return found.model_copy(deep=True) if found is not None and child_visible(found, context) else None

    def record_by_idem(self, context: TenantContext, kind: RecordKind, idem_key: str) -> Any | None:
        found = next((r for r in self._rows(context, kind) if r.owner_user_id == context.user_id and r.idem_key == idem_key), None)
        return found.model_copy(deep=True) if found is not None else None

    def add_record(self, kind: RecordKind, record: Any) -> None:
        with self._lock:
            for other in self.records[kind].values():
                if _scope(other, record) and other.owner_user_id == record.owner_user_id:  # type: ignore[arg-type]
                    if other.idem_key == record.idem_key:
                        raise DuplicateError("idem_key")
                    if kind == "trips" and record.status == "open" and other.status == "open" and other.vehicle_id == record.vehicle_id:
                        raise DuplicateError("open_trip")
            self.records[kind][record.id] = record.model_copy(deep=True)

    def save_record(self, kind: RecordKind, record: Any, expected_version: int) -> bool:
        with self._lock:
            current = self.records[kind].get(record.id)
            if current is None or current.version != expected_version:
                return False
            self.records[kind][record.id] = record.model_copy(deep=True, update={"version": expected_version + 1})
            return True

    def delete_record(self, context: TenantContext, kind: RecordKind, record_id: str) -> bool:
        with self._lock:
            found = self.records[kind].get(record_id)
            if found is None or not child_visible(found, context):
                return False
            del self.records[kind][record_id]
            return True

    def set_payment(self, context: TenantContext, kind: RecordKind, record_id: str, payment: PaymentRef | None) -> bool:
        with self._lock:
            found = self.records[kind].get(record_id)
            if found is None or not child_visible(found, context) or not hasattr(found, "payment"):
                return False
            found.payment = payment.model_copy() if payment else None
            return True

    def open_trips(self, context: TenantContext, vehicle_id: str | None) -> list[Trip]:
        return [r.model_copy() for r in self._rows(context, "trips")
                if r.status == "open" and r.owner_user_id == context.user_id and (vehicle_id is None or r.vehicle_id == vehicle_id)]

    # reminders and preferences -----------------------------------------------------------------

    @staticmethod
    def _reminder_key(reminder: ReminderSent) -> tuple[str, str, str, date, int]:
        return (reminder.vehicle_id, reminder.kind, reminder.source_id, reminder.due_on, reminder.step)

    def reminder_sent(self, reminder: ReminderSent) -> bool:
        return self._reminder_key(reminder) in self.reminders

    def add_reminder(self, reminder: ReminderSent) -> bool:
        with self._lock:
            key = self._reminder_key(reminder)
            if key in self.reminders:
                return False
            self.reminders[key] = reminder.model_copy()
            return True

    def get_preferences(self, context: TenantContext) -> Preferences | None:
        found = self.preferences.get((context.tenant_id, context.workspace_id, context.user_id))
        return found.model_copy() if found else None

    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool:
        with self._lock:
            key = (preferences.tenant_id, preferences.workspace_id, preferences.owner_user_id)
            current = self.preferences.get(key)
            if (current.version if current else 0) != expected_version:
                return False
            self.preferences[key] = preferences.model_copy(update={"version": expected_version + 1})
            return True

    # privacy -----------------------------------------------------------------------------------

    def _mine(self, context: TenantContext) -> dict[str, list[Any]]:
        """The person's own data: their personal vehicles (with every record), their records and reminders elsewhere, their preferences."""
        mine = [v for v in self.vehicles.values() if _scope(v, context) and v.owner_user_id == context.user_id]
        personal = {v.id for v in mine if v.ownership == Ownership.PERSONAL}
        out: dict[str, list[Any]] = {"vehicles": mine}
        for name, table in (("odometer", self.readings_by_id), ("fuel", self.records["fuel"]), ("trips", self.records["trips"]),
                            ("service", self.records["service"]), ("reminders", self.reminders)):
            out[name] = [r for r in table.values() if _scope(r, context) and (r.owner_user_id == context.user_id or r.vehicle_id in personal)]
        out["preferences"] = [p for p in self.preferences.values() if _scope(p, context) and p.owner_user_id == context.user_id]
        return out

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(len(rows) for rows in self._mine(context).values())

    def delete_everything(self, context: TenantContext) -> int:
        """Personal vehicles go with every record; on business vehicles the person's own records go and the vehicle stays."""
        with self._lock:
            mine = self._mine(context)
            removed = sum(len(rows) for name, rows in mine.items() if name != "vehicles")
            removed += sum(1 for v in mine["vehicles"] if v.ownership == Ownership.PERSONAL)
            ids = {id(r) for rows in mine.values() for r in rows}
            for table in (self.readings_by_id, *self.records.values(), self.reminders, self.preferences):
                for key in [k for k, r in table.items() if id(r) in ids]:
                    del table[key]
            for vehicle in mine["vehicles"]:
                if vehicle.ownership == Ownership.PERSONAL:
                    self.vehicles.pop(vehicle.id, None)
            return removed

    def export(self, context: TenantContext) -> dict[str, Any]:
        with self._lock:
            return export_records(self._mine(context))


def export_records(mine: dict[str, list[Any]]) -> dict[str, Any]:
    hidden = {"tenant_id", "workspace_id", "idem_key"}
    return {name: [r.model_dump(mode="json", exclude=hidden) for r in rows] for name, rows in mine.items()}


def decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


vehicle_memory_store = InMemoryVehicleStore()


def store_for_session(session: Any, context: TenantContext) -> VehicleStore:
    """The vehicle store on an existing unit of work (a finance link's session), or the in-memory store."""
    if session is None:
        return vehicle_memory_store
    from ..db.vehicles_repository import SqlVehicleStore

    return SqlVehicleStore(session, context)


@contextmanager
def open_vehicle_store(context: TenantContext) -> Iterator[VehicleStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.vehicles_repository import SqlVehicleStore

        return SqlVehicleStore(session, context)

    with open_scoped_store(context, vehicle_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = [
    "DuplicateError",
    "InMemoryVehicleStore",
    "MAX_RECORDS_READ",
    "MAX_VEHICLES",
    "OdometerRefused",
    "RECORD_MODELS",
    "RecordKind",
    "VehicleStore",
    "check_odometer",
    "child_visible",
    "decimal_or_none",
    "export_records",
    "open_vehicle_store",
    "record_text",
    "record_time",
    "store_for_session",
    "vehicle_memory_store",
    "vehicle_visible",
]
