"""HTTP routes under ``/api/v1/vehicles`` (docs/vehicles.md#api).

Who can do what (checked here, on the server, for every route; the database repeats the visibility rule):

* ``vehicle:read`` (owners and members): business vehicles of the workspace and their own personal ones.
* ``vehicle:write`` (owners and members): add vehicles, log fuel, trips, service and odometer readings.
  A business vehicle is changed by the person who added it or a workspace owner (``vehicle:manage``);
  a record by the person who logged it or a workspace owner.
* Linking a payment also needs ``finance:manage``: the payment is the caller's own Finance transaction.
* Viewers have no Vehicles. A personal vehicle of someone else answers 404, the same as one that does not exist.

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}`` plus figures where they help.
"""

from __future__ import annotations

import csv
import functools
import io
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import analytics as A
from . import service as S
from .models import Country, FuelType, OdometerReading, Ownership, PaymentMethod, ServiceKind, TripPurpose, Vehicle, VehicleType
from .store import MAX_VEHICLES, DuplicateError, RecordKind, VehicleStore, open_vehicle_store

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"])

DENIED = "Vehicles are for workspace owners and members. Ask a workspace owner to change your role from viewer to member."
FINANCE_DENIED = "Linking a payment needs Finance access in this workspace (owners and members)."
MAX_PAGE = 200
Money = Decimal


def refusals(route: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except S.VehicleError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})

    return wrapper


def _need(context: TenantContext, permission: Permission, message: str = DENIED) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise S.VehicleError(403, message, "forbidden") from None
        raise


def _read(context: TenantContext) -> None:
    _need(context, Permission.VEHICLE_READ)


def _write(context: TenantContext) -> None:
    _need(context, Permission.VEHICLE_WRITE)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    """Ids and codes only: never a registration, a VIN, a place or an amount."""
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=target, outcome="success", correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    ))


def _page(items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# -- views -------------------------------------------------------------------------------------------

HIDDEN = {"tenant_id", "workspace_id", "idem_key"}


def vehicle_view(context: TenantContext, vehicle: Vehicle, *, odometer: Decimal | None = None, due: list[dict[str, Any]] | None = None,
                 full_vin: bool = False) -> dict[str, Any]:
    data = vehicle.model_dump(mode="json", exclude=HIDDEN)
    editable = S.can_edit_vehicle(context, vehicle)
    data["vin"] = vehicle.vin if (full_vin and editable) else S.mask_vin(vehicle.vin)
    data["vin_masked"] = not (full_vin and editable) and vehicle.vin is not None
    data["can_edit"] = editable
    data["mine"] = vehicle.owner_user_id == context.user_id
    data["odometer_km"] = str(odometer) if odometer is not None else None
    data["due"] = due or []
    _redact(data, "purchase_payment", vehicle.purchase_payment, context)
    return data


def _redact(data: dict[str, Any], key: str, payment: Any, context: TenantContext) -> None:
    """Another member's payment shows its amount only, never their ledger or link id."""
    if data.get(key) is not None and payment is not None and payment.paid_by != context.user_id:
        data[key]["canonical_id"] = None
        data[key]["link_id"] = None


def record_view(context: TenantContext, record: Any) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude=HIDDEN)
    data["can_edit"] = S.can_edit_record(context, record)
    data["mine"] = record.owner_user_id == context.user_id
    _redact(data, "payment", getattr(record, "payment", None), context)
    return data


# -- vehicles ----------------------------------------------------------------------------------------


class VehicleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownership: Literal["personal", "business"] = "personal"
    registration: str = Field(min_length=1, max_length=20)
    vin: str | None = Field(default=None, max_length=20)
    nickname: str | None = Field(default=None, max_length=60)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    year: int | None = Field(default=None, ge=1950, le=2100)
    vehicle_type: VehicleType = "car"
    country: Country
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fuel_type: FuelType
    tank_litres: Decimal | None = Field(default=None, ge=1, le=2000, decimal_places=2)
    acquisition_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"), decimal_places=2)
    residual_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"), decimal_places=2)
    useful_life_months: int | None = Field(default=None, ge=1, le=600)
    insurance_expires_on: date | None = None
    registration_expires_on: date | None = None
    inspection_expires_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)


class VehiclePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    registration: str | None = Field(default=None, min_length=1, max_length=20)
    vin: str | None = Field(default=None, max_length=20)
    nickname: str | None = Field(default=None, max_length=60)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    year: int | None = Field(default=None, ge=1950, le=2100)
    vehicle_type: VehicleType | None = None
    country: Country | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fuel_type: FuelType | None = None
    tank_litres: Decimal | None = Field(default=None, ge=1, le=2000, decimal_places=2)
    acquisition_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"), decimal_places=2)
    residual_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"), decimal_places=2)
    useful_life_months: int | None = Field(default=None, ge=1, le=600)
    insurance_expires_on: date | None = None
    registration_expires_on: date | None = None
    inspection_expires_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


def _due(store: VehicleStore, context: TenantContext, vehicles: list[Vehicle], window: int,
         readings: dict[str, list[OdometerReading]] | None = None) -> dict[str, tuple[Decimal | None, list[dict[str, Any]]]]:
    today = utc_now().date()
    service = store.records_for(context, "service", [v.id for v in vehicles])
    out = {}
    for vehicle in vehicles:
        odometer = A.current_odometer(readings[vehicle.id] if readings is not None else store.readings(context, vehicle.id))
        out[vehicle.id] = (odometer, S.due_items(vehicle, [r for r in service if r.vehicle_id == vehicle.id], odometer, today=today, window_days=window))
    return out


@router.get("", response_model=None)
@refusals
def list_vehicles(q: str | None = Query(default=None, max_length=80), archived: bool = False, limit: int = Query(default=50, ge=1, le=MAX_PAGE),
                  offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_vehicle_store(context) as store:
        items, total = store.list_vehicles(context, query=(q or "").strip() or None, archived=archived, limit=limit, offset=offset)
        window = S.preferences(store, context).reminder_days
        extra = _due(store, context, items, window)
    return _page([vehicle_view(context, v, odometer=extra[v.id][0], due=extra[v.id][1]) for v in items], total, limit, offset)


@router.post("", status_code=201, response_model=None)
@refusals
def create_vehicle(body: VehicleInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    now = utc_now()
    vehicle = S.new_vehicle(context, body.model_dump(exclude={"odometer_km"}), now)
    with open_vehicle_store(context) as store:
        _, total = store.list_vehicles(context, query=None, archived=False, limit=1, offset=0)
        if total >= MAX_VEHICLES:
            raise S.VehicleError(409, f"A workspace keeps up to {MAX_VEHICLES} active vehicles. Archive one first.", "too_many")
        try:
            store.add_vehicle(vehicle)
        except DuplicateError:
            raise S.VehicleError(409, "A vehicle with this registration is already in this workspace.", "duplicate_registration") from None
        odometer = None
        if body.odometer_km is not None:
            odometer = S.add_reading(store, context, vehicle, km=body.odometer_km, read_at=now, source="manual", source_id=None).reading_km
    _audit(repository, context, "vehicle.create", vehicle.id, {"ownership": vehicle.ownership.value})
    return JSONResponse(status_code=201, content=vehicle_view(context, vehicle, odometer=odometer, full_vin=True))


@router.get("/overview", response_model=None)
@refusals
def overview(start: date | None = None, end: date | None = None, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    end = end or utc_now().date()
    start = start or end.replace(day=1) - timedelta(days=330)
    if start > end or (end - start).days > 1100:
        raise S.VehicleError(422, "Choose a period of up to three years, start before end.", "invalid_period")
    with open_vehicle_store(context) as store:
        vehicles, _ = store.list_vehicles(context, query=None, archived=False, limit=MAX_VEHICLES, offset=0)
        ids = [v.id for v in vehicles]
        fuel = store.records_for(context, "fuel", ids)
        service = store.records_for(context, "service", ids)
        window = S.preferences(store, context).reminder_days
        readings = {v.id: store.readings(context, v.id) for v in vehicles}
        due = _due(store, context, vehicles, window, readings)
    result = A.overview(vehicles, fuel, service, start=start, end=end)
    result.update(A.usage(vehicles, readings, fuel, start=start, end=end))
    result["vehicles"] = len(vehicles)
    result["due"] = sorted((item for _, items in due.values() for item in items),
                           key=lambda i: (i["days_left"] if i["days_left"] is not None else 10_000, i["registration"]))
    result["unlinked_records"] = sum(1 for r in [*fuel, *service] if r.payment is None or r.payment.status == "void")
    return result


@router.get("/reminders", response_model=None)
@refusals
def reminders(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_vehicle_store(context) as store:
        vehicles, _ = store.list_vehicles(context, query=None, archived=False, limit=MAX_VEHICLES, offset=0)
        window = S.preferences(store, context).reminder_days
        due = _due(store, context, vehicles, window)
    items = sorted((item for _, rows in due.values() for item in rows), key=lambda i: (i["days_left"] if i["days_left"] is not None else 10_000))
    return {"items": items, "window_days": window}


class PreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    trip_places: bool = False
    default_vehicle_id: str | None = Field(default=None, max_length=80)
    reminder_days: int = Field(default=30, ge=1, le=120)
    reminders: bool = True


@router.get("/preferences", response_model=None)
@refusals
def get_preferences(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_vehicle_store(context) as store:
        return S.preferences(store, context).model_dump(mode="json", exclude={"tenant_id", "workspace_id"})


@router.put("/preferences", response_model=None)
@refusals
def put_preferences(body: PreferencesInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_vehicle_store(context) as store:
        if body.default_vehicle_id is not None:
            S.get_vehicle(store, context, body.default_vehicle_id)
        current = S.preferences(store, context)
        updated = current.model_copy(update={**body.model_dump(exclude={"version"}), "updated_at": utc_now()})
        if not store.save_preferences(updated, body.version):
            raise S.VehicleError(409, "Your vehicle settings changed in another tab. Reload and try again.", "stale")
        saved = S.preferences(store, context)
    _audit(repository, context, "vehicle.preferences", context.user_id, {"trip_places": body.trip_places})
    return saved.model_dump(mode="json", exclude={"tenant_id", "workspace_id"})


@router.get("/payments", response_model=None)
@refusals
def payments(q: str | None = Query(default=None, max_length=80), around: date | None = None,
             amount: Decimal | None = Query(default=None, gt=0, lt=Decimal("1000000000")), limit: int = Query(default=20, ge=1, le=50),
             context: TenantContext = Depends(tenant_context)) -> Any:
    """The caller's own Finance payments to pick from (never another person's ledger)."""
    _read(context)
    _need(context, Permission.FINANCE_READ, FINANCE_DENIED)
    return {"items": S.payment_candidates(context, query=(q or "").strip() or None, around=around, amount=amount, limit=limit)}


@router.get("/{vehicle_id}", response_model=None)
@refusals
def get_vehicle(vehicle_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        extra = _due(store, context, [vehicle], S.preferences(store, context).reminder_days)
    return vehicle_view(context, vehicle, odometer=extra[vehicle.id][0], due=extra[vehicle.id][1], full_vin=True)


@router.patch("/{vehicle_id}", response_model=None)
@refusals
def update_vehicle(vehicle_id: str, body: VehiclePatch, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        S.require_vehicle_edit(context, vehicle)
        _no_nulls(changes, ("registration", "country", "fuel_type", "vehicle_type", "currency"))
        updated = S.apply_vehicle_changes(vehicle, changes, utc_now())
        try:
            saved = store.save_vehicle(updated, body.version)
        except DuplicateError:
            raise S.VehicleError(409, "A vehicle with this registration is already in this workspace.", "duplicate_registration") from None
        if not saved:
            raise S.VehicleError(409, "This vehicle changed since you opened it. Reload and try again.", "stale")
        vehicle = S.get_vehicle(store, context, vehicle_id)
    _audit(repository, context, "vehicle.update", vehicle_id, {"fields": sorted(changes)})
    return vehicle_view(context, vehicle, full_vin=True)


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    archived: bool = True


@router.post("/{vehicle_id}/archive", response_model=None)
@refusals
def archive_vehicle(vehicle_id: str, body: ArchiveInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        S.require_vehicle_edit(context, vehicle)
        vehicle.archived_at = utc_now() if body.archived else None
        vehicle.updated_at = utc_now()
        try:
            saved = store.save_vehicle(vehicle, body.version)
        except DuplicateError:
            raise S.VehicleError(409, "Another active vehicle has this registration. Archive or rename it first.", "duplicate_registration") from None
        if not saved:
            raise S.VehicleError(409, "This vehicle changed since you opened it. Reload and try again.", "stale")
        vehicle = S.get_vehicle(store, context, vehicle_id)
    _audit(repository, context, "vehicle.archive" if body.archived else "vehicle.restore", vehicle_id)
    return vehicle_view(context, vehicle)


@router.delete("/{vehicle_id}", status_code=204, response_class=Response, response_model=None)
@refusals
def delete_vehicle(vehicle_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        S.require_vehicle_edit(context, vehicle)
        if S.has_payments(store, context, vehicle):
            raise S.VehicleError(409, "This vehicle has records linked to payments. Remove those links, or archive the vehicle to keep its history.",
                                 "has_payments")
        store.delete_vehicle(context, vehicle_id)
    _audit(repository, context, "vehicle.delete", vehicle_id)
    return Response(status_code=204)


@router.get("/{vehicle_id}/report", response_model=None)
@refusals
def report(vehicle_id: str, start: date | None = None, end: date | None = None, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    if start is not None and end is not None and start > end:
        raise S.VehicleError(422, "The start date is after the end date.", "invalid_period")
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        readings = store.readings(context, vehicle_id)
        fuel = store.records_for(context, "fuel", [vehicle_id])
        service = store.records_for(context, "service", [vehicle_id])
    return A.vehicle_report(vehicle, readings, fuel, service, start=start, end=end, today=utc_now().date())


# -- odometer ----------------------------------------------------------------------------------------


class ReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_km: Decimal = Field(ge=0, lt=10_000_000, decimal_places=1)
    read_at: datetime | None = None
    correction: bool = False
    reason: str | None = Field(default=None, max_length=300)


@router.get("/{vehicle_id}/odometer", response_model=None)
@refusals
def list_readings(vehicle_id: str, limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                  context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_vehicle_store(context) as store:
        S.get_vehicle(store, context, vehicle_id)
        rows = list(reversed(store.readings(context, vehicle_id)))
    return _page([r.model_dump(mode="json", exclude=HIDDEN) for r in rows[offset:offset + limit]], len(rows), limit, offset)


@router.post("/{vehicle_id}/odometer", status_code=201, response_model=None)
@refusals
def add_reading(vehicle_id: str, body: ReadingInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        reading = S.add_reading(store, context, vehicle, km=body.reading_km, read_at=S.not_future(body.read_at or utc_now(), "Reading time"),
                                source="manual", source_id=None, correction=body.correction, reason=body.reason)
    _audit(repository, context, "vehicle.odometer.correction" if body.correction else "vehicle.odometer.add", reading.id, {"vehicle_id": vehicle_id})
    return JSONResponse(status_code=201, content=reading.model_dump(mode="json", exclude=HIDDEN))


# -- records: fuel, trips and service ----------------------------------------------------------------


class FuelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filled_at: datetime
    station: str | None = Field(default=None, max_length=120)
    fuel_type: FuelType | None = None
    litres: Decimal = Field(gt=0, lt=10_000, decimal_places=3)
    price_per_litre: Decimal | None = Field(default=None, gt=0, lt=10_000, decimal_places=3)
    total: Decimal | None = Field(default=None, gt=0, lt=Decimal("10000000000"), decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)
    full_tank: bool = True
    payment_method: PaymentMethod | None = None
    document_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    notes: str | None = Field(default=None, max_length=500)


class TripInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    ended_at: datetime | None = None
    start_odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)
    end_odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)
    purpose: TripPurpose = "business"
    purpose_note: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    origin: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)


class ServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ServiceKind
    service_on: date
    odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)
    workshop: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=60)
    next_due_on: date | None = None
    next_due_km: Decimal | None = Field(default=None, gt=0, lt=10_000_000, decimal_places=1)
    warranty_until: date | None = None
    trip_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    receipt_total: Decimal | None = Field(default=None, gt=0, lt=Decimal("10000000000"), decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_method: PaymentMethod | None = None
    document_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


INPUTS: dict[str, type[BaseModel]] = {"fuel": FuelInput, "trips": TripInput, "service": ServiceInput}
LABEL = {"fuel": "Fuel entry", "trips": "Trip", "service": "Service record"}


def _build(store: VehicleStore, context: TenantContext, kind: RecordKind, vehicle: Vehicle, data: dict[str, Any], existing: Any | None,
           now: datetime, idem_key: str) -> Any:
    """A record from its input (new, or an edit of ``existing``), validated."""
    base: dict[str, Any] = existing.model_dump() if existing is not None else {
        "id": new_id({"fuel": "vfuel", "trips": "vtrip", "service": "vsvc"}[kind]), "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id, "owner_user_id": context.user_id, "vehicle_id": vehicle.id, "visibility": vehicle.ownership,
        "vehicle_owner_id": vehicle.owner_user_id, "idem_key": idem_key, "created_at": now}
    base.update(data)
    base["updated_at"] = now
    if kind == "fuel":
        base["filled_at"] = S.not_future(base["filled_at"], "Fill time")
        base["fuel_type"] = base.get("fuel_type") or vehicle.fuel_type
        base["currency"] = (base.get("currency") or vehicle.currency).upper()
        for key, limit in (("station", 120), ("notes", 500)):
            base[key] = S.clean(base.get(key), limit)
        # The pump shows total and litres; the price per litre follows them unless it was the figure given or changed.
        price_given = data.get("price_per_litre") is not None
        total_given = data.get("total") is not None
        if base.get("total") is not None and not price_given:
            base["price_per_litre"] = (Decimal(base["total"]) / Decimal(base["litres"])).quantize(Decimal("0.001"))
        elif base.get("price_per_litre") is not None and (price_given or "litres" in data) and not total_given:
            base["total"] = (Decimal(base["price_per_litre"]) * Decimal(base["litres"])).quantize(Decimal("0.01"))
    elif kind == "trips":
        base["started_at"] = S.not_future(base["started_at"], "Start")
        if base.get("ended_at") is not None:
            base["ended_at"] = S.not_future(base["ended_at"], "End")
            if base["ended_at"] < base["started_at"]:
                raise S.VehicleError(422, "The trip ends before it starts.", "invalid_time")
        start_km, end_km = base.get("start_odometer_km"), base.get("end_odometer_km")
        if start_km is not None and end_km is not None and end_km < start_km:
            raise S.VehicleError(422, "The odometer at the end is lower than at the start.", "invalid_odometer")
        base["status"] = "closed" if base.get("ended_at") is not None else "open"
        places = S.preferences(store, context).trip_places
        for key in ("origin", "destination"):
            base[key] = S.clean(base.get(key), 120)
            unchanged = existing is not None and base[key] == S.clean(getattr(existing, key), 120)
            if key in data and base[key] and not places and not unchanged:  # stored places stay and may be resent; new ones need the switch
                raise S.VehicleError(422, "Trip places are off. Turn on \"Store trip places\" in Vehicles settings to keep them.", "places_off")
        for key, limit in (("purpose_note", 200), ("notes", 500)):
            base[key] = S.clean(base.get(key), limit)
    else:
        if base["service_on"] > utc_now().date():
            raise S.VehicleError(422, "Service date: the date is in the future.", "in_future")
        base["currency"] = (base.get("currency") or vehicle.currency).upper()
        for key, limit in (("workshop", 120), ("description", 500), ("reference", 60)):
            base[key] = S.clean(base.get(key), limit)
        if base.get("trip_id"):
            trip = store.get_record(context, "trips", base["trip_id"])
            if trip is None or trip.vehicle_id != vehicle.id:
                raise S.VehicleError(422, "That trip is not on this vehicle.", "invalid_trip")
    from .store import RECORD_MODELS

    record = RECORD_MODELS[kind].model_validate(base)
    if kind == "trips":
        record = S.trip_distance(store, context, record)  # type: ignore[arg-type]
    return record


def _planned_readings(kind: RecordKind, record: Any) -> list[tuple[Decimal, datetime, str]]:
    """The odometer readings a record carries: (km, time, source)."""
    if kind == "fuel" and record.odometer_km is not None:
        return [(record.odometer_km, record.filled_at, "fuel")]
    if kind == "service" and record.odometer_km is not None:
        return [(record.odometer_km, S.at_noon(record.service_on), "service")]
    out = []
    if kind == "trips":
        if record.start_odometer_km is not None:
            out.append((record.start_odometer_km, record.started_at, "trip_start"))
        if record.end_odometer_km is not None and record.ended_at is not None:
            out.append((record.end_odometer_km, record.ended_at, "trip_end"))
    return out


def _check_readings(store: VehicleStore, context: TenantContext, kind: RecordKind, vehicle: Vehicle, record: Any) -> None:
    """Refuse before anything is written: the record's readings must fit the vehicle's other readings."""
    from .models import OdometerReading
    from .store import check_odometer

    others = [r for r in store.readings(context, vehicle.id) if r.source_id != record.id]
    now = utc_now()
    for km, moment, source in _planned_readings(kind, record):
        probe = OdometerReading(id="~probe", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                vehicle_id=vehicle.id, visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, reading_km=km,
                                read_at=moment, source=source, created_at=now)
        before, after = check_odometer(others, probe)
        if before is not None or after is not None:
            other = before or after
            raise S.VehicleError(409, (f"The odometer cannot go down: {other.reading_km} km was recorded on {other.read_at.date().isoformat()}. "  # type: ignore[union-attr]
                                       "If the odometer was replaced or a reading was wrong, add a correction with a reason."), "odometer_decrease",
                                 {"reading_km": str(other.reading_km), "read_at": other.read_at.isoformat()})  # type: ignore[union-attr]


def _readings_for(store: VehicleStore, context: TenantContext, kind: RecordKind, vehicle: Vehicle, record: Any) -> None:
    """Write the record's odometer readings (the old ones of an edited record go first)."""
    store.delete_readings(context, vehicle.id, record.id)
    for km, moment, source in _planned_readings(kind, record):
        S.add_reading(store, context, vehicle, km=km, read_at=moment, source=source, source_id=record.id)


def create_record(context: TenantContext, kind: RecordKind, vehicle_id: str, data: dict[str, Any], idempotency_key: str | None) -> tuple[Any, bool]:
    now = utc_now()
    key = S.record_key(context, kind, vehicle_id, idempotency_key) if idempotency_key else S.record_key(context, kind, new_id("k"))
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        if vehicle.archived_at is not None:
            raise S.VehicleError(409, "This vehicle is archived. Restore it to log new records.", "archived")
        replay = store.record_by_idem(context, kind, key)
        if replay is not None:
            return replay, True
        record = _build(store, context, kind, vehicle, data, None, now, key)
        _check_readings(store, context, kind, vehicle, record)
        try:
            store.add_record(kind, record)
        except DuplicateError as exc:
            if str(exc) == "open_trip":
                raise S.VehicleError(409, "You already have a trip in progress on this vehicle. End it first.", "trip_open") from None
            raise S.VehicleError(409, "This record was already saved.", "duplicate") from None
        _readings_for(store, context, kind, vehicle, record)
    return record, False


def _body(model: type[BaseModel]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Routes made in a loop: give FastAPI the body class itself (annotations here are strings)."""

    def apply(route: Callable[..., Any]) -> Callable[..., Any]:
        route.__annotations__["body"] = model
        return route

    return apply


def _patch_model(model: type[BaseModel]) -> type[BaseModel]:
    """The same fields as ``model``, every one optional, plus the ``version`` being edited."""
    fields: dict[str, Any] = {"version": (int, Field(ge=1))}
    for name, info in model.model_fields.items():
        fields[name] = (info.annotation | None, FieldInfo.merge_field_infos(info, default=None))  # type: ignore[operator]
    return create_model(f"{model.__name__}Patch", __config__=ConfigDict(extra="forbid"), **fields)


REQUIRED = {"fuel": ("filled_at", "litres", "fuel_type", "currency", "full_tank"), "trips": ("started_at", "purpose"),
            "service": ("kind", "service_on", "currency")}


def _no_nulls(changes: dict[str, Any], required: tuple[str, ...]) -> None:
    empty = sorted(key for key in required if key in changes and changes[key] is None)
    if empty:
        raise S.VehicleError(422, f"These fields cannot be empty: {', '.join(empty)}.", "required_field", {"fields": empty})


def _record_routes(kind: RecordKind) -> None:
    model = INPUTS[kind]
    label = LABEL[kind]

    @router.get(f"/{{vehicle_id}}/{kind}", name=f"list_{kind}", response_model=None)
    @refusals
    def list_records(vehicle_id: str, q: str | None = Query(default=None, max_length=80), start: date | None = None, end: date | None = None,
                     limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                     context: TenantContext = Depends(tenant_context)) -> Any:
        _read(context)
        with open_vehicle_store(context) as store:
            S.get_vehicle(store, context, vehicle_id)
            items, total = store.list_records(context, kind, vehicle_id, query=(q or "").strip() or None, start=start, end=end, limit=limit, offset=offset)
        return _page([record_view(context, r) for r in items], total, limit, offset)

    @router.post(f"/{{vehicle_id}}/{kind}", status_code=201, name=f"create_{kind}", response_model=None)
    @refusals
    @_body(model)
    def create(vehicle_id: str, body: model, context: TenantContext = Depends(tenant_context),  # type: ignore[valid-type]
               repository: AnumRepository = Depends(repository_context),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
        _write(context)
        record, replayed = create_record(context, kind, vehicle_id, body.model_dump(exclude_unset=False), idempotency_key)  # type: ignore[attr-defined]
        if not replayed:
            _audit(repository, context, f"vehicle.{kind}.create", record.id, {"vehicle_id": vehicle_id})
        return JSONResponse(status_code=200 if replayed else 201, content={**record_view(context, record), "replayed": replayed})

    patch_model = _patch_model(model)

    @router.patch(f"/{kind}/{{record_id}}", name=f"update_{kind}", response_model=None)
    @refusals
    @_body(patch_model)
    def update(record_id: str, body: patch_model, context: TenantContext = Depends(tenant_context),  # type: ignore[valid-type]
               repository: AnumRepository = Depends(repository_context)) -> Any:
        _write(context)
        changes = body.model_dump(exclude_unset=True, exclude={"version"})  # type: ignore[attr-defined]
        _no_nulls(changes, REQUIRED[kind])
        with open_vehicle_store(context) as store:
            record = store.get_record(context, kind, record_id)
            if record is None:
                raise S.not_found(label)
            S.require_record_edit(context, record)
            vehicle = S.get_vehicle(store, context, record.vehicle_id)
            updated = _build(store, context, kind, vehicle, changes, record, utc_now(), record.idem_key)
            _check_readings(store, context, kind, vehicle, updated)
            if not store.save_record(kind, updated, body.version):  # type: ignore[attr-defined]
                raise S.VehicleError(409, f"This {label.lower()} changed since you opened it. Reload and try again.", "stale")
            _readings_for(store, context, kind, vehicle, updated)
            saved = store.get_record(context, kind, record_id)
        _audit(repository, context, f"vehicle.{kind}.update", record_id, {"fields": sorted(changes)})
        return record_view(context, saved)

    @router.delete(f"/{kind}/{{record_id}}", status_code=204, name=f"delete_{kind}", response_class=Response, response_model=None)
    @refusals
    def remove(record_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
        _write(context)
        with open_vehicle_store(context) as store:
            record = store.get_record(context, kind, record_id)
            if record is None:
                raise S.not_found(label)
            S.require_record_edit(context, record)
        payment = getattr(record, "payment", None)
        if payment is not None and payment.status != "void":
            if record.owner_user_id != context.user_id:
                raise S.VehicleError(409, "Another member linked their payment to this record. They remove the link first.", "linked_by_other")
            S.unlink_payment(context, "fuel_entry" if kind == "fuel" else "service_record", record_id)  # the payment stays in Finance
        with open_vehicle_store(context) as store:
            store.delete_readings(context, record.vehicle_id, record_id)
            store.delete_record(context, kind, record_id)
        _audit(repository, context, f"vehicle.{kind}.delete", record_id, {"vehicle_id": record.vehicle_id})
        return Response(status_code=204)


for _kind in ("fuel", "trips", "service"):
    _record_routes(_kind)  # type: ignore[arg-type]


# -- payments ----------------------------------------------------------------------------------------

CostKind = Literal["fuel_entry", "service_record", "purchase"]


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1000000000"), decimal_places=4)
    currency: str | None = Field(default=None, min_length=3, max_length=3, description="Needed when the payment has more than one currency")


class CashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    amount: Decimal = Field(gt=0, lt=Decimal("1000000000"), decimal_places=2)
    posted_on: date


def _finance_manage(context: TenantContext) -> None:
    _write(context)
    if not has_permission(context, Permission.FINANCE_MANAGE):
        raise S.VehicleError(403, FINANCE_DENIED, "finance_forbidden")


@router.post("/costs/{kind}/{record_id}/payment", response_model=None)
@refusals
def link_payment(kind: CostKind, record_id: str, body: LinkInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    payment, replayed = S.link_payment(context, kind, record_id, canonical_id=body.canonical_id, amount=body.amount, currency=body.currency)
    if not replayed:
        _audit(repository, context, "vehicle.payment.link", record_id, {"kind": kind, "link_id": payment.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"payment": payment.model_dump(mode="json"), "replayed": replayed})


@router.post("/costs/{kind}/{record_id}/cash", response_model=None)
@refusals
def record_cash(kind: CostKind, record_id: str, body: CashInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _finance_manage(context)
    payment, replayed = S.record_cash(context, kind, record_id, account_id=body.account_id, amount=body.amount, posted_on=body.posted_on,
                                      idempotency_key=idempotency_key)
    if not replayed:
        _audit(repository, context, "vehicle.payment.cash", record_id, {"kind": kind, "link_id": payment.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"payment": payment.model_dump(mode="json"), "replayed": replayed})


@router.delete("/costs/{kind}/{record_id}/payment", status_code=204, response_class=Response, response_model=None)
@refusals
def unlink_payment(kind: CostKind, record_id: str, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    S.unlink_payment(context, kind, record_id)
    _audit(repository, context, "vehicle.payment.unlink", record_id, {"kind": kind})
    return Response(status_code=204)


# -- export --------------------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/{vehicle_id}/export.csv", response_model=None)
@refusals
def export_csv(vehicle_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    """One vehicle's log (fuel, service, trips, odometer), newest first. Paid amounts only where a payment is linked."""
    _read(context)
    with open_vehicle_store(context) as store:
        vehicle = S.get_vehicle(store, context, vehicle_id)
        fuel = store.records_for(context, "fuel", [vehicle_id])
        service = store.records_for(context, "service", [vehicle_id])
        trips = store.records_for(context, "trips", [vehicle_id])
        readings = store.readings(context, vehicle_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["type", "date", "detail", "litres", "odometer_km", "distance_km", "paid_amount", "paid_currency", "payment_status", "notes"])
    rows: list[tuple[str, list[Any]]] = []
    for f in fuel:
        p = f.payment
        rows.append((f.filled_at.isoformat(), ["fuel", f.filled_at.isoformat(), f.station, f.litres, f.odometer_km, None,
                                               p.amount if p else None, p.currency if p else None, p.status if p else "not_linked", f.notes]))
    for s in service:
        p = s.payment
        rows.append((s.service_on.isoformat(), [f"service:{s.kind}", s.service_on.isoformat(), s.workshop or s.description, None, s.odometer_km, None,
                                                p.amount if p else None, p.currency if p else None, p.status if p else "not_linked", s.reference]))
    for t in trips:
        rows.append((t.started_at.isoformat(), [f"trip:{t.purpose}", t.started_at.isoformat(), t.purpose_note, None, t.end_odometer_km, t.distance_km,
                                                None, None, None, t.notes]))
    for r in readings:
        rows.append((r.read_at.isoformat(), ["correction" if r.correction else "odometer", r.read_at.isoformat(), r.reason, None, r.reading_km,
                                             None, None, None, None, r.source]))
    for _, row in sorted(rows, key=lambda item: item[0], reverse=True):
        writer.writerow([_cell(v) for v in row])
    name = "".join(ch for ch in vehicle.registration if ch.isascii() and ch.isalnum()) or "vehicle"
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="anum-vehicle-{name}.csv"', "Cache-Control": "no-store"})


__all__ = ["router", "vehicle_view", "record_view", "Ownership"]
