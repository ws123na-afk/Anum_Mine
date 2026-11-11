"""HTTP routes under ``/api/v1/purchases`` (docs/purchases.md#api).

Who can do what (checked here, on the server, for every route; the database repeats the visibility rule):

* ``purchase:read`` (owners and members): business purchases of the workspace and their own personal ones.
* ``purchase:write`` (owners and members): add purchases, follow their delivery, add shipments and returns.
  A business purchase is edited or deleted by the person who added it or a workspace owner (``purchase:manage``);
  a shipment or return by its author, the purchase's owner or a workspace owner.
* Linking a payment or a refund also needs ``finance:manage`` and is done by the purchase's owner only: the
  money is their own Finance transaction.
* Viewers have no Purchases. A personal purchase of someone else answers 404, the same as one that does not exist.

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
from pydantic import BaseModel, ConfigDict, Field

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import service as S
from .models import (
    AWAITING_DELIVERY,
    OPEN_RETURN,
    REFUND_WAITING,
    RETURN_TO_PURCHASE,
    RETURN_TRANSITIONS,
    STATUSES,
    Item,
    Ownership,
    PaymentMethod,
    Purchase,
    Return,
    ReturnReason,
    ReturnStatus,
    Shipment,
    ShipmentStatus,
    Status,
)
from .store import MAX_RETURNS, MAX_SHIPMENTS, DuplicateError, ListFilters, PurchaseStore, open_purchase_store

router = APIRouter(prefix="/api/v1/purchases", tags=["purchases"])

DENIED = "Purchases are for workspace owners and members. Ask a workspace owner to change your role from viewer to member."
FINANCE_DENIED = "Linking a payment or a refund needs Finance access in this workspace (owners and members)."
MAX_PAGE = 200
Money = Decimal
ID = r"^[A-Za-z0-9_.:-]+$"


def refusals(route: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except S.PurchaseError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})

    return wrapper


def _need(context: TenantContext, permission: Permission, message: str = DENIED) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise S.PurchaseError(403, message, "forbidden") from None
        raise


def _read(context: TenantContext) -> None:
    _need(context, Permission.PURCHASE_READ)


def _write(context: TenantContext) -> None:
    _need(context, Permission.PURCHASE_WRITE)


def _finance_manage(context: TenantContext) -> None:
    _write(context)
    if not has_permission(context, Permission.FINANCE_MANAGE):
        raise S.PurchaseError(403, FINANCE_DENIED, "finance_forbidden")


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    """Ids and codes only: never a shop, an order number, an address or an amount."""
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=target, outcome="success", correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    ))


def _page(items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# -- views -------------------------------------------------------------------------------------------

HIDDEN = {"tenant_id", "workspace_id", "idem_key"}


def _redact_ref(data: dict[str, Any] | None, paid_by: str | None, context: TenantContext) -> None:
    """Another member's payment or refund shows its amount only, never their ledger or link ids."""
    if data is not None and paid_by is not None and paid_by != context.user_id:
        data["canonical_id"] = None
        data["link_id"] = None
        data["original_canonical_id"] = None


def purchase_view(context: TenantContext, purchase: Purchase, *, due: list[dict[str, Any]] | None = None, items: list[Item] | None = None,
                  refunded: list[dict[str, str]] | None = None) -> dict[str, Any]:
    data = purchase.model_dump(mode="json", exclude=HIDDEN)
    data["can_edit"] = S.can_edit_purchase(context, purchase)
    data["mine"] = purchase.owner_user_id == context.user_id
    data["can_pay"] = data["mine"] and has_permission(context, Permission.FINANCE_MANAGE)
    data["allowed_moves"] = S.allowed_moves(purchase.status)
    data["due"] = due or []
    data["refunded"] = refunded or []
    if items is not None:
        data["items_total"] = S.money(total) if (total := S.items_total(items)) is not None else None
    if purchase.payment is not None:
        _redact_ref(data["payment"], purchase.payment.paid_by, context)
    return data


def item_view(item: Item) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude=HIDDEN)


def shipment_view(context: TenantContext, purchase: Purchase, shipment: Shipment) -> dict[str, Any]:
    data = shipment.model_dump(mode="json", exclude=HIDDEN)
    data["can_edit"] = S.can_edit_row(context, purchase, shipment)
    return data


def return_view(context: TenantContext, purchase: Purchase | None, item: Return) -> dict[str, Any]:
    data = item.model_dump(mode="json", exclude=HIDDEN)
    data["can_edit"] = purchase is not None and S.can_edit_row(context, purchase, item)
    data["can_refund"] = purchase is not None and purchase.owner_user_id == context.user_id and has_permission(context, Permission.FINANCE_MANAGE)
    data["refunded"] = S.money(S.refunded_total(item, item.currency))
    # Every currency the refunds came in, never added together (a refund in the card's currency is not the return's).
    data["refunded_all"] = _refunded_by_currency([item])
    data["allowed_moves"] = [s for s in RETURN_TRANSITIONS.get(item.status, ()) if s not in ("partially_refunded", "refunded")]
    if purchase is not None:
        data["merchant"] = purchase.merchant
        data["order_ref"] = purchase.order_ref
    for ref, original in zip(data["refunds"], item.refunds, strict=True):
        _redact_ref(ref, original.paid_by, context)
    return data


def event_view(event: Any) -> dict[str, Any]:
    return event.model_dump(mode="json", exclude=HIDDEN | {"visibility", "purchase_owner_id"})


def _refunded_by_currency(returns: list[Return]) -> list[dict[str, str]]:
    totals: dict[str, Decimal] = {}
    for item in returns:
        for ref in item.refunds:
            if ref.status == "active":
                totals[ref.currency] = totals.get(ref.currency, Decimal(0)) + ref.amount
    return [{"currency": c, "amount": S.money(v)} for c, v in sorted(totals.items())]


def _due_for(store: PurchaseStore, context: TenantContext, purchases: list[Purchase]) -> dict[str, list[dict[str, Any]]]:
    today = utc_now().date()
    prefs = S.preferences(store, context)
    ids = [p.id for p in purchases]
    returns = store.returns(context, ids)
    items = store.items(context, ids)
    return {p.id: S.due_items(p, [r for r in returns if r.purchase_id == p.id], [i for i in items if i.purchase_id == p.id], today=today,
                              lead_days=prefs.lead_days, warranty_days=prefs.warranty_days) for p in purchases}


# -- inputs --------------------------------------------------------------------------------------------

Amount = Field(default=None, ge=0, le=Decimal("999999999999"), decimal_places=2)


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=80, pattern=ID)
    description: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=60)
    quantity: Decimal = Field(default=Decimal(1), gt=0, lt=1_000_000, decimal_places=3)
    unit_price: Decimal | None = Amount
    warranty_until: date | None = None


class PurchaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownership: Literal["personal", "business"] = "personal"
    merchant: str = Field(min_length=1, max_length=120)
    order_ref: str | None = Field(default=None, max_length=80)
    ordered_on: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_amount: Decimal | None = Amount
    shipping_amount: Decimal | None = Amount
    total: Decimal | None = Amount
    payment_method: PaymentMethod | None = None
    document_id: str | None = Field(default=None, max_length=80, pattern=ID)
    expected_delivery_on: date | None = None
    address_ref: str | None = Field(default=None, max_length=80)
    warranty_until: date | None = None
    return_window_until: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal["ordered", "confirmed", "shipped", "out_for_delivery", "delivered"] = "ordered"
    items: list[ItemInput] = Field(default_factory=list, max_length=200)


class PurchasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    merchant: str | None = Field(default=None, min_length=1, max_length=120)
    order_ref: str | None = Field(default=None, max_length=80)
    ordered_on: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_amount: Decimal | None = Amount
    shipping_amount: Decimal | None = Amount
    total: Decimal | None = Amount
    payment_method: PaymentMethod | None = None
    document_id: str | None = Field(default=None, max_length=80, pattern=ID)
    expected_delivery_on: date | None = None
    delivered_on: date | None = None
    address_ref: str | None = Field(default=None, max_length=80)
    warranty_until: date | None = None
    return_window_until: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    needs_review: bool | None = None
    items: list[ItemInput] | None = Field(default=None, max_length=200)


REQUIRED = ("merchant", "ordered_on", "currency", "needs_review")


def _no_nulls(changes: dict[str, Any], required: tuple[str, ...]) -> None:
    empty = sorted(key for key in required if key in changes and changes[key] is None)
    if empty:
        raise S.PurchaseError(422, f"These fields cannot be empty: {', '.join(empty)}.", "required_field", {"fields": empty})


def _check_document(context: TenantContext, document_id: str | None) -> None:
    """A receipt, invoice or proof must be a vault document the caller can see."""
    if document_id is None:
        return
    from ..vault.service import get_visible
    from ..vault.store import open_vault_unit

    try:
        with open_vault_unit(context) as unit:
            get_visible(unit, context, document_id)
    except HTTPException:
        raise S.PurchaseError(422, "That document is not in your document vault, or you cannot open it.", "document_not_found") from None


def _note_document(context: TenantContext, document_id: str | None, purchase: Purchase) -> None:
    """Show the purchase on the document in the vault (when the caller may edit the document); never fails the purchase."""
    if document_id is None:
        return
    from ..vault import service as V
    from ..vault.store import open_vault_unit

    try:
        with open_vault_unit(context) as unit:
            record = V.get_visible(unit, context, document_id)
            if not V.can_edit(context, record) or any(l.target_type == "purchase" and l.target_id == purchase.id
                                                      for l in unit.store.links(context, document_id)):
                return
        V.add_link(context, document_id, "purchase", purchase.id, purchase.merchant)
    except HTTPException:
        return


# -- list, create, overview ------------------------------------------------------------------------------

StatusFilter = Literal["ordered", "confirmed", "shipped", "out_for_delivery", "delivered", "partially_delivered", "cancelled", "return_requested",
                       "return_approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "disputed",
                       "awaiting", "returns", "closed", "open"]


PaymentFilter = Literal["card", "apple_pay", "cash", "bank_transfer", "cash_on_delivery", "instalments", "gift_card", "other", "linked", "unlinked"]
IN_TRANSIT = frozenset({"in_transit", "out_for_delivery"})


def _row_extras(store: PurchaseStore, context: TenantContext, purchases: list[Purchase]) -> dict[str, dict[str, Any]]:
    """What a list row shows besides the purchase: its first item, how many items, and its newest shipment."""
    ids = [p.id for p in purchases]
    by_purchase: dict[str, dict[str, Any]] = {pid: {"first_item": None, "item_count": 0, "latest_shipment": None} for pid in ids}
    for item in sorted(store.items(context, ids), key=lambda i: (i.purchase_id, i.position)):
        row = by_purchase[item.purchase_id]
        row["item_count"] += 1
        if row["first_item"] is None:
            row["first_item"] = item.description
    owners = {p.id: p for p in purchases}
    for shipment in store.shipments(context, ids):  # oldest first, so the last one wins
        by_purchase[shipment.purchase_id]["latest_shipment"] = shipment_view(context, owners[shipment.purchase_id], shipment)
    return by_purchase


@router.get("", response_model=None)
@refusals
def list_purchases(q: str | None = Query(default=None, max_length=80), status_filter: StatusFilter | None = Query(default=None, alias="status"),
                   archived: bool = False, limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                   merchant: str | None = Query(default=None, max_length=120), ordered_from: date | None = Query(default=None, alias="from"),
                   ordered_to: date | None = Query(default=None, alias="to"), payment: PaymentFilter | None = None,
                   source: Literal["web", "iphone"] | None = None, warranty: bool = False,
                   context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    if ordered_from and ordered_to and ordered_from > ordered_to:
        raise S.PurchaseError(422, "The start date is after the end date.", "bad_range")
    filters = ListFilters(merchant=(merchant or "").strip() or None, ordered_from=ordered_from, ordered_to=ordered_to, payment=payment,
                          source=source, warranty_on=utc_now().date() if warranty else None)
    with open_purchase_store(context) as store:
        items, total = store.list_purchases(context, query=(q or "").strip() or None, status=status_filter, archived=archived, limit=limit,
                                            offset=offset, filters=filters)
        due = _due_for(store, context, items)
        returns = store.returns(context, [p.id for p in items])
        extras = _row_extras(store, context, items)
    return _page([{**purchase_view(context, p, due=due[p.id], refunded=_refunded_by_currency([r for r in returns if r.purchase_id == p.id])),
                   **extras[p.id]} for p in items], total, limit, offset)


@router.get("/merchants", response_model=None)
@refusals
def merchants(context: TenantContext = Depends(tenant_context)) -> Any:
    """The shops of the caller's visible purchases, for the Seller filter."""
    _read(context)
    with open_purchase_store(context) as store:
        return {"items": store.merchants(context)}


def create_purchase(context: TenantContext, body: PurchaseInput, idempotency_key: str | None) -> tuple[Purchase, list[Item], bool]:
    now = utc_now()
    key = S.record_key(context, "purchase", idempotency_key) if idempotency_key else S.record_key(context, "purchase", new_id("k"))
    _check_document(context, body.document_id)
    with open_purchase_store(context) as store:
        replay = store.purchase_by_idem(context, key)
        if replay is not None:
            return replay, store.items(context, [replay.id]), True
        purchase = S.new_purchase(context, body.model_dump(exclude={"items", "status"}), now=now, idem_key=key,
                                  currency=S.preferences(store, context).default_currency)
        try:
            store.add_purchase(purchase)
        except DuplicateError:
            raise S.PurchaseError(409, "This purchase was already saved.", "duplicate") from None
        items = S.save_items(store, context, purchase, [i.model_dump() for i in body.items], now)
        S.add_event(store, context, purchase, "status", to_status="ordered", note=None, now=now)
        if body.status != "ordered":
            purchase = S.move(store, context, purchase, body.status, expected_version=None, note=None, occurred_at=None)
    return purchase, items, False


@router.post("", status_code=201, response_model=None)
@refusals
def create(body: PurchaseInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _write(context)
    purchase, items, replayed = create_purchase(context, body, idempotency_key)
    if not replayed:
        _note_document(context, purchase.document_id, purchase)
        _audit(repository, context, "purchase.create", purchase.id, {"ownership": purchase.ownership.value})
    return JSONResponse(status_code=200 if replayed else 201,
                        content={**purchase_view(context, purchase, items=items), "items": [item_view(i) for i in items], "replayed": replayed})


def _money_rows(totals: dict[str, Decimal]) -> list[dict[str, str]]:
    return [{"currency": c, "amount": S.money(v)} for c, v in sorted(totals.items()) if v != 0]


@router.get("/overview", response_model=None)
@refusals
def overview(context: TenantContext = Depends(tenant_context)) -> Any:
    """Counts, spend and refunds per currency (never added across currencies), refunds still owed and what is due."""
    _read(context)
    today = utc_now().date()
    with open_purchase_store(context) as store:
        purchases = store.all_purchases(context)
        ids = [p.id for p in purchases]
        returns = store.returns(context, ids)
        due = _due_for(store, context, purchases)
        shipments = store.shipments(context, ids)
    awaiting_ids = {p.id for p in purchases if p.status in AWAITING_DELIVERY}
    month_start = today.replace(day=1)
    this_month = [p for p in purchases if p.ordered_on >= month_start and p.status != "cancelled"]
    committed: dict[str, Decimal] = {}
    for purchase in this_month:
        if purchase.total is not None:
            committed[purchase.currency] = committed.get(purchase.currency, Decimal(0)) + purchase.total
    spend: dict[str, Decimal] = {}
    refunded: dict[str, Decimal] = {}
    owed: dict[str, Decimal] = {}
    monthly: dict[tuple[str, str], Decimal] = {}
    start = (today.replace(day=1) - timedelta(days=335)).replace(day=1)
    for purchase in purchases:
        if purchase.payment is not None and purchase.payment.status == "active":
            spend[purchase.payment.currency] = spend.get(purchase.payment.currency, Decimal(0)) + purchase.payment.amount
            if purchase.ordered_on >= start:
                key = (purchase.ordered_on.strftime("%Y-%m"), purchase.payment.currency)
                monthly[key] = monthly.get(key, Decimal(0)) + purchase.payment.amount
    for item in returns:
        got = S.refunded_total(item, item.currency)
        for ref in item.refunds:
            if ref.status == "active":
                refunded[ref.currency] = refunded.get(ref.currency, Decimal(0)) + ref.amount
        if item.status in REFUND_WAITING and item.amount is not None and item.amount > got:
            owed[item.currency] = owed.get(item.currency, Decimal(0)) + item.amount - got
    net = {c: spend.get(c, Decimal(0)) - refunded.get(c, Decimal(0)) for c in set(spend) | set(refunded)}
    late = sum(1 for p in purchases if p.status in AWAITING_DELIVERY and p.expected_delivery_on is not None and p.expected_delivery_on < today)
    items = sorted((d for rows in due.values() for d in rows), key=lambda d: (d["days_left"], d["kind"]))
    return {
        "purchases": len(purchases),
        "awaiting_delivery": sum(1 for p in purchases if p.status in AWAITING_DELIVERY),
        "late": late,
        "open_returns": sum(1 for r in returns if r.status in OPEN_RETURN),
        "needs_review": sum(1 for p in purchases if p.needs_review),
        # This month (UTC): orders placed and what the shops said they cost (a detail, not the ledger), per currency.
        "month": month_start.strftime("%Y-%m"),
        "orders_this_month": len(this_month),
        "committed_this_month": _money_rows(committed),
        "untotalled_this_month": sum(1 for p in this_month if p.total is None),
        # Shipments on their way (in transit or out for delivery) of purchases still awaiting delivery.
        "in_transit": sum(1 for sh in shipments if sh.status in IN_TRANSIT and sh.purchase_id in awaiting_ids),
        "unlinked": sum(1 for p in purchases if p.owner_user_id == context.user_id and p.status != "cancelled"
                        and (p.payment is None or p.payment.status == "void")),
        "spend": _money_rows(spend), "refunded": _money_rows(refunded), "net": _money_rows(net), "refunds_owed": _money_rows(owed),
        "monthly": [{"month": m, "currency": c, "amount": S.money(v)} for (m, c), v in sorted(monthly.items())],
        "due": items[:50],
    }


@router.get("/reminders", response_model=None)
@refusals
def reminders(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_purchase_store(context) as store:
        purchases = store.all_purchases(context)
        due = _due_for(store, context, purchases)
        prefs = S.preferences(store, context)
    items = sorted((d for rows in due.values() for d in rows), key=lambda d: (d["days_left"], d["kind"]))
    return {"items": items, "lead_days": prefs.lead_days, "warranty_days": prefs.warranty_days}


class PreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    reminders: bool = True
    lead_days: int = Field(default=3, ge=1, le=60)
    warranty_days: int = Field(default=30, ge=1, le=120)
    default_currency: str = Field(default="SAR", min_length=3, max_length=3)


@router.get("/preferences", response_model=None)
@refusals
def get_preferences(context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_purchase_store(context) as store:
        return S.preferences(store, context).model_dump(mode="json", exclude={"tenant_id", "workspace_id"})


@router.put("/preferences", response_model=None)
@refusals
def put_preferences(body: PreferencesInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    values = body.model_dump(exclude={"version"})
    values["default_currency"] = S.currency_code(values["default_currency"], "SAR")
    with open_purchase_store(context) as store:
        current = S.preferences(store, context)
        updated = current.model_copy(update={**values, "updated_at": utc_now()})
        if not store.save_preferences(updated, body.version):
            raise S.PurchaseError(409, "Your purchase settings changed in another tab. Reload and try again.", "stale")
        saved = S.preferences(store, context)
    _audit(repository, context, "purchase.preferences", context.user_id, {"reminders": body.reminders})
    return saved.model_dump(mode="json", exclude={"tenant_id", "workspace_id"})


@router.get("/payments", response_model=None)
@refusals
def payments(kind: Literal["payment", "refund"] = "payment", q: str | None = Query(default=None, max_length=80), around: date | None = None,
             amount: Decimal | None = Query(default=None, gt=0, lt=Decimal("1000000000")), limit: int = Query(default=20, ge=1, le=50),
             context: TenantContext = Depends(tenant_context)) -> Any:
    """The caller's own Finance transactions to pick from (never another person's ledger)."""
    _read(context)
    _need(context, Permission.FINANCE_READ, FINANCE_DENIED)
    return {"items": S.payment_candidates(context, kind=kind, query=(q or "").strip() or None, around=around, amount=amount, limit=limit)}


@router.get("/returns", response_model=None)
@refusals
def list_returns(open_only: bool = Query(default=True, alias="open"), limit: int = Query(default=20, ge=1, le=MAX_PAGE),
                 offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_purchase_store(context) as store:
        rows, total = store.list_returns(context, open_only=open_only, limit=limit, offset=offset)
        purchases = {p.id: p for p in (store.get_purchase(context, pid) for pid in {r.purchase_id for r in rows}) if p is not None}
    return _page([return_view(context, purchases.get(r.purchase_id), r) for r in rows], total, limit, offset)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export.csv", response_model=None)
@refusals
def export_csv(archived: bool = False, context: TenantContext = Depends(tenant_context)) -> Any:
    """Every purchase the caller can see, newest first. Paid and refunded amounts only where a link is active."""
    _read(context)
    with open_purchase_store(context) as store:
        rows, _ = store.list_purchases(context, query=None, status=None, archived=archived, limit=5_000, offset=0)
        returns = store.returns(context, [p.id for p in rows])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ordered_on", "merchant", "order_ref", "status", "currency", "total", "paid_amount", "paid_currency", "payment_status",
                     "refunded", "expected_delivery_on", "delivered_on", "warranty_until", "address_ref"])
    for p in rows:
        pay = p.payment
        refunded = " ".join(f"{r['amount']} {r['currency']}" for r in _refunded_by_currency([r for r in returns if r.purchase_id == p.id]))
        writer.writerow([_cell(v) for v in (p.ordered_on.isoformat(), p.merchant, p.order_ref, p.status, p.currency, p.total,
                                            pay.amount if pay else None, pay.currency if pay else None, pay.status if pay else "not_linked", refunded,
                                            p.expected_delivery_on, p.delivered_on, p.warranty_until, p.address_ref)])
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="anum-purchases.csv"', "Cache-Control": "no-store"})


# -- returns and shipments by their own id (before /{purchase_id}) ---------------------------------------


class ShipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carrier: str | None = Field(default=None, max_length=60)
    tracking_number: str | None = Field(default=None, max_length=60)
    status: ShipmentStatus = "in_transit"
    shipped_on: date | None = None
    expected_on: date | None = None
    delivered_on: date | None = None
    note: str | None = Field(default=None, max_length=300)


class ShipmentPatch(ShipmentInput):
    version: int = Field(ge=1)
    status: ShipmentStatus | None = None  # type: ignore[assignment]


def _shipment_fields(data: dict[str, Any]) -> dict[str, Any]:
    if "carrier" in data:
        data["carrier"] = S.clean(data["carrier"], 60)
    if "tracking_number" in data:
        data["tracking_number"] = S.reference(data["tracking_number"], "Tracking number")
    if "note" in data:
        data["note"] = S.clean(data["note"], 300)
    for key in ("shipped_on", "delivered_on"):
        if key in data:
            S.not_future_day(data[key], "Shipment date")
    return data


def _after_shipment(store: PurchaseStore, context: TenantContext, purchase: Purchase, shipment: Shipment, source: str = "web") -> Purchase:
    """The purchase follows its shipments: shipped, out for delivery, partly delivered or delivered."""
    target = S.delivery_from_shipments(store.shipments(context, [purchase.id]))
    if target is None:
        return purchase
    note = " ".join(x for x in (shipment.carrier, shipment.tracking_number) if x) or None
    return S.follow(store, context, purchase, target, note=note, source=source, subject_id=shipment.id)


def add_shipment(store: PurchaseStore, context: TenantContext, purchase: Purchase, data: dict[str, Any], *, source: str = "web") -> Shipment:
    data = _shipment_fields(data)
    if not data.get("carrier") and not data.get("tracking_number"):
        raise S.PurchaseError(422, "Give the carrier or the tracking number.", "shipment_empty")
    if len(store.shipments(context, [purchase.id])) >= MAX_SHIPMENTS:
        raise S.PurchaseError(409, f"A purchase has at most {MAX_SHIPMENTS} shipments.", "too_many")
    now = utc_now()
    shipment = Shipment(id=new_id("purs"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                        purchase_id=purchase.id, visibility=purchase.ownership, purchase_owner_id=purchase.owner_user_id, created_at=now,
                        updated_at=now, **data)
    try:
        store.add_shipment(shipment)
    except DuplicateError:
        raise S.PurchaseError(409, "This tracking number is already on the purchase.", "duplicate_tracking") from None
    S.add_event(store, context, purchase, "shipment", subject_id=shipment.id, note=" ".join(x for x in (shipment.carrier, shipment.tracking_number) if x),
                source=source, now=now)
    _after_shipment(store, context, purchase, shipment, source)
    return shipment


@router.patch("/shipments/{shipment_id}", response_model=None)
@refusals
def update_shipment(shipment_id: str, body: ShipmentPatch, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    _no_nulls(changes, ("status",))
    with open_purchase_store(context) as store:
        shipment = store.get_shipment(context, shipment_id) if S.SAFE_ID.match(shipment_id) else None
        if shipment is None:
            raise S.not_found("Shipment")
        purchase = S.get_purchase(store, context, shipment.purchase_id, lock=True)
        S.require_row_edit(context, purchase, shipment)
        updated = shipment.model_copy(update={**_shipment_fields(changes), "updated_at": utc_now()})
        if not updated.carrier and not updated.tracking_number:
            raise S.PurchaseError(422, "Give the carrier or the tracking number.", "shipment_empty")
        try:
            saved = store.save_shipment(updated, body.version)
        except DuplicateError:
            raise S.PurchaseError(409, "This tracking number is already on the purchase.", "duplicate_tracking") from None
        if not saved:
            raise S.PurchaseError(409, "This shipment changed since you opened it. Reload and try again.", "stale")
        if "status" in changes and changes["status"] != shipment.status:
            S.add_event(store, context, purchase, "shipment", subject_id=shipment.id, note=f"Shipment {changes['status'].replace('_', ' ')}")
            _after_shipment(store, context, purchase, updated)
        result = store.get_shipment(context, shipment_id)
    _audit(repository, context, "purchase.shipment.update", shipment_id, {"fields": sorted(changes)})
    return shipment_view(context, purchase, result)  # type: ignore[arg-type]


@router.delete("/shipments/{shipment_id}", status_code=204, response_class=Response, response_model=None)
@refusals
def delete_shipment(shipment_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        shipment = store.get_shipment(context, shipment_id) if S.SAFE_ID.match(shipment_id) else None
        if shipment is None:
            raise S.not_found("Shipment")
        purchase = S.get_purchase(store, context, shipment.purchase_id)
        S.require_row_edit(context, purchase, shipment)
        store.delete_shipment(context, shipment_id)
        S.add_event(store, context, purchase, "shipment", subject_id=shipment_id, note="Shipment removed")
    _audit(repository, context, "purchase.shipment.delete", shipment_id)
    return Response(status_code=204)


class ReturnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_on: date | None = None
    reason: ReturnReason
    reason_note: str | None = Field(default=None, max_length=300)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999999"), decimal_places=2)
    deadline_on: date | None = None
    pickup_on: date | None = None
    courier: str | None = Field(default=None, max_length=60)
    tracking_number: str | None = Field(default=None, max_length=60)
    proof_document_id: str | None = Field(default=None, max_length=80, pattern=ID)
    confirmation_ref: str | None = Field(default=None, max_length=80)
    expected_refund_on: date | None = None


class ReturnPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: ReturnReason | None = None
    reason_note: str | None = Field(default=None, max_length=300)
    items: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999999"), decimal_places=2)
    deadline_on: date | None = None
    pickup_on: date | None = None
    courier: str | None = Field(default=None, max_length=60)
    tracking_number: str | None = Field(default=None, max_length=60)
    proof_document_id: str | None = Field(default=None, max_length=80, pattern=ID)
    confirmation_ref: str | None = Field(default=None, max_length=80)
    refund_ref: str | None = Field(default=None, max_length=80)
    expected_refund_on: date | None = None
    dispute_note: str | None = Field(default=None, max_length=500)


def _return_fields(data: dict[str, Any]) -> dict[str, Any]:
    for key, limit in (("reason_note", 300), ("courier", 60), ("dispute_note", 500)):
        if key in data:
            data[key] = S.clean(data[key], limit)
    for key, label in (("tracking_number", "Tracking number"), ("confirmation_ref", "Confirmation number"), ("refund_ref", "Refund reference")):
        if key in data:
            data[key] = S.reference(data[key], label)
    if "requested_on" in data:
        S.not_future_day(data["requested_on"], "Request date")
    return data


def _lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"item_id", "quantity"}:
            raise S.PurchaseError(422, "Each returned item needs item_id and quantity.", "invalid_item")
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not S.SAFE_ID.match(item_id):
            raise S.PurchaseError(422, "A returned item is not on this purchase.", "invalid_item")
        try:
            quantity = Decimal(str(row.get("quantity", 1)))
        except ArithmeticError:
            raise S.PurchaseError(422, "The quantity is not a number.", "invalid_quantity") from None
        out.append({"item_id": item_id, "quantity": quantity})
    return out


@router.patch("/returns/{return_id}", response_model=None)
@refusals
def update_return(return_id: str, body: ReturnPatch, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    _no_nulls(changes, ("reason",))
    _check_document(context, changes.get("proof_document_id"))
    with open_purchase_store(context) as store:
        item = store.get_return(context, return_id) if S.SAFE_ID.match(return_id) else None
        if item is None:
            raise S.not_found("Return")
        purchase = S.get_purchase(store, context, item.purchase_id, lock=True)
        S.require_row_edit(context, purchase, item)
        data = _return_fields(changes)
        if "items" in data:
            others = [r for r in store.returns(context, [purchase.id]) if r.id != item.id]
            data["items"] = S.return_lines(store.items(context, [purchase.id]), others, _lines(data["items"] or []))
        updated = item.model_copy(update={**data, "updated_at": utc_now()})
        if not store.save_return(updated, body.version):
            raise S.PurchaseError(409, "This return changed since you opened it. Reload and try again.", "stale")
        S.add_event(store, context, purchase, "return", subject_id=item.id, note="Return details changed")
        S.settle_refunds(store, context, store.get_purchase(context, purchase.id) or purchase)
        result = store.get_return(context, return_id)
        fresh = store.get_purchase(context, purchase.id)
    _audit(repository, context, "purchase.return.update", return_id, {"fields": sorted(changes)})
    return return_view(context, fresh, result)  # type: ignore[arg-type]


class MoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=1, max_length=20)
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=300)
    occurred_at: datetime | None = None


@router.post("/returns/{return_id}/status", response_model=None)
@refusals
def move_return(return_id: str, body: MoveInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        item = store.get_return(context, return_id) if S.SAFE_ID.match(return_id) else None
        if item is None:
            raise S.not_found("Return")
        purchase = S.get_purchase(store, context, item.purchase_id, lock=True)
        S.require_row_edit(context, purchase, item)
        before = item.status
        S.move_return(item, body.to)
        if before in ("rejected", "cancelled") and body.to not in ("rejected", "cancelled") and item.items:
            # Its items stopped counting while it was rejected: another return may have taken them since.
            others = [r for r in store.returns(context, [purchase.id]) if r.id != item.id]
            S.return_lines(store.items(context, [purchase.id]), others, [line.model_dump() for line in item.items])
        item.updated_at = utc_now()
        if body.to == "disputed" and body.note:
            item.dispute_note = S.clean(body.note, 500)
        if not store.save_return(item, body.version):
            raise S.PurchaseError(409, "This return changed since you opened it. Reload and try again.", "stale")
        S.add_event(store, context, purchase, "return", subject_id=item.id, note=body.note or f"Return {before.replace('_', ' ')} to {body.to.replace('_', ' ')}")
        target = RETURN_TO_PURCHASE.get(body.to)
        if target is not None:
            S.follow(store, context, purchase, target, note=body.note, subject_id=item.id)
        if body.to in ("rejected", "cancelled") and purchase.status in ("return_requested", "return_approved", "pickup_scheduled") and not any(
                r.status in OPEN_RETURN for r in store.returns(context, [purchase.id]) if r.id != item.id):
            S.follow(store, context, purchase, "delivered", note=body.note, subject_id=item.id)
        result = store.get_return(context, return_id)
        fresh = store.get_purchase(context, purchase.id)
    _audit(repository, context, "purchase.return.status", return_id, {"from": before, "to": body.to})
    return return_view(context, fresh, result)  # type: ignore[arg-type]


@router.delete("/returns/{return_id}", status_code=204, response_class=Response, response_model=None)
@refusals
def delete_return(return_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        item = store.get_return(context, return_id) if S.SAFE_ID.match(return_id) else None
        if item is None:
            raise S.not_found("Return")
        purchase = S.get_purchase(store, context, item.purchase_id)
        S.require_row_edit(context, purchase, item)
        if any(r.status != "void" for r in item.refunds):
            raise S.PurchaseError(409, "This return has refunds linked from Finance. Remove those links first, or mark the return cancelled.",
                                  "has_refunds")
        store.delete_return(context, return_id)
        S.add_event(store, context, purchase, "return", subject_id=return_id, note="Return removed")
    _audit(repository, context, "purchase.return.delete", return_id)
    return Response(status_code=204)


# -- refunds -------------------------------------------------------------------------------------------


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=80, pattern=ID)
    amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1000000000"), decimal_places=4)
    currency: str | None = Field(default=None, min_length=3, max_length=3, description="Needed when the transaction has more than one currency")


class CashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    amount: Decimal = Field(gt=0, lt=Decimal("1000000000"), decimal_places=2)
    posted_on: date


@router.post("/returns/{return_id}/refunds", response_model=None)
@refusals
def link_refund(return_id: str, body: LinkInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    refund, replayed = S.link_refund(context, return_id, canonical_id=body.canonical_id, amount=body.amount, currency=body.currency)
    if not replayed:
        _audit(repository, context, "purchase.refund.link", return_id, {"link_id": refund.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"refund": refund.model_dump(mode="json"), "replayed": replayed})


@router.post("/returns/{return_id}/refunds/cash", response_model=None)
@refusals
def cash_refund(return_id: str, body: CashInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _finance_manage(context)
    refund, replayed = S.record_cash_refund(context, return_id, account_id=body.account_id, amount=body.amount, posted_on=body.posted_on,
                                            idempotency_key=idempotency_key)
    if not replayed:
        _audit(repository, context, "purchase.refund.cash", return_id, {"link_id": refund.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"refund": refund.model_dump(mode="json"), "replayed": replayed})


@router.delete("/returns/{return_id}/refunds/{link_id}", status_code=204, response_class=Response, response_model=None)
@refusals
def unlink_refund(return_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    S.unlink_refund(context, return_id, link_id)
    _audit(repository, context, "purchase.refund.unlink", return_id, {"link_id": link_id})
    return Response(status_code=204)


# -- one purchase ------------------------------------------------------------------------------------------


def detail(store: PurchaseStore, context: TenantContext, purchase: Purchase) -> dict[str, Any]:
    items = store.items(context, [purchase.id])
    returns = store.returns(context, [purchase.id])
    shipments = store.shipments(context, [purchase.id])
    prefs = S.preferences(store, context)
    due = S.due_items(purchase, returns, items, today=utc_now().date(), lead_days=prefs.lead_days, warranty_days=prefs.warranty_days)
    events, events_total = store.events(context, purchase.id, limit=20, offset=0)
    return {**purchase_view(context, purchase, due=due, items=items, refunded=_refunded_by_currency(returns)),
            "items": [item_view(i) for i in items], "shipments": [shipment_view(context, purchase, s) for s in shipments],
            "returns": [return_view(context, purchase, r) for r in returns], "events": [event_view(e) for e in events], "events_total": events_total}


@router.get("/{purchase_id}", response_model=None)
@refusals
def get_one(purchase_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_purchase_store(context) as store:
        return detail(store, context, S.get_purchase(store, context, purchase_id))


@router.patch("/{purchase_id}", response_model=None)
@refusals
def update(purchase_id: str, body: PurchasePatch, context: TenantContext = Depends(tenant_context),
           repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    changes = body.model_dump(exclude_unset=True, exclude={"version", "items"})
    _no_nulls(changes, REQUIRED)
    if "document_id" in changes:
        _check_document(context, changes["document_id"])
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id, lock=True)
        S.require_purchase_edit(context, purchase)
        updated = S.apply_changes(purchase, changes, utc_now())
        if not store.save_purchase(updated, body.version):
            raise S.PurchaseError(409, "This purchase changed since you opened it. Reload and try again.", "stale")
        if body.items is not None:
            S.save_items(store, context, updated, [i.model_dump() for i in body.items], utc_now())
        fields = sorted(changes) + (["items"] if body.items is not None else [])
        S.add_event(store, context, updated, "note", note="Details changed: " + ", ".join(f.replace("_", " ") for f in fields) if fields else "Details changed")
        result = detail(store, context, S.get_purchase(store, context, purchase_id))
    if changes.get("document_id"):
        _note_document(context, changes["document_id"], updated)
    _audit(repository, context, "purchase.update", purchase_id, {"fields": fields})
    return result


def attach_document(context: TenantContext, purchase_id: str, document_id: str) -> dict[str, Any]:
    """Set a purchase's receipt to a vault document, for a confirmed receipt match (docs/documents.md#receipt-matches).
    The same rules as ``PATCH`` with ``document_id``: purchases write, the purchase's edit right, a document the caller
    can open; a purchase that has a receipt already is refused (``409 has_receipt``). Raises ``PurchaseError``."""
    _write(context)
    _check_document(context, document_id)
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id, lock=True)
        S.require_purchase_edit(context, purchase)
        if purchase.document_id:
            raise S.PurchaseError(409, "This purchase has a receipt already. Change it on the purchase if this one is right.", "has_receipt")
        updated = S.apply_changes(purchase, {"document_id": document_id}, utc_now())
        if not store.save_purchase(updated, purchase.version):
            raise S.PurchaseError(409, "This purchase changed since you opened it. Reload and try again.", "stale")
        S.add_event(store, context, updated, "note", note="Receipt attached from the document vault")
        result = detail(store, context, S.get_purchase(store, context, purchase_id))
    _note_document(context, document_id, updated)
    return result


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    archived: bool = True


@router.post("/{purchase_id}/archive", response_model=None)
@refusals
def archive(purchase_id: str, body: ArchiveInput, context: TenantContext = Depends(tenant_context),
            repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id)
        S.require_purchase_edit(context, purchase)
        purchase.archived_at = utc_now() if body.archived else None
        purchase.updated_at = utc_now()
        if not store.save_purchase(purchase, body.version):
            raise S.PurchaseError(409, "This purchase changed since you opened it. Reload and try again.", "stale")
        S.add_event(store, context, purchase, "note", note="Archived" if body.archived else "Restored")
        result = detail(store, context, S.get_purchase(store, context, purchase_id))
    _audit(repository, context, "purchase.archive" if body.archived else "purchase.restore", purchase_id)
    return result


@router.delete("/{purchase_id}", status_code=204, response_class=Response, response_model=None)
@refusals
def delete(purchase_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id)
        S.require_purchase_edit(context, purchase)
        returns = store.returns(context, [purchase_id])
        if (purchase.payment is not None and purchase.payment.status != "void") or any(r.status != "void" for x in returns for r in x.refunds):
            raise S.PurchaseError(409, "This purchase has a payment or refunds linked from Finance. Remove those links, or archive it to keep its history.",
                                  "has_payments")
        store.delete_purchase(context, purchase_id)
    _audit(repository, context, "purchase.delete", purchase_id)
    return Response(status_code=204)


@router.post("/{purchase_id}/status", response_model=None)
@refusals
def move_status(purchase_id: str, body: MoveInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    if body.to not in STATUSES:
        raise S.PurchaseError(422, "That status does not exist.", "invalid_status")
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id, lock=True)
        if purchase.archived_at is not None:
            raise S.PurchaseError(409, "This purchase is archived. Restore it first.", "archived")
        before = purchase.status
        S.move(store, context, purchase, body.to, expected_version=body.version, note=body.note, occurred_at=body.occurred_at)
        result = detail(store, context, S.get_purchase(store, context, purchase_id))
    _audit(repository, context, "purchase.status", purchase_id, {"from": before, "to": body.to})
    return result


@router.get("/{purchase_id}/events", response_model=None)
@refusals
def events(purchase_id: str, limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
           context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_purchase_store(context) as store:
        S.get_purchase(store, context, purchase_id)
        rows, total = store.events(context, purchase_id, limit=limit, offset=offset)
    return _page([event_view(e) for e in rows], total, limit, offset)


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=300)


@router.post("/{purchase_id}/notes", status_code=201, response_model=None)
@refusals
def add_note(purchase_id: str, body: NoteInput, context: TenantContext = Depends(tenant_context),
             repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id)
        if not S.clean(body.note, 300):
            raise S.PurchaseError(422, "Write the note.", "note_required")
        event = S.add_event(store, context, purchase, "note", note=body.note)
    _audit(repository, context, "purchase.note", purchase_id)
    return JSONResponse(status_code=201, content=event_view(event))


@router.post("/{purchase_id}/shipments", status_code=201, response_model=None)
@refusals
def create_shipment(purchase_id: str, body: ShipmentInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id, lock=True)
        if purchase.archived_at is not None:
            raise S.PurchaseError(409, "This purchase is archived. Restore it first.", "archived")
        shipment = add_shipment(store, context, purchase, body.model_dump())
    _audit(repository, context, "purchase.shipment.create", shipment.id, {"purchase_id": purchase_id})
    return JSONResponse(status_code=201, content=shipment_view(context, purchase, shipment))


@router.post("/{purchase_id}/returns", status_code=201, response_model=None)
@refusals
def create_return(purchase_id: str, body: ReturnInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _write(context)
    _check_document(context, body.proof_document_id)
    now = utc_now()
    key = S.record_key(context, "return", purchase_id, idempotency_key) if idempotency_key else S.record_key(context, "return", new_id("k"))
    with open_purchase_store(context) as store:
        purchase = S.get_purchase(store, context, purchase_id, lock=True)
        replay = store.return_by_idem(context, key)
        if replay is not None:
            return JSONResponse(status_code=200, content={**return_view(context, purchase, replay), "replayed": True})
        if purchase.archived_at is not None:
            raise S.PurchaseError(409, "This purchase is archived. Restore it first.", "archived")
        if purchase.status in ("ordered", "confirmed", "shipped", "out_for_delivery"):
            raise S.PurchaseError(409, "Nothing has been delivered yet. Cancel the order instead, or mark it delivered first.", "not_delivered")
        existing = store.returns(context, [purchase.id])
        if len(existing) >= MAX_RETURNS:
            raise S.PurchaseError(409, f"A purchase has at most {MAX_RETURNS} returns.", "too_many")
        data = _return_fields(body.model_dump(exclude={"items"}))
        items = store.items(context, [purchase.id])
        lines = S.return_lines(items, existing, _lines(body.items))
        amount = data.pop("amount") or S.lines_amount(items, lines) or (purchase.total if not lines else None)
        item = Return(id=new_id("purr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                      purchase_id=purchase.id, visibility=purchase.ownership, purchase_owner_id=purchase.owner_user_id,
                      requested_on=data.pop("requested_on") or now.date(), deadline_on=data.pop("deadline_on") or purchase.return_window_until,
                      # The amount is inferred from the purchase's own figures or typed in its currency: never relabelled.
                      items=lines, amount=amount, currency=purchase.currency,
                      idem_key=key, created_at=now, updated_at=now, **data)
        try:
            store.add_return(item)
        except DuplicateError:
            raise S.PurchaseError(409, "This return was already saved.", "duplicate") from None
        S.add_event(store, context, purchase, "return", subject_id=item.id, note=f"Return requested: {body.reason.replace('_', ' ')}", now=now)
        fresh = S.follow(store, context, purchase, "return_requested", note=None, subject_id=item.id)
    _audit(repository, context, "purchase.return.create", item.id, {"purchase_id": purchase_id})
    return JSONResponse(status_code=201, content={**return_view(context, fresh, item), "replayed": False})


@router.post("/{purchase_id}/payment", response_model=None)
@refusals
def link_payment(purchase_id: str, body: LinkInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    payment, replayed = S.link_payment(context, purchase_id, canonical_id=body.canonical_id, amount=body.amount, currency=body.currency)
    if not replayed:
        _audit(repository, context, "purchase.payment.link", purchase_id, {"link_id": payment.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"payment": payment.model_dump(mode="json"), "replayed": replayed})


@router.post("/{purchase_id}/payment/cash", response_model=None)
@refusals
def record_cash(purchase_id: str, body: CashInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _finance_manage(context)
    payment, replayed = S.record_cash(context, purchase_id, account_id=body.account_id, amount=body.amount, posted_on=body.posted_on,
                                      idempotency_key=idempotency_key)
    if not replayed:
        _audit(repository, context, "purchase.payment.cash", purchase_id, {"link_id": payment.link_id})
    return JSONResponse(status_code=200 if replayed else 201, content={"payment": payment.model_dump(mode="json"), "replayed": replayed})


@router.delete("/{purchase_id}/payment", status_code=204, response_class=Response, response_model=None)
@refusals
def unlink_payment(purchase_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _finance_manage(context)
    S.unlink_payment(context, purchase_id)
    _audit(repository, context, "purchase.payment.unlink", purchase_id)
    return Response(status_code=204)


__all__ = ["router", "purchase_view", "return_view", "add_shipment", "Ownership", "Status", "ReturnStatus"]
