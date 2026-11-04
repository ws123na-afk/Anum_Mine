"""Purchases, deliveries, returns and refunds (docs/purchases.md).

Rows hold what was ordered, where it is and what is being sent back. They never hold posted money:
a purchase points at its canonical Finance transaction through ``finance_links`` (dimension
``purchase``, role ``payment``) and a refund points at the refund's own canonical transaction (role
``refund``, target the return). Each keeps a read copy of its link in :class:`PaymentRef`
(docs/finance-links.md). ``total`` and a return's ``amount`` are the shop's figures, details only.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

STATUSES = ("ordered", "confirmed", "shipped", "out_for_delivery", "delivered", "partially_delivered", "cancelled", "return_requested",
            "return_approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "disputed")
RETURN_STATUSES = ("requested", "approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "rejected",
                   "disputed", "cancelled")
RETURN_REASONS = ("damaged", "defective", "wrong_item", "not_as_described", "missing_parts", "late", "changed_mind", "other")
SHIPMENT_STATUSES = ("pending", "in_transit", "out_for_delivery", "delivered", "failed", "returned_to_sender")
PAYMENT_METHODS = ("card", "apple_pay", "cash", "bank_transfer", "cash_on_delivery", "instalments", "gift_card", "other")
EVENT_KINDS = ("status", "note", "shipment", "return", "payment", "refund", "share")
REMINDER_KINDS = ("delivery_expected", "delivery_late", "return_window", "pickup_due", "refund_overdue", "warranty", "missing_confirmation")

Status = Literal["ordered", "confirmed", "shipped", "out_for_delivery", "delivered", "partially_delivered", "cancelled", "return_requested",
                 "return_approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "disputed"]
ReturnStatus = Literal["requested", "approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "rejected",
                       "disputed", "cancelled"]
ReturnReason = Literal["damaged", "defective", "wrong_item", "not_as_described", "missing_parts", "late", "changed_mind", "other"]
ShipmentStatus = Literal["pending", "in_transit", "out_for_delivery", "delivered", "failed", "returned_to_sender"]
PaymentMethod = Literal["card", "apple_pay", "cash", "bank_transfer", "cash_on_delivery", "instalments", "gift_card", "other"]
EventKind = Literal["status", "note", "shipment", "return", "payment", "refund", "share"]
ReminderKind = Literal["delivery_expected", "delivery_late", "return_window", "pickup_due", "refund_overdue", "warranty", "missing_confirmation"]

# The purchase state machine (docs/purchases.md#lifecycle): the states each state may move to.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "ordered": ("confirmed", "shipped", "out_for_delivery", "partially_delivered", "delivered", "cancelled", "disputed"),
    "confirmed": ("shipped", "out_for_delivery", "partially_delivered", "delivered", "cancelled", "disputed"),
    "shipped": ("out_for_delivery", "partially_delivered", "delivered", "cancelled", "disputed"),
    "out_for_delivery": ("shipped", "partially_delivered", "delivered", "disputed"),
    "partially_delivered": ("shipped", "out_for_delivery", "delivered", "return_requested", "disputed"),
    "delivered": ("return_requested", "refund_pending", "disputed"),
    "cancelled": ("refund_pending", "partially_refunded", "refunded", "disputed"),
    "return_requested": ("return_approved", "pickup_scheduled", "returned", "delivered", "refund_pending", "disputed"),
    "return_approved": ("pickup_scheduled", "returned", "delivered", "refund_pending", "disputed"),
    "pickup_scheduled": ("returned", "return_approved", "disputed"),
    "returned": ("refund_pending", "partially_refunded", "refunded", "disputed"),
    "refund_pending": ("partially_refunded", "refunded", "disputed"),
    "partially_refunded": ("refunded", "refund_pending", "return_requested", "disputed"),
    "refunded": ("disputed",),
    "disputed": ("delivered", "returned", "refund_pending", "partially_refunded", "refunded", "cancelled"),
}
# A return's own state machine.
RETURN_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "requested": ("approved", "pickup_scheduled", "returned", "refund_pending", "rejected", "cancelled", "disputed"),
    "approved": ("pickup_scheduled", "returned", "refund_pending", "cancelled", "disputed"),
    "pickup_scheduled": ("returned", "approved", "cancelled", "disputed"),
    "returned": ("refund_pending", "disputed"),
    "refund_pending": ("disputed",),
    "partially_refunded": ("refund_pending", "disputed"),
    "refunded": ("disputed",),
    "rejected": ("disputed", "requested"),
    "disputed": ("approved", "returned", "refund_pending", "rejected", "cancelled"),
    "cancelled": (),
}
# What a return's status means for its purchase (moved only when the purchase's own machine allows it).
RETURN_TO_PURCHASE = {"requested": "return_requested", "approved": "return_approved", "pickup_scheduled": "pickup_scheduled", "returned": "returned",
                      "refund_pending": "refund_pending", "partially_refunded": "partially_refunded", "refunded": "refunded", "disputed": "disputed"}
AWAITING_DELIVERY = frozenset({"ordered", "confirmed", "shipped", "out_for_delivery", "partially_delivered"})
CLOSED = frozenset({"refunded", "cancelled"})
OPEN_RETURN = frozenset({"requested", "approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "disputed"})
REFUND_WAITING = frozenset({"returned", "refund_pending", "partially_refunded"})
# Statuses a refund link may set on its own (from the refunded amount); never set by hand.
REFUND_DRIVEN = frozenset({"partially_refunded", "refunded"})


class Ownership(StrEnum):
    PERSONAL = "personal"  # only its owner sees it
    BUSINESS = "business"  # every workspace member allowed to use purchases sees it


PLACES = {"tax_amount": 2, "shipping_amount": 2, "total": 2, "unit_price": 2, "amount": 2, "quantity": 3}


class _Quantized(BaseModel):
    @model_validator(mode="after")
    def _places(self):  # type: ignore[no-untyped-def]
        for name, places in PLACES.items():
            value = getattr(self, name, None)
            if isinstance(value, Decimal):
                object.__setattr__(self, name, value.quantize(Decimal(1).scaleb(-places)))
        return self


class PaymentRef(_Quantized):
    """A read copy of the finance link behind a payment or a refund (kept current by the link itself)."""

    link_id: str
    canonical_id: str
    status: Literal["active", "needs_review", "void"]
    status_reason: str | None = None
    amount: Decimal
    currency: str
    paid_by: str
    recorded_as_cash: bool = False
    linked_at: datetime


class _Scoped(_Quantized):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


class Purchase(_Scoped):
    id: str
    ownership: Ownership
    order_ref: str | None = None
    merchant: str
    ordered_on: date
    currency: str
    tax_amount: Decimal | None = None
    shipping_amount: Decimal | None = None
    total: Decimal | None = None  # the shop's figure, a detail: money is the linked payment
    payment_method: str | None = None
    payment: PaymentRef | None = None
    document_id: str | None = None
    expected_delivery_on: date | None = None
    delivered_on: date | None = None
    address_ref: str | None = None  # a label such as "Home" or "Office", never the address itself
    warranty_until: date | None = None
    return_window_until: date | None = None
    status: str = "ordered"
    source: Literal["web", "iphone"] = "web"
    needs_review: bool = False
    notes: str | None = None
    archived_at: datetime | None = None
    idem_key: str
    version: int = 1
    created_at: datetime
    updated_at: datetime


class _Child(_Scoped):
    id: str
    purchase_id: str
    visibility: Ownership  # copied from the purchase
    purchase_owner_id: str  # copied from the purchase


class Item(_Child):
    position: int = 0
    description: str
    sku: str | None = None
    quantity: Decimal = Decimal(1)
    unit_price: Decimal | None = None
    warranty_until: date | None = None
    created_at: datetime


class Event(_Child):
    kind: str
    from_status: str | None = None
    to_status: str | None = None
    subject_id: str | None = None
    note: str | None = None
    source: Literal["web", "iphone", "system"] = "web"
    occurred_at: datetime
    created_at: datetime


class Shipment(_Child):
    carrier: str | None = None
    tracking_number: str | None = None
    status: str = "in_transit"
    shipped_on: date | None = None
    expected_on: date | None = None
    delivered_on: date | None = None
    note: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


class ReturnLine(_Quantized):
    item_id: str
    quantity: Decimal


class Return(_Child):
    status: str = "requested"
    requested_on: date
    reason: str
    reason_note: str | None = None
    items: list[ReturnLine] = Field(default_factory=list)
    amount: Decimal | None = None  # what the shop should refund, a detail: money is the linked refund
    currency: str
    deadline_on: date | None = None
    pickup_on: date | None = None
    courier: str | None = None
    tracking_number: str | None = None
    proof_document_id: str | None = None
    confirmation_ref: str | None = None
    refund_ref: str | None = None
    returned_on: date | None = None
    expected_refund_on: date | None = None
    refunded_on: date | None = None
    dispute_note: str | None = None
    refunds: list[PaymentRef] = Field(default_factory=list)
    idem_key: str
    version: int = 1
    created_at: datetime
    updated_at: datetime


class ReminderSent(_Child):
    kind: str
    source_id: str
    due_on: date
    step: int
    sent_at: datetime


class Preferences(_Scoped):
    reminders: bool = True
    lead_days: int = Field(default=3, ge=1, le=60)
    warranty_days: int = Field(default=30, ge=1, le=120)
    default_currency: str = "SAR"
    version: int = 0
    updated_at: datetime | None = None


PAYMENT_TARGET = "purchase"  # the purchase's payment link
REFUND_TARGET = "purchase_return"  # a refund link targets the return it pays back
TARGET_TYPES = frozenset({PAYMENT_TARGET, REFUND_TARGET})

__all__ = [
    "AWAITING_DELIVERY",
    "CLOSED",
    "Event",
    "Item",
    "OPEN_RETURN",
    "Ownership",
    "PAYMENT_METHODS",
    "PAYMENT_TARGET",
    "PaymentRef",
    "Preferences",
    "Purchase",
    "REFUND_TARGET",
    "REFUND_WAITING",
    "RETURN_REASONS",
    "RETURN_STATUSES",
    "RETURN_TO_PURCHASE",
    "RETURN_TRANSITIONS",
    "ReminderSent",
    "Return",
    "ReturnLine",
    "STATUSES",
    "SHIPMENT_STATUSES",
    "Shipment",
    "TARGET_TYPES",
    "TRANSITIONS",
]
