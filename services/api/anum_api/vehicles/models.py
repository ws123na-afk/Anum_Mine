"""Vehicle, fuel and mileage records (docs/vehicles.md).

Records hold quantities, odometer values and details. They never hold posted money: a fuel entry,
a service record or a vehicle's purchase points at a canonical Finance transaction through
``finance_links`` and keeps a read copy of that link in :class:`PaymentRef` (docs/finance-links.md).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

FUEL_TYPES = ("petrol_91", "petrol_95", "petrol_98", "diesel", "electric", "hybrid", "lpg", "other")
VEHICLE_TYPES = ("car", "suv", "pickup", "van", "truck", "motorcycle", "bus", "other")
SERVICE_KINDS = ("maintenance", "oil", "tyres", "battery", "repair", "inspection", "insurance", "registration", "fine", "toll", "parking",
                 "warranty", "wash", "other")
PAYMENT_METHODS = ("card", "apple_pay", "cash", "fleet_card", "bank_transfer", "other")
COUNTRIES = ("SA", "AE")
COUNTRY_CURRENCY = {"SA": "SAR", "AE": "AED"}
TRIP_PURPOSES = ("business", "personal", "commute")
READING_SOURCES = ("manual", "fuel", "trip_start", "trip_end", "service", "device", "correction")

FuelType = Literal["petrol_91", "petrol_95", "petrol_98", "diesel", "electric", "hybrid", "lpg", "other"]
VehicleType = Literal["car", "suv", "pickup", "van", "truck", "motorcycle", "bus", "other"]
ServiceKind = Literal["maintenance", "oil", "tyres", "battery", "repair", "inspection", "insurance", "registration", "fine", "toll", "parking",
                      "warranty", "wash", "other"]
PaymentMethod = Literal["card", "apple_pay", "cash", "fleet_card", "bank_transfer", "other"]
Country = Literal["SA", "AE"]
TripPurpose = Literal["business", "personal", "commute"]


class Ownership(StrEnum):
    PERSONAL = "personal"  # only its owner sees it
    BUSINESS = "business"  # every workspace member allowed to use vehicles sees it


class PaymentRef(BaseModel):
    """A read copy of the finance link behind a cost (kept current by the link itself)."""

    link_id: str
    canonical_id: str
    status: Literal["active", "needs_review", "void"]
    status_reason: str | None = None
    amount: Decimal
    currency: str
    paid_by: str
    recorded_as_cash: bool = False
    linked_at: datetime


class _Scoped(BaseModel):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


class Vehicle(_Scoped):
    id: str
    ownership: Ownership
    registration: str
    vin: str | None = None
    nickname: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vehicle_type: str = "car"
    country: str
    currency: str
    fuel_type: str
    tank_litres: Decimal | None = None
    acquisition_date: date | None = None
    purchase_price: Decimal | None = None
    residual_value: Decimal | None = None
    useful_life_months: int | None = None
    purchase_payment: PaymentRef | None = None
    insurance_expires_on: date | None = None
    registration_expires_on: date | None = None
    inspection_expires_on: date | None = None
    notes: str | None = None
    archived_at: datetime | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


class _Child(_Scoped):
    id: str
    vehicle_id: str
    visibility: Ownership  # copied from the vehicle
    vehicle_owner_id: str  # copied from the vehicle


class OdometerReading(_Child):
    reading_km: Decimal
    read_at: datetime
    source: str
    source_id: str | None = None
    correction: bool = False
    reason: str | None = None
    created_at: datetime


class FuelEntry(_Child):
    filled_at: datetime
    station: str | None = None
    fuel_type: str
    litres: Decimal
    price_per_litre: Decimal | None = None
    total: Decimal | None = None  # the pump's figure, a detail: money is the linked payment
    currency: str
    odometer_km: Decimal | None = None
    full_tank: bool = True
    payment_method: str | None = None
    payment: PaymentRef | None = None
    document_id: str | None = None
    notes: str | None = None
    idem_key: str
    version: int = 1
    created_at: datetime
    updated_at: datetime


class Trip(_Child):
    status: Literal["open", "closed"] = "closed"
    started_at: datetime
    ended_at: datetime | None = None
    start_odometer_km: Decimal | None = None
    end_odometer_km: Decimal | None = None
    distance_km: Decimal | None = None
    distance_basis: Literal["typed", "previous_reading"] | None = None
    purpose: str = "business"
    purpose_note: str | None = None
    project_id: str | None = None
    origin: str | None = None
    destination: str | None = None
    notes: str | None = None
    source: Literal["web", "device"] = "web"
    device_id: str | None = None
    idem_key: str
    version: int = 1
    created_at: datetime
    updated_at: datetime


class ServiceRecord(_Child):
    kind: str
    service_on: date
    odometer_km: Decimal | None = None
    workshop: str | None = None
    description: str | None = None
    reference: str | None = None
    next_due_on: date | None = None
    next_due_km: Decimal | None = None
    warranty_until: date | None = None
    trip_id: str | None = None
    receipt_total: Decimal | None = None  # the receipt's figure, a detail: money is the linked payment
    currency: str
    payment_method: str | None = None
    payment: PaymentRef | None = None
    document_id: str | None = None
    idem_key: str
    version: int = 1
    created_at: datetime
    updated_at: datetime


class ReminderSent(_Child):
    kind: Literal["insurance", "registration", "inspection", "service"]
    source_id: str
    due_on: date
    step: int
    sent_at: datetime


class Preferences(_Scoped):
    trip_places: bool = False  # store trip origin and destination (off until the person opts in)
    default_vehicle_id: str | None = None
    reminder_days: int = Field(default=30, ge=1, le=120)
    reminders: bool = True
    version: int = 0
    updated_at: datetime | None = None


COST_KINDS = {"fuel_entry": "vehicle_fuel_entry", "service_record": "vehicle_service_record", "purchase": "vehicle"}
TARGET_TYPES = frozenset(COST_KINDS.values())

__all__ = [
    "COST_KINDS",
    "COUNTRIES",
    "COUNTRY_CURRENCY",
    "FUEL_TYPES",
    "FuelEntry",
    "OdometerReading",
    "Ownership",
    "PAYMENT_METHODS",
    "PaymentRef",
    "Preferences",
    "READING_SOURCES",
    "ReminderSent",
    "SERVICE_KINDS",
    "ServiceRecord",
    "TARGET_TYPES",
    "TRIP_PURPOSES",
    "Trip",
    "VEHICLE_TYPES",
    "Vehicle",
]
