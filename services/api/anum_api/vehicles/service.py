"""Vehicle rules (docs/vehicles.md): who may change what, the odometer rule, payments and reminders.

* A **personal** vehicle and its records belong to one person. A **business** vehicle is visible to
  every member who may use vehicles; anyone of them logs fuel, trips and service on it, and only the
  person who added the vehicle or a workspace owner (``vehicle:manage``) changes or deletes it. A
  record is changed by the person who logged it or a workspace owner.
* **The odometer never goes down** without a correction that carries a reason. The rule is checked
  here under a lock on the vehicle row (to answer with the readings involved) and again by the
  database trigger of migration ``0135_vehicles``.
* **Payments are links, never postings.** A fuel entry, a service record or the vehicle's purchase
  points at a canonical Finance transaction through ``finance_links`` (dimension ``vehicle``). A cash
  payment is first recorded once in Finance (the same path as a hand-entered transaction), then
  linked. The link keeps the record's read copy (``payment``) current through :func:`observe`, in
  the link's own transaction, through corrections, reconciliation and undo.

Everything here is synchronous: FastAPI runs it in its thread pool. No unit of work is held across
another module's call.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import TenantContext, new_id, utc_now
from . import analytics as A
from .models import COST_KINDS, COUNTRY_CURRENCY, TARGET_TYPES, FuelEntry, OdometerReading, Ownership, PaymentRef, Preferences, ServiceRecord, Trip, Vehicle
from .store import OdometerRefused, RecordKind, VehicleStore, check_odometer, open_vehicle_store, store_for_session, vehicle_memory_store

VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{11,17}$")
REGISTRATION = re.compile(r"^[0-9A-Z؀-ۿ -]{1,20}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
FUTURE_SLACK = timedelta(minutes=10)
KIND_OF_TARGET = {"vehicle_fuel_entry": "fuel", "vehicle_service_record": "service"}


class VehicleError(Exception):
    def __init__(self, status_code: int, message: str, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.extra = extra or {}


def not_found(what: str = "Vehicle") -> VehicleError:
    return VehicleError(404, f"{what} not found. It may have been deleted, or it is someone's personal vehicle.", "not_found")


# -- who may do what -------------------------------------------------------------------------------


def can_manage(context: TenantContext) -> bool:
    return has_permission(context, Permission.VEHICLE_MANAGE)


def can_edit_vehicle(context: TenantContext, vehicle: Vehicle) -> bool:
    return vehicle.owner_user_id == context.user_id or (vehicle.ownership == Ownership.BUSINESS and can_manage(context))


def can_edit_record(context: TenantContext, record: Any) -> bool:
    return record.owner_user_id == context.user_id or (record.visibility == Ownership.BUSINESS and can_manage(context))


def require_vehicle_edit(context: TenantContext, vehicle: Vehicle) -> None:
    if not can_edit_vehicle(context, vehicle):
        raise VehicleError(403, "Only the person who added this business vehicle or a workspace owner can change it.", "not_vehicle_owner")


def require_record_edit(context: TenantContext, record: Any) -> None:
    if not can_edit_record(context, record):
        raise VehicleError(403, "Only the person who logged this record or a workspace owner can change it.", "not_record_owner")


# -- cleaning ----------------------------------------------------------------------------------------


def clean(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())[:limit]
    return text or None


def registration(value: str) -> str:
    text = " ".join(value.split()).upper()
    if not REGISTRATION.match(text):
        raise VehicleError(422, "Registration: use letters, digits, spaces or dashes, up to 20 characters.", "invalid_registration")
    return text


def vin(value: str | None) -> str | None:
    text = (value or "").replace(" ", "").upper()
    if not text:
        return None
    if not VIN.match(text):
        raise VehicleError(422, "VIN: 11 to 17 letters and digits, without I, O or Q.", "invalid_vin")
    return text


def mask_vin(value: str | None) -> str | None:
    """Lists show only the last four characters of a VIN."""
    if not value:
        return None
    return "•" * (len(value) - 4) + value[-4:]


def at_noon(day: date) -> datetime:
    moment = datetime.combine(day, time(12, 0), tzinfo=UTC)
    return min(moment, utc_now())


def not_future(moment: datetime, field: str) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if moment > utc_now() + FUTURE_SLACK:
        raise VehicleError(422, f"{field}: the time is in the future.", "in_future")
    return moment


def record_key(context: TenantContext, *parts: object) -> str:
    return hashlib.sha256("\x1f".join((context.user_id, *(str(p) for p in parts))).encode()).hexdigest()


# -- vehicles ----------------------------------------------------------------------------------------


def get_vehicle(store: VehicleStore, context: TenantContext, vehicle_id: str) -> Vehicle:
    found = store.get_vehicle(context, vehicle_id) if SAFE_ID.match(vehicle_id) else None
    if found is None:
        raise not_found()
    return found


def new_vehicle(context: TenantContext, data: dict[str, Any], now: datetime) -> Vehicle:
    country = data["country"]
    currency = (data.get("currency") or COUNTRY_CURRENCY[country]).upper()
    return check_vehicle(Vehicle(
        id=new_id("veh"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        ownership=Ownership(data.get("ownership") or "personal"), registration=registration(data["registration"]), vin=vin(data.get("vin")),
        nickname=clean(data.get("nickname"), 60), make=clean(data.get("make"), 60), model=clean(data.get("model"), 60), year=data.get("year"),
        vehicle_type=data.get("vehicle_type") or "car", country=country, currency=currency, fuel_type=data["fuel_type"],
        tank_litres=data.get("tank_litres"), acquisition_date=data.get("acquisition_date"), purchase_price=data.get("purchase_price"),
        residual_value=data.get("residual_value"), useful_life_months=data.get("useful_life_months"),
        insurance_expires_on=data.get("insurance_expires_on"), registration_expires_on=data.get("registration_expires_on"),
        inspection_expires_on=data.get("inspection_expires_on"), notes=clean(data.get("notes"), 1000), created_at=now, updated_at=now))


def check_vehicle(vehicle: Vehicle) -> Vehicle:
    if not re.fullmatch(r"[A-Z]{3}", vehicle.currency):
        raise VehicleError(422, "Currency: a three-letter code such as SAR or AED.", "invalid_currency")
    if vehicle.residual_value is not None and vehicle.purchase_price is not None and vehicle.residual_value > vehicle.purchase_price:
        raise VehicleError(422, "The value at the end cannot be more than the purchase price.", "invalid_residual")
    if vehicle.acquisition_date is not None and vehicle.acquisition_date > utc_now().date():
        raise VehicleError(422, "The acquisition date is in the future.", "in_future")
    return vehicle


def apply_vehicle_changes(vehicle: Vehicle, changes: dict[str, Any], now: datetime) -> Vehicle:
    data = vehicle.model_dump()
    for key, value in changes.items():
        if key == "registration":
            value = registration(value)
        elif key == "vin":
            value = vin(value)
        elif key in ("nickname", "make", "model"):
            value = clean(value, 60)
        elif key == "notes":
            value = clean(value, 1000)
        elif key == "currency" and value:
            value = value.upper()
        data[key] = value
    data["updated_at"] = now
    return check_vehicle(Vehicle.model_validate(data))


def has_payments(store: VehicleStore, context: TenantContext, vehicle: Vehicle) -> bool:
    if vehicle.purchase_payment is not None and vehicle.purchase_payment.status != "void":
        return True
    for kind in ("fuel", "service"):
        for record in store.records_for(context, kind, [vehicle.id]):  # type: ignore[arg-type]
            if record.payment is not None and record.payment.status != "void":
                return True
    return False


# -- odometer ----------------------------------------------------------------------------------------


def add_reading(store: VehicleStore, context: TenantContext, vehicle: Vehicle, *, km: Decimal, read_at: datetime, source: str,
                source_id: str | None, correction: bool = False, reason: str | None = None, now: datetime | None = None) -> OdometerReading:
    """Add one reading (the caller's unit of work). The odometer may go down only through a correction with a reason."""
    now = now or utc_now()
    if km < 0 or km >= Decimal(10_000_000):
        raise VehicleError(422, "Odometer: enter the kilometres shown, from 0 to 9,999,999.", "invalid_odometer")
    if correction:
        reason = clean(reason, 300)
        if not reason or len(reason) < 3:
            raise VehicleError(422, "A correction needs a reason, for example \"cluster replaced\" or \"typo on 3 May\".", "reason_required")
        require_vehicle_edit(context, vehicle)
    lock = getattr(store, "lock_vehicle", None)
    if callable(lock):
        lock(context, vehicle.id)  # one reading of a vehicle at a time: the check below and the trigger agree
    reading = OdometerReading(
        id=new_id("vodo"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, vehicle_id=vehicle.id,
        visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, reading_km=km.quantize(Decimal("0.1")), read_at=read_at,
        source="correction" if correction else source, source_id=source_id, correction=correction, reason=reason if correction else None,
        created_at=now)
    before, after = check_odometer(store.readings(context, vehicle.id), reading)
    if before is not None or after is not None:
        other = before or after
        assert other is not None  # nosec B101: one of the two is set
        raise VehicleError(409, (f"The odometer cannot go down: {other.reading_km} km was recorded on {other.read_at.date().isoformat()}. "
                                 "If the odometer was replaced or a reading was wrong, add a correction with a reason."), "odometer_decrease",
                           {"reading_km": str(other.reading_km), "read_at": other.read_at.isoformat(), "direction": "before" if before else "after"})
    try:
        store.add_reading(reading)
    except OdometerRefused:
        raise VehicleError(409, "The odometer cannot go down. Add a correction with a reason if the earlier reading was wrong.",
                           "odometer_decrease") from None
    return reading


def replace_reading(store: VehicleStore, context: TenantContext, vehicle: Vehicle, source_id: str, *, km: Decimal | None,
                    read_at: datetime, source: str) -> None:
    store.delete_readings(context, vehicle.id, source_id)
    if km is not None:
        add_reading(store, context, vehicle, km=km, read_at=read_at, source=source, source_id=source_id)


def previous_reading(store: VehicleStore, context: TenantContext, vehicle_id: str, moment: datetime) -> OdometerReading | None:
    rows = [r for r in store.readings(context, vehicle_id) if r.read_at <= moment]
    return rows[-1] if rows else None


# -- trips -------------------------------------------------------------------------------------------


def trip_distance(store: VehicleStore, context: TenantContext, trip: Trip) -> Trip:
    """Distance from the two typed readings, else from the last reading before the trip started (never from the phone)."""
    if trip.end_odometer_km is None:
        trip.distance_km, trip.distance_basis = None, None
    elif trip.start_odometer_km is not None:
        trip.distance_km, trip.distance_basis = trip.end_odometer_km - trip.start_odometer_km, "typed"
    else:
        before = previous_reading(store, context, trip.vehicle_id, trip.started_at)
        if before is not None and trip.end_odometer_km >= before.reading_km:
            trip.distance_km, trip.distance_basis = trip.end_odometer_km - before.reading_km, "previous_reading"
        else:
            trip.distance_km, trip.distance_basis = None, None
    return trip


def preferences(store: VehicleStore, context: TenantContext) -> Preferences:
    return store.get_preferences(context) or Preferences(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id)


# -- payments ----------------------------------------------------------------------------------------


def target_of(kind: str) -> str:
    return COST_KINDS[kind]


def _link_error(exc: Any) -> VehicleError:
    return VehicleError(exc.status_code, exc.message, exc.code, exc.extra)


def payment_target(context: TenantContext, kind: str, record_id: str) -> tuple[Any, Vehicle]:
    """The record (or vehicle) a payment is linked to, checked for the caller."""
    if kind not in COST_KINDS:
        raise VehicleError(404, "Not found.", "not_found")
    with open_vehicle_store(context) as store:
        if kind == "purchase":
            vehicle = get_vehicle(store, context, record_id)
            require_vehicle_edit(context, vehicle)
            return vehicle, vehicle
        record = store.get_record(context, "fuel" if kind == "fuel_entry" else "service", record_id) if SAFE_ID.match(record_id) else None
        if record is None:
            raise not_found("Record")
        if record.owner_user_id != context.user_id:
            raise VehicleError(403, "Only the person who logged this record can link their payment to it.", "not_record_owner")
        return record, get_vehicle(store, context, record.vehicle_id)


def _current_payment(target: Any) -> PaymentRef | None:
    return target.purchase_payment if isinstance(target, Vehicle) else target.payment


def link_payment(context: TenantContext, kind: str, record_id: str, *, canonical_id: str, amount: Decimal | None,
                 currency: str | None = None) -> tuple[PaymentRef, bool]:
    """Link (part of) a canonical transaction to the record. Returns the payment and whether it was a replay.

    The link's idempotency key names the record only, so a record has at most one live vehicle link: two tabs
    linking different payments at once are serialised by the owner's lock in ``finance.links.create`` and the
    second is refused (``already_linked``)."""
    from ..finance import links as K
    from ..finance.links_store import open_link_stores

    target, _vehicle = payment_target(context, kind, record_id)
    current = _current_payment(target)
    if current is not None and current.status != "void":
        if current.canonical_id == canonical_id:
            return current, True  # the same payment again: nothing changes
        raise VehicleError(409, "This record is already linked to another payment. Remove that link first.", "already_linked")
    if not SAFE_ID.match(canonical_id):
        raise VehicleError(422, "The payment id is not valid.", "invalid_id")
    with open_link_stores(context) as (_finance, store, links):
        chain = store.chain(context, canonical_id)
        caps = K.capacity(chain) if chain else {}
        live = links.live_links(context, canonical_id) if chain else []
    if not chain:
        raise VehicleError(404, "Payment not found in your Finance ledger.", "transaction_not_found")
    if currency is not None:
        currency = currency.upper()
        if currency not in caps:
            raise VehicleError(422, f"The payment is not in {currency}. Link it in its own currency.", "currency_mismatch", {"currencies": sorted(caps)})
    elif len(caps) == 1:
        currency = next(iter(caps))
    else:
        raise VehicleError(422, "This payment has more than one currency: choose the currency and enter the amount for this vehicle.",
                           "currency_required", {"currencies": sorted(caps)})
    if amount is None:
        amount = caps[currency] - K.used(live, "vehicle", currency)
        if amount <= 0:
            raise VehicleError(409, "Nothing of this payment is left to link to a vehicle.", "over_allocated", {"remaining": "0", "currency": currency})
    try:
        created = K.create(context, K.LinkRequest(canonical_id=canonical_id, dimension="vehicle", role="payment", target_type=target_of(kind),
                                                  target_id=record_id, amount=amount, currency=currency,
                                                  idempotency_key=f"vehicles\x1f{kind}\x1f{record_id}"))
    except K.LinkError as exc:
        if exc.code == "idempotency_conflict":
            raise VehicleError(409, "This record was just linked to another payment. Reload to see it.", "already_linked") from None
        raise _link_error(exc) from None
    return payment_from_link(created.link), created.replayed


CASH_CATEGORY = {"fuel": "fuel", "insurance": "insurance", "registration": "fees", "fine": "fees"}


def record_cash(context: TenantContext, kind: str, record_id: str, *, account_id: str, amount: Decimal, posted_on: date,
                idempotency_key: str | None) -> tuple[PaymentRef, bool]:
    """A cash payment: one hand-entered Finance transaction (the ledger's own path), then the link. Retrying is safe:
    the transaction id is derived from the record and the request, so a retry finds the same transaction."""
    from ..finance.api import TransactionInput, record_manual_transaction
    from ..finance.models import Category

    if amount <= 0:
        raise VehicleError(422, "The amount must be more than zero.", "invalid_amount")
    target, vehicle = payment_target(context, kind, record_id)
    current = _current_payment(target)
    if current is not None and current.status != "void":
        if current.recorded_as_cash:
            return current, True
        raise VehicleError(409, "This record is already linked to a payment. Remove that link first.", "already_linked")
    label = {"fuel_entry": "Fuel", "service_record": f"Vehicle {getattr(target, 'kind', 'service')}", "purchase": "Vehicle purchase"}[kind]
    category_key = "fuel" if kind == "fuel_entry" else CASH_CATEGORY.get(getattr(target, "kind", ""), "transport")
    seed = idempotency_key or f"v{getattr(target, 'version', 1)}"
    body = TransactionInput(account_id=account_id, posted_on=posted_on, amount=-amount, description=f"{label}, {vehicle.registration}",
                            category=Category(category_key), notes="Recorded from Vehicles")
    from fastapi import HTTPException

    # The id is derived from the record and the request, so a retry finds the transaction it made. An earlier cash
    # payment of this record with other figures (unlinked, then entered again) is never reused: the next id is tried.
    canonical, created = None, None
    for attempt in range(8):
        transaction_id = "fin_txn_veh" + record_key(context, "cash", kind, record_id, seed, attempt)[:32]
        existed = _transaction_exists(context, transaction_id)
        try:
            item, _decision, found = record_manual_transaction(context, body, transaction_id=transaction_id)
        except HTTPException as exc:
            raise VehicleError(exc.status_code, str(exc.detail), "finance_refused") from None
        if item.account_id == account_id and item.amount == -amount and item.posted_on == posted_on:
            canonical, created = found, (None if existed else transaction_id)
            break
    if canonical is None:
        raise VehicleError(409, "This cash payment could not be recorded. Try again with a new request.", "cash_conflict")
    try:
        payment, replayed = link_payment(context, kind, record_id, canonical_id=canonical, amount=None)
    except VehicleError:
        # Another tab linked a payment first: the expense this request recorded is undone, so it never stays posted.
        if created is not None:
            _undo_cash(context, created)
        raise
    payment.recorded_as_cash = True
    with open_vehicle_store(context) as store:
        _set_payment(store, context, kind, record_id, payment)
    return payment, replayed


def _transaction_exists(context: TenantContext, transaction_id: str) -> bool:
    from ..finance.store import open_finance_store

    with open_finance_store(context) as store:
        return store.get_transaction(context, transaction_id) is not None


def _undo_cash(context: TenantContext, transaction_id: str) -> None:
    """Reverse a cash transaction this request recorded (the same path as deleting it in Finance)."""
    from ..finance.sms_service import owner_lock, reverse
    from ..finance.sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        entry = sms.entry_for_transaction(context, transaction_id)
        if entry is not None and entry.status.value == "posted":
            reverse(store, sms, context, entry, now=utc_now(), why="cash payment not linked to the vehicle record")


def unlink_payment(context: TenantContext, kind: str, record_id: str) -> None:
    """Remove the record's link (the Finance transaction stays). Repeating it changes nothing. A business allocation
    used as the record's cost is let go the same way (the allocation stays in Finance)."""
    from ..finance import links as K

    target, _vehicle = payment_target(context, kind, record_id)
    current = _current_payment(target)
    if current is None:
        return
    if current.source == "allocation":
        with open_vehicle_store(context) as store:
            _set_payment(store, context, kind, record_id, None)
        return
    if current.status != "void":
        try:
            K.void(context, current.link_id, expected_version=None)
        except K.LinkError as exc:
            if exc.code != "link_not_found":
                raise _link_error(exc) from None
    with open_vehicle_store(context) as store:
        _set_payment(store, context, kind, record_id, None)


def _set_payment(store: VehicleStore, context: TenantContext, kind: str, record_id: str, payment: PaymentRef | None) -> bool:
    if kind in ("purchase", "vehicle"):
        return store.set_purchase_payment(context, record_id, payment)
    return store.set_payment(context, "fuel" if kind in ("fuel_entry", "vehicle_fuel_entry") else "service", record_id, payment)


def payment_from_link(link: Any) -> PaymentRef:
    return PaymentRef(link_id=link.id, canonical_id=link.canonical_id, status=link.status.value if hasattr(link.status, "value") else link.status,
                      status_reason=link.status_reason, amount=Decimal(link.amount).quantize(Decimal("0.01")), currency=link.currency, paid_by=link.owner_user_id,
                      linked_at=link.created_at)


def observe(links_store: Any, link: Any) -> None:
    """The finance link of a vehicle cost was made or changed (status, canonical id): update the record's read copy
    in the same unit of work. A link the owner removed clears it; an undone payment leaves it as ``void``."""
    if link.target_type not in TARGET_TYPES or link.amount is None:
        return
    context = TenantContext(tenant_id=link.tenant_id, workspace_id=link.workspace_id, user_id=link.owner_user_id)
    store = store_for_session(getattr(links_store, "session", None), context)
    kind = "purchase" if link.target_type == "vehicle" else link.target_type
    if kind == "purchase":
        found = store.get_vehicle(context, link.target_id)
        current = found.purchase_payment if found else None
    else:
        found = store.get_record(context, KIND_OF_TARGET[link.target_type], link.target_id)  # type: ignore[arg-type]
        current = found.payment if found else None
    if found is None:
        return
    status = link.status.value if hasattr(link.status, "value") else link.status
    if current is not None and current.link_id != link.id and current.status != "void" and status == "void":
        return  # an older link was voided after the record was linked again: keep the current one
    if status == "void" and link.status_reason == "removed_by_owner":
        payment = None
    else:
        payment = payment_from_link(link)
        if current is not None and current.link_id == link.id:
            payment.recorded_as_cash = current.recorded_as_cash
    _set_payment(store, context, kind, link.target_id, payment)


def link_target_exists(context: TenantContext, target_type: str, target_id: str) -> bool:
    """``finance.links`` asks whether a vehicle target exists and the caller may link a payment to it."""
    if target_type not in TARGET_TYPES or not SAFE_ID.match(target_id):
        return False
    try:
        with open_vehicle_store(context) as store:
            if target_type == "vehicle":
                vehicle = store.get_vehicle(context, target_id)
                return vehicle is not None and can_edit_vehicle(context, vehicle)
            record = store.get_record(context, KIND_OF_TARGET[target_type], target_id)  # type: ignore[arg-type]
            return record is not None and record.owner_user_id == context.user_id
    except VehicleError:
        return False


def plug_finance_links() -> None:
    """Register the ``vehicle`` link dimension with the finance links foundation."""
    from ..finance import links as K

    K.register_target("vehicle", link_target_exists, observe, single_payment=True)


# -- payment picker ----------------------------------------------------------------------------------


# -- business allocations as a vehicle's cost (docs/vehicles.md#business-allocations) ----------------------------------

_allocation_lock = __import__("threading").Lock()
ALLOCATION_KINDS = ("fuel_entry", "service_record")


def _allocations(context: TenantContext) -> list[Any]:
    """This business workspace's live allocation rows the caller made (their own payments), newest first."""
    from ..finance.links_store import open_workspace_allocations

    with open_workspace_allocations(context) as links:
        rows, _ = links.list_allocations(context, currency=None, limit=5_000, offset=0)
    return [r for r in rows if r.allocated_by == context.user_id and r.status == "active"]


def _used_allocations(store: VehicleStore, context: TenantContext) -> dict[str, str]:
    """allocation id -> the record using it, over every vehicle record the caller can see."""
    ids = [v.id for archived in (False, True) for v in store.list_vehicles(context, query=None, archived=archived, limit=10_000, offset=0)[0]]
    used: dict[str, str] = {}
    for kind in ("fuel", "service"):
        for record in store.records_for(context, kind, ids) if ids else []:  # type: ignore[arg-type]
            payment = record.payment
            if payment is not None and payment.source == "allocation" and payment.allocation_id and payment.status != "void":
                used[payment.allocation_id] = record.id
    return used


def allocation_candidates(context: TenantContext, *, around: date | None, amount: Decimal | None, limit: int) -> list[dict[str, Any]]:
    """Allocations of this business the caller made that no vehicle record uses yet, ranked by the record's figure and
    date. Only the business's own view of the payment: date, amount, currency, category, memo and the source reference."""
    rows = _allocations(context)
    if not rows:
        return []
    with open_vehicle_store(context) as store:
        used = _used_allocations(store, context)
    centre = around or utc_now().date()
    free = [r for r in rows if r.id not in used]

    def rank(row: Any) -> tuple[Any, ...]:
        gap = abs(row.amount - amount) if amount is not None else Decimal(0)
        return (gap, abs((row.posted_on - centre).days), row.id)

    return [{"allocation_id": r.id, "posted_on": r.posted_on.isoformat(), "amount": A.money(r.amount), "currency": r.currency,
             "category": r.category, "memo": r.memo, "source_ref": r.source_ref} for r in sorted(free, key=rank)[:limit]]


def use_allocation(context: TenantContext, kind: str, record_id: str, allocation_id: str) -> tuple[PaymentRef, bool]:
    """Count a business allocation the caller made as this business vehicle record's cost. Returns the read copy and
    whether it was already so. One allocation is one record's cost; a record has one cost (a link or an allocation)."""
    if kind not in ALLOCATION_KINDS:
        raise VehicleError(422, "A business allocation is a fuel or service cost. Link the vehicle's purchase as a payment.", "invalid_kind")
    target, vehicle = payment_target(context, kind, record_id)
    if vehicle.ownership != Ownership.BUSINESS:
        raise VehicleError(422, "Only a business vehicle's record can use a business allocation. Link your payment to a personal vehicle instead.",
                           "not_business_vehicle")
    current = _current_payment(target)
    if current is not None and current.status != "void":
        if current.source == "allocation" and current.allocation_id == allocation_id:
            return current, True
        raise VehicleError(409, "This record already has its cost. Remove it first.", "already_linked")
    row = next((r for r in _allocations(context) if r.id == allocation_id), None)
    if row is None:
        raise VehicleError(404, "Allocation not found in this business. Allocate the payment to this business under Finance first.",
                           "allocation_not_found")
    if row.currency != getattr(target, "currency", row.currency):
        raise VehicleError(422, f"The allocation is in {row.currency} and the record in {target.currency}.", "currency_mismatch",
                           {"currency": row.currency})
    payment = PaymentRef(link_id="", canonical_id="", status="active", amount=row.amount, currency=row.currency, paid_by=context.user_id,
                         linked_at=utc_now(), source="allocation", allocation_id=row.id, source_ref=row.source_ref)
    from sqlalchemy import text

    with _allocation_lock, open_vehicle_store(context) as store:
        session = getattr(store, "session", None)
        if session is not None:  # two requests using one allocation at once: the second waits, then sees the first
            session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                            {"k": f"vehicle_allocation:{context.tenant_id}:{context.workspace_id}"})
        used = _used_allocations(store, context)
        if used.get(allocation_id) not in (None, record_id):
            raise VehicleError(409, "Another vehicle record already counts this allocation as its cost.", "allocation_used")
        fresh = store.get_record(context, "fuel" if kind == "fuel_entry" else "service", record_id)
        if fresh is None:
            raise not_found("Record")
        if fresh.payment is not None and fresh.payment.status != "void":
            raise VehicleError(409, "This record already has its cost. Remove it first.", "already_linked")
        _set_payment(store, context, kind, record_id, payment)
    return payment, False


def refresh_allocations(context: TenantContext, records: list[Any]) -> list[Any]:
    """Bring each record whose cost is a business allocation in line with the allocation row as it is now: removed (the
    owner took the allocation back, or the payment was undone) reads ``void`` with ``allocation_removed`` and is not
    counted. Read only: the stored copy is not changed (records come from a closed unit of work)."""
    pending = [r for r in records if getattr(r, "payment", None) is not None and r.payment.source == "allocation"]
    if not pending:
        return records
    if not has_permission(context, Permission.BUSINESS_READ):
        live: dict[str, Any] = {}
    else:
        from ..finance.links_store import open_workspace_allocations

        with open_workspace_allocations(context) as links:
            rows, _ = links.list_allocations(context, currency=None, limit=10_000, offset=0)
        live = {r.id: r for r in rows if r.status == "active"}
    for record in pending:
        row = live.get(record.payment.allocation_id or "")
        if row is None:
            record.payment = record.payment.model_copy(update={"status": "void", "status_reason": "allocation_removed"})
        else:
            record.payment = record.payment.model_copy(update={"status": "active", "status_reason": None, "amount": row.amount,
                                                               "currency": row.currency})
    return records


def payment_candidates(context: TenantContext, *, query: str | None, around: date | None, amount: Decimal | None, limit: int) -> list[dict[str, Any]]:
    """The caller's own live payments (never income or transfers) near a date, with what is left to link to a vehicle.

    Two reads in one unit of work (the entries in the window with their postings, and the caller's vehicle links),
    ranked before the cut. A suggestion only: linking checks the whole chain again under the owner's lock."""
    from ..finance import links as K
    from ..finance.links_store import LinkStatus, open_link_stores

    centre = around or utc_now().date()
    start, end = centre - timedelta(days=60), centre + timedelta(days=15)
    needle = (query or "").casefold().strip()
    with open_link_stores(context) as (_finance, store, links):
        entries = store.list_entries(context, start=start, end=end, limit=5_000)
        vehicle_links, _ = links.list_links(context, canonical_id=None, dimension="vehicle", target_type=None, target_id=None, status=None,
                                            limit=10_000, offset=0)
    chains: dict[str, list[Any]] = {}
    for entry in entries:
        chains.setdefault(entry.canonical_id or entry.id, []).append(entry)
    live_by: dict[str, list[Any]] = {}
    for link in vehicle_links:
        if link.status != LinkStatus.VOID:
            live_by.setdefault(link.canonical_id, []).append(link)
    out: list[dict[str, Any]] = []
    for canonical, chain in chains.items():
        current = K.live_entry(chain)
        if current is None or (current.category or "") in ("income", "transfer"):
            continue
        if needle and needle not in f"{current.merchant or ''} {current.description}".casefold():
            continue
        live = live_by.get(canonical, [])
        for currency, cap in K.capacity(chain).items():
            remaining = cap - K.used(live, "vehicle", currency)
            out.append({"canonical_id": canonical, "posted_on": current.posted_on.isoformat(), "merchant": (current.merchant or current.description)[:80],
                        "category": current.category, "amount": A.money(cap), "currency": currency, "remaining": A.money(max(remaining, Decimal(0))),
                        "linked_to_vehicle": bool(live), "_day": current.posted_on, "_cap": cap})

    def rank(item: dict[str, Any]) -> tuple[Any, ...]:
        gap = abs(item["_cap"] - amount) if amount is not None else Decimal(0)
        return (gap, abs((item["_day"] - centre).days), item["canonical_id"])

    out.sort(key=rank)
    return [{k: v for k, v in item.items() if not k.startswith("_")} for item in out[:limit]]


# -- reminders ---------------------------------------------------------------------------------------

EXPIRY_KINDS = (("insurance", "insurance_expires_on"), ("registration", "registration_expires_on"), ("inspection", "inspection_expires_on"))
SERVICE_KM_WARNING = Decimal(500)


def due_items(vehicle: Vehicle, service: list[ServiceRecord], odometer: Decimal | None, *, today: date, window_days: int) -> list[dict[str, Any]]:
    """What is due for a vehicle within ``window_days`` (or overdue): expiry dates and the latest service of each kind."""
    if vehicle.archived_at is not None:
        return []
    out: list[dict[str, Any]] = []
    for kind, attribute in EXPIRY_KINDS:
        due: date | None = getattr(vehicle, attribute)
        if due is not None and (due - today).days <= window_days:
            out.append({"vehicle_id": vehicle.id, "registration": vehicle.registration, "kind": kind, "source_id": vehicle.id,
                        "due_on": due.isoformat(), "days_left": (due - today).days, "due_km": None, "km_left": None})
    latest: dict[str, ServiceRecord] = {}
    for record in sorted(service, key=lambda r: (r.service_on, r.created_at)):
        if record.vehicle_id == vehicle.id:
            latest[record.kind] = record
    for record in latest.values():
        days_left = (record.next_due_on - today).days if record.next_due_on else None
        km_left = (record.next_due_km - odometer) if record.next_due_km is not None and odometer is not None else None
        if (days_left is not None and days_left <= window_days) or (km_left is not None and km_left <= SERVICE_KM_WARNING):
            out.append({"vehicle_id": vehicle.id, "registration": vehicle.registration, "kind": "service", "service_kind": record.kind,
                        "source_id": record.id, "due_on": record.next_due_on.isoformat() if record.next_due_on else None, "days_left": days_left,
                        "due_km": str(record.next_due_km) if record.next_due_km is not None else None,
                        "km_left": str(km_left) if km_left is not None else None})
    out.sort(key=lambda item: (item["days_left"] if item["days_left"] is not None else 10_000, item["kind"]))
    return out


def clear_memory() -> None:
    vehicle_memory_store.clear()


__all__ = [
    "VehicleError",
    "add_reading",
    "can_edit_record",
    "can_edit_vehicle",
    "due_items",
    "link_payment",
    "mask_vin",
    "observe",
    "payment_candidates",
    "plug_finance_links",
    "record_cash",
    "unlink_payment",
]
