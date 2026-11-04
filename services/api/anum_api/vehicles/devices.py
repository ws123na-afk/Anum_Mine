"""iPhone driving sessions (docs/vehicles.md#iphone-driving-sessions).

A Shortcut automation on CarPlay or a car's Bluetooth **connecting** starts a driving session; the
automation on **disconnecting** ends it and asks the person to type the odometer (and, if they want,
the litres and pump total of a fill, or a parking fee). The phone never reads the car's odometer:
the value is what the person typed, and a session without one closes without a distance.

The request carries the shared iPhone device token (``Authorization: Bearer <token>``, scope
``vehicle``, docs/iphone-integrations.md). :func:`register_device_authority` is the seam
``finance.devices.plug_module_authorities`` connects; without a device token the ordinary sign-in
applies. The person must still hold ``vehicle:write``. Nothing here posts money: a fill or a parking
fee is a record waiting for its payment to be linked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Permission
from ..dependencies import has_permission, tenant_context
from ..schemas import TenantContext, new_id, utc_now
from . import service as S
from .models import Trip, Vehicle
from .store import DuplicateError, open_vehicle_store

SCOPE = "vehicle"
INTEGRATION = "vehicle.driving"
INGEST_PATH = "/api/v1/vehicles/device/sessions"


@dataclass(frozen=True)
class DevicePrincipal:
    context: TenantContext
    device_id: str | None
    via: str  # "device_token" or "session"


class DeviceAuthority(Protocol):
    def authorize(self, authorization: str | None, scope: str) -> Any | None: ...


_authority: DeviceAuthority | None = None


def register_device_authority(authority: DeviceAuthority | None) -> None:
    global _authority
    _authority = authority


async def device_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default="member"),
) -> DevicePrincipal:
    from ..finance.devices import require_https

    authority = _authority
    if authority is not None:
        require_https(request)
        # Token checks read the database: in a worker thread, never on the event loop.
        principal = await asyncio.to_thread(authority.authorize, authorization, SCOPE)
        if principal is not None:
            return DevicePrincipal(context=principal.context, device_id=principal.device_id, via="device_token")
    context = await tenant_context(request, authorization, x_tenant_id, x_workspace_id, x_user_id, x_user_roles)
    return DevicePrincipal(context=context, device_id=None, via="session")


class SessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "end", "test"]
    vehicle: str | None = Field(default=None, max_length=80, description="The vehicle's registration or id; the default vehicle when empty")
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=120)
    odometer_km: Decimal | None = Field(default=None, ge=0, lt=10_000_000, decimal_places=1)
    purpose: Literal["business", "personal", "commute"] | None = None
    fuel_litres: Decimal | None = Field(default=None, gt=0, lt=10_000, decimal_places=3)
    fuel_total: Decimal | None = Field(default=None, gt=0, lt=Decimal("10000000000"), decimal_places=2)
    fuel_full: bool = True
    parking_total: Decimal | None = Field(default=None, gt=0, lt=Decimal("10000000000"), decimal_places=2)


router = APIRouter(tags=["vehicles"])


def _compact(text: str) -> str:
    return "".join(text.split()).upper()


def resolve_vehicle(store: Any, context: TenantContext, wanted: str | None) -> Vehicle:
    vehicles, _ = store.list_vehicles(context, query=None, archived=False, limit=500, offset=0)
    if wanted:
        key = _compact(wanted)
        found = next((v for v in vehicles if v.id == wanted or _compact(v.registration) == key), None)
        if found is None:
            raise S.VehicleError(404, "No active vehicle with that registration in this workspace.", "vehicle_not_found")
        return found
    default = S.preferences(store, context).default_vehicle_id
    found = next((v for v in vehicles if v.id == default), None)
    if found is not None:
        return found
    if len(vehicles) == 1:
        return vehicles[0]
    raise S.VehicleError(422, "Say which vehicle: add its registration to the Shortcut, or choose a default vehicle in Vehicles settings.",
                         "vehicle_required")


def _trip_view(trip: Trip) -> dict[str, Any]:
    return {"id": trip.id, "vehicle_id": trip.vehicle_id, "status": trip.status, "started_at": trip.started_at.isoformat(),
            "ended_at": trip.ended_at.isoformat() if trip.ended_at else None,
            "distance_km": str(trip.distance_km) if trip.distance_km is not None else None, "distance_basis": trip.distance_basis}


def handle(context: TenantContext, device_id: str | None, event: SessionEvent) -> dict[str, Any]:
    """One driving-session event. Starting twice returns the open session; ending a session twice returns the first end."""
    now = utc_now()
    moment = S.not_future(event.occurred_at or now, "Time")
    seed = event.idempotency_key or f"{event.action}:{moment.isoformat()}"
    with open_vehicle_store(context) as store:
        vehicle = resolve_vehicle(store, context, event.vehicle)
        if event.action == "test":
            return {"status": "test_ok", "vehicle": vehicle.registration, "message": f"ANUM is connected. Trips will be logged on {vehicle.registration}."}
        open_trips = store.open_trips(context, vehicle.id)
        if event.action == "start":
            if open_trips:
                return {"status": "already_started", "trip": _trip_view(open_trips[0]), "message": "A trip is already in progress."}
            key = S.record_key(context, "device-start", vehicle.id, seed)
            replay = store.record_by_idem(context, "trips", key)
            if replay is not None:
                return {"status": "replayed", "trip": _trip_view(replay), "message": "Trip started."}
            trip = Trip(id=new_id("vtrip"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                        vehicle_id=vehicle.id, visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, status="open",
                        started_at=moment, purpose=event.purpose or "business", source="device", device_id=device_id, idem_key=key,
                        created_at=now, updated_at=now)
            try:
                store.add_record("trips", trip)
            except DuplicateError:
                raise S.VehicleError(409, "A trip is already in progress.", "trip_open") from None
            return {"status": "started", "trip": _trip_view(trip), "message": f"Trip started on {vehicle.registration}."}
        # end
        end_key = S.record_key(context, "device-end", vehicle.id, seed)
        parking_key = S.record_key(context, "device-parking", vehicle.id, seed)
        done = store.record_by_idem(context, "fuel", end_key) or store.record_by_idem(context, "service", parking_key)
        if done is None:
            recent, _ = store.list_records(context, "trips", vehicle.id, query=None, start=None, end=None, limit=5, offset=0)
            done = next((t for t in recent if t.status == "closed" and t.source == "device" and t.ended_at == moment
                         and t.owner_user_id == context.user_id), None)
        if done is not None:
            return {"status": "replayed", "message": "Trip already saved."}
        trip = open_trips[0] if open_trips else None
        if trip is None:
            if event.odometer_km is not None:
                S.add_reading(store, context, vehicle, km=event.odometer_km, read_at=moment, source="device", source_id=None)
            status, view = "reading_only", None
            message = "No trip was in progress, so ANUM saved the odometer reading only." if event.odometer_km is not None else "No trip was in progress."
        else:
            trip.ended_at, trip.status, trip.updated_at = max(moment, trip.started_at), "closed", now
            trip.end_odometer_km = event.odometer_km
            if event.purpose:
                trip.purpose = event.purpose
            trip = S.trip_distance(store, context, trip)
            if not store.save_record("trips", trip, trip.version):
                raise S.VehicleError(409, "The trip changed at the same time. Try again.", "stale")
            if event.odometer_km is not None:
                S.add_reading(store, context, vehicle, km=event.odometer_km, read_at=trip.ended_at, source="trip_end", source_id=trip.id)
            status, view = "ended", _trip_view(trip)
            message = (f"Trip saved: {trip.distance_km} km." if trip.distance_km is not None
                       else "Trip saved. Type the odometer next time to get the distance.")
        if event.fuel_litres is not None:
            store.add_record("fuel", fuel_record(context, vehicle, end_key, moment, event, now))
            message += " Fuel noted: link its payment in ANUM."
        if event.parking_total is not None:
            store.add_record("service", parking_record(context, vehicle, parking_key, moment, event,
                                                  trip.id if trip else None, now))
            message += " Parking noted: link its payment in ANUM."
        return {"status": status, "trip": view, "message": message}


def fuel_record(context: TenantContext, vehicle: Vehicle, key: str, moment: datetime, event: SessionEvent, now: datetime) -> Any:
    from .models import FuelEntry

    price = (event.fuel_total / event.fuel_litres).quantize(Decimal("0.001")) if event.fuel_total and event.fuel_litres else None
    return FuelEntry(id=new_id("vfuel"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                     vehicle_id=vehicle.id, visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, filled_at=moment,
                     fuel_type=vehicle.fuel_type, litres=event.fuel_litres, price_per_litre=price, total=event.fuel_total, currency=vehicle.currency,
                     odometer_km=event.odometer_km, full_tank=event.fuel_full, idem_key=key, notes="From the iPhone", created_at=now, updated_at=now)


def parking_record(context: TenantContext, vehicle: Vehicle, key: str, moment: datetime, event: SessionEvent, trip_id: str | None, now: datetime) -> Any:
    from .models import ServiceRecord

    return ServiceRecord(id=new_id("vsvc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                         vehicle_id=vehicle.id, visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, kind="parking",
                         service_on=moment.date(), trip_id=trip_id, receipt_total=event.parking_total, currency=vehicle.currency,
                         description="From the iPhone", idem_key=key, created_at=now, updated_at=now)


@router.post(INGEST_PATH, response_model=None)
def driving_session(event: SessionEvent, principal: DevicePrincipal = Depends(device_principal)) -> Any:
    from ..finance.devices import report_failure

    context = principal.context
    if not has_permission(context, Permission.VEHICLE_WRITE):
        report_failure(context, principal.device_id, SCOPE, "not_allowed")
        return JSONResponse(status_code=403, content={"detail": "This person can no longer log vehicles (their role changed).", "code": "forbidden"})
    try:
        return handle(context, principal.device_id, event)
    except S.VehicleError as exc:
        if principal.via == "device_token":
            report_failure(context, principal.device_id, SCOPE, exc.code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})


def self_check(context: TenantContext) -> list[tuple[str, bool]]:
    """The synthetic check on Settings, Integrations, iPhone: records nothing."""
    with open_vehicle_store(context) as store:
        vehicles, total = store.list_vehicles(context, query=None, archived=False, limit=2, offset=0)
        default = S.preferences(store, context).default_vehicle_id
    return [("vehicle_added", total > 0), ("vehicle_chosen", total == 1 or default is not None),
            ("permission", has_permission(context, Permission.VEHICLE_WRITE))]


def register() -> None:
    from ..finance.devices import Integration, integration, register_integration

    if integration(INTEGRATION) is None:
        register_integration(Integration(key=INTEGRATION, scope=SCOPE, permission=Permission.VEHICLE_WRITE, ingest_path=INGEST_PATH,
                                         per_minute=10, per_day=300, default_on=False, self_check=self_check, order=35,
                                         body_fields=("action", "vehicle", "occurred_at", "idempotency_key", "odometer_km", "fuel_litres",
                                                      "fuel_total", "parking_total")))


__all__ = ["INGEST_PATH", "SCOPE", "SessionEvent", "device_principal", "handle", "register", "register_device_authority", "router"]
