"""iPhone: share an order confirmation or a delivery message (docs/purchases.md#iphone-share).

The person shares one message from Mail, Messages or a shop's app to an ANUM Shortcut (Share Sheet),
or a Messages automation for a courier's sender name sends one delivery SMS. ANUM never reads the
mailbox or the messages by itself: only the text the person chose to send arrives here.

The text is **data, never instructions** (``parser.py``): fixed patterns read an order number, a
tracking number, a carrier, a shop, a delivery status and an expected date. It is stored as
purchase data only: a status change and a shipment on a matching purchase, or a new purchase that
waits for the person's review. It is **never finance**: nothing here touches the ledger or the
iPhone SMS review queue, and a message that mentions a refund or a cancellation is only noted (a
refund is the person's Finance transaction linked on the return). The raw text is never stored.

The request carries the shared iPhone device token (``Authorization: Bearer <token>``, scope
``purchases``, docs/iphone-integrations.md). :func:`register_device_authority` is the seam
``finance.devices.plug_module_authorities`` connects; without a device token the ordinary sign-in
applies. The person must hold ``purchase:write``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Permission
from ..dependencies import has_permission, tenant_context
from ..schemas import TenantContext, utc_now
from . import parser as P
from . import service as S
from .models import AWAITING_DELIVERY, TRANSITIONS, Purchase
from .store import DuplicateError, PurchaseStore, open_purchase_store

SCOPE = "purchases"
INTEGRATION = "purchases.share"
INGEST_PATH = "/api/v1/purchases/device/share"
# How far a shared message may move a purchase forward: delivery progress only, never a cancellation or a refund.
SHIPMENT_STATUS = {"shipped": "in_transit", "out_for_delivery": "out_for_delivery", "delivered": "delivered"}
SHIPMENT_RANK = {"pending": 0, "in_transit": 1, "out_for_delivery": 2, "delivered": 3}
FORWARD = {"ordered": 0, "confirmed": 1, "shipped": 2, "out_for_delivery": 3, "partially_delivered": 4, "delivered": 5}


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


class ShareEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["save", "test"] = "save"
    text: str | None = Field(default=None, max_length=P.MAX_TEXT, description="The shared message, as the person chose to send it")
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=120)
    ownership: Literal["personal", "business"] = "personal"


router = APIRouter(tags=["purchases"])


def _match(store: PurchaseStore, context: TenantContext, found: P.Shared) -> Purchase | None:
    """The purchase the message is about: by order number, else by tracking number. Only purchases the person can see."""
    if found.order_ref:
        rows = [p for p in store.find_by_order_ref(context, found.order_ref) if p.archived_at is None]
        if rows:
            return rows[0]
    if found.tracking_number:
        for shipment in store.find_by_tracking(context, found.tracking_number):
            purchase = store.get_purchase(context, shipment.purchase_id)
            if purchase is not None and purchase.archived_at is None:
                return purchase
    return None


def _forward(current: str, wanted: str | None) -> str | None:
    """The status the message may set: only a step forward on the delivery path that the state machine allows."""
    if wanted is None or current not in FORWARD or wanted not in FORWARD or FORWARD[wanted] <= FORWARD[current]:
        return None
    return wanted if wanted in TRANSITIONS.get(current, ()) else None


def handle(context: TenantContext, device_id: str | None, event: ShareEvent) -> dict[str, Any]:
    from .api import _after_shipment as after_shipment
    from .api import add_shipment

    now = utc_now()
    moment = S.not_future(event.occurred_at or now, "Time")
    if event.action == "test":
        sample = P.parse("SYNTHETIC. Your order 123-TEST has shipped with Aramex. Tracking number 1234567890.", today=now.date())
        return {"status": "test_ok", "message": "ANUM is connected. Shared order and delivery messages are saved under Purchases.",
                "reader_ok": bool(sample.order_ref and sample.tracking_number)}
    text = (event.text or "").strip()
    if not text:
        raise S.PurchaseError(422, "Nothing was shared. Share the order or delivery message to the Shortcut.", "empty_text")
    found = P.parse(text, today=moment.date())
    if not found.recognised:
        raise S.PurchaseError(422, "No order number, tracking number or known shop was found in the shared text. Add the purchase by hand.",
                              "nothing_found")
    seed = event.idempotency_key or P.clean_text(text)
    key = S.record_key(context, "share", seed)
    notes = []
    if found.mentions_cancellation:
        notes.append("The message mentions a cancellation: check it and cancel the order yourself if it is right.")
    if found.mentions_refund:
        notes.append("The message mentions a refund: link the refund from Finance on the return when it arrives.")
    marker = "shr_" + key[:40]  # the share event of an update path: the same message twice changes nothing more
    with open_purchase_store(context) as store:
        replay = store.purchase_by_idem(context, key)
        seen = store.share_seen(context, marker) if replay is None else None
        if replay is not None or seen is not None:
            return {"status": "replayed", "purchase_id": replay.id if replay is not None else seen, "message": "Already saved."}
        purchase = _match(store, context, found)
        if purchase is None:
            purchase = S.new_purchase(context, {"merchant": found.merchant or "Shared order", "order_ref": found.order_ref, "ordered_on": moment.date(),
                                                "currency": found.currency, "total": found.amount,
                                                "expected_delivery_on": found.expected_on if found.expected_on and found.expected_on >= moment.date() else None,
                                                "ownership": event.ownership},
                                      now=now, idem_key=key, currency=S.preferences(store, context).default_currency, source="iphone", needs_review=True)
            try:
                store.add_purchase(purchase)
            except DuplicateError:
                return {"status": "replayed", "message": "Already saved."}
            S.add_event(store, context, purchase, "share", to_status="ordered", note="Saved from a shared message", source="iphone", now=now)
            created = True
        else:
            purchase = S.get_purchase(store, context, purchase.id, lock=True)
            created = False
            if (found.expected_on is not None and purchase.status in AWAITING_DELIVERY and purchase.expected_delivery_on != found.expected_on
                    and found.expected_on >= purchase.ordered_on):
                purchase = S.apply_changes(purchase, {"expected_delivery_on": found.expected_on}, now)
                if store.save_purchase(purchase, purchase.version):
                    purchase.version += 1
                    S.add_event(store, context, purchase, "share", note=f"Expected delivery {found.expected_on.isoformat()}", source="iphone", now=now)
        ship_status = SHIPMENT_STATUS.get(found.status or "")
        if found.tracking_number or found.carrier:
            existing = store.shipments(context, [purchase.id])
            same = next((s for s in existing if found.tracking_number and (s.tracking_number or "").upper() == found.tracking_number.upper()), None)
            carriers = {(s.carrier or "").casefold() for s in existing}
            if same is not None:
                # The message is about one shipment: that shipment moves on, and the purchase follows all of its shipments.
                if ship_status is not None and SHIPMENT_RANK.get(ship_status, 0) > SHIPMENT_RANK.get(same.status, 0):
                    moved = same.model_copy(update={"status": ship_status, "updated_at": now,
                                                    "delivered_on": moment.date() if ship_status == "delivered" else same.delivered_on})
                    if store.save_shipment(moved, same.version):
                        S.add_event(store, context, purchase, "shipment", subject_id=same.id, note=f"Shipment {ship_status.replace('_', ' ')}",
                                    source="iphone", now=now)
                        after_shipment(store, context, purchase, moved, "iphone")
            elif found.tracking_number or (found.carrier or "").casefold() not in carriers:
                # A tracking number is added once; a message naming only the carrier adds a shipment only when there is none from it.
                add_shipment(store, context, purchase, {"carrier": found.carrier, "tracking_number": found.tracking_number,
                                                        "status": ship_status or "in_transit", "expected_on": found.expected_on}, source="iphone")
            purchase = store.get_purchase(context, purchase.id) or purchase
        # With shipments, delivery progress comes from all of them (above); the message alone may only confirm the order.
        has_shipments = bool(store.shipments(context, [purchase.id]))
        target = _forward(purchase.status, found.status) if not has_shipments or found.status in ("ordered", "confirmed") else None
        if target is not None:
            purchase = S.move(store, context, purchase, target, expected_version=None, note="From a shared message", occurred_at=moment, source="iphone")
        for note in notes:
            S.add_event(store, context, purchase, "share", note=note, source="iphone", now=now)
        if not created:
            S.add_event(store, context, purchase, "share", note="Updated from a shared message", subject_id=marker, source="iphone", now=now)
        status = purchase.status
    label = purchase.merchant + (f" {purchase.order_ref}" if purchase.order_ref else "")
    message = (f"Saved {label} under Purchases. Check it in ANUM." if created else f"Updated {label}: {status.replace('_', ' ')}.")
    if notes:
        message += " " + " ".join(notes)
    return {"status": "created" if created else "updated", "purchase_id": purchase.id, "purchase_status": status, "message": message}


@router.post(INGEST_PATH, response_model=None)
def share(event: ShareEvent, principal: DevicePrincipal = Depends(device_principal)) -> Any:
    from ..finance.devices import report_failure

    context = principal.context
    if not has_permission(context, Permission.PURCHASE_WRITE):
        report_failure(context, principal.device_id, SCOPE, "not_allowed")
        return JSONResponse(status_code=403, content={"detail": "This person can no longer add purchases (their role changed).", "code": "forbidden"})
    try:
        return handle(context, principal.device_id, event)
    except S.PurchaseError as exc:
        if principal.via == "device_token":
            report_failure(context, principal.device_id, SCOPE, exc.code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})


def self_check(context: TenantContext) -> list[tuple[str, bool]]:
    """The synthetic check on Settings, Integrations, iPhone: records nothing."""
    sample = P.parse("SYNTHETIC. Your order 123-TEST has shipped with Aramex. Tracking number 1234567890.", today=utc_now().date())
    return [("reader", bool(sample.order_ref and sample.tracking_number and sample.status == "shipped")),
            ("permission", has_permission(context, Permission.PURCHASE_WRITE))]


def register() -> None:
    from ..finance.devices import Integration, integration, register_integration

    if integration(INTEGRATION) is None:
        register_integration(Integration(key=INTEGRATION, scope=SCOPE, permission=Permission.PURCHASE_WRITE, ingest_path=INGEST_PATH,
                                         per_minute=10, per_day=200, default_on=False, self_check=self_check, order=45,
                                         body_fields=("action", "text", "occurred_at", "idempotency_key", "ownership")))


__all__ = ["INGEST_PATH", "SCOPE", "ShareEvent", "device_principal", "handle", "register", "register_device_authority", "router"]
