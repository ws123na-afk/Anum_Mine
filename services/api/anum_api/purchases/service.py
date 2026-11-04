"""Purchase rules (docs/purchases.md): who may change what, the lifecycle, payments, refunds and reminders.

* A **personal** purchase and its rows belong to one person. A **business** purchase is visible to
  every member who may use purchases; anyone of them follows its delivery (status, shipments) and
  asks for a return. Only the person who added it or a workspace owner (``purchase:manage``) edits
  or deletes it.
* **The lifecycle is a state machine** with an append-only history: every change records the
  state before and after (``purchase_events``). A change from a stale version answers 409.
* **Payments and refunds are links, never postings.** The purchase points at its canonical Finance
  transaction (dimension ``purchase``, role ``payment``). A refund points at the refund's own
  canonical transaction (role ``refund``, target the return of the original purchase). Only a
  transaction that gives money back to an expense category may be linked as a refund: a refund is
  never income. Cash is recorded once in Finance (the same path as a hand-entered transaction),
  then linked. The links keep the read copies (``payment``, ``refunds``) current through
  :func:`observe`, in the link's own transaction, through corrections, reconciliation and undo.

Everything here is synchronous: FastAPI runs it in its thread pool. No unit of work is held across
another module's call.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import TenantContext, new_id, utc_now
from .models import (
    AWAITING_DELIVERY,
    CLOSED,
    OPEN_RETURN,
    PAYMENT_TARGET,
    REFUND_DRIVEN,
    REFUND_TARGET,
    REFUND_WAITING,
    RETURN_TRANSITIONS,
    TARGET_TYPES,
    TRANSITIONS,
    Event,
    Item,
    Ownership,
    PaymentRef,
    Preferences,
    Purchase,
    Return,
    ReturnLine,
    Shipment,
)
from .store import MAX_ITEMS, PurchaseStore, open_purchase_store, purchase_memory_store, store_for_session

SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/#-]{0,79}$")
FUTURE_SLACK = timedelta(minutes=10)
CENT = Decimal("0.01")


class PurchaseError(Exception):
    def __init__(self, status_code: int, message: str, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.extra = extra or {}


def not_found(what: str = "Purchase") -> PurchaseError:
    return PurchaseError(404, f"{what} not found. It may have been deleted, or it is someone's personal purchase.", "not_found")


# -- who may do what -------------------------------------------------------------------------------


def can_manage(context: TenantContext) -> bool:
    return has_permission(context, Permission.PURCHASE_MANAGE)


def can_edit_purchase(context: TenantContext, purchase: Purchase) -> bool:
    return purchase.owner_user_id == context.user_id or (purchase.ownership == Ownership.BUSINESS and can_manage(context))


def can_edit_row(context: TenantContext, purchase: Purchase, row: Any) -> bool:
    """A shipment or return: its author, the purchase's owner, or a workspace owner on a business purchase."""
    return row.owner_user_id == context.user_id or can_edit_purchase(context, purchase)


def require_purchase_edit(context: TenantContext, purchase: Purchase) -> None:
    if not can_edit_purchase(context, purchase):
        raise PurchaseError(403, "Only the person who added this business purchase or a workspace owner can change it.", "not_purchase_owner")


def require_row_edit(context: TenantContext, purchase: Purchase, row: Any) -> None:
    if not can_edit_row(context, purchase, row):
        raise PurchaseError(403, "Only the person who added this, the purchase's owner or a workspace owner can change it.", "not_row_owner")


def require_payer(context: TenantContext, purchase: Purchase) -> None:
    if purchase.owner_user_id != context.user_id:
        raise PurchaseError(403, "Only the person who added this purchase links its payment and refunds, from their own Finance ledger.",
                            "not_purchase_owner")


# -- cleaning ----------------------------------------------------------------------------------------


def clean(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())[:limit]
    return text or None


def reference(value: str | None, field: str) -> str | None:
    text = clean(value, 80)
    if text is None:
        return None
    if not REF.match(text):
        raise PurchaseError(422, f"{field}: use letters, digits, spaces and . _ / # - only.", "invalid_reference", {"field": field})
    return text


def currency_code(value: str | None, fallback: str) -> str:
    code = (value or fallback).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise PurchaseError(422, "Currency: a three-letter code such as SAR or AED.", "invalid_currency")
    return code


def not_future(moment: datetime, field: str) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if moment > utc_now() + FUTURE_SLACK:
        raise PurchaseError(422, f"{field}: the time is in the future.", "in_future")
    return moment


def not_future_day(day: date | None, field: str) -> date | None:
    if day is not None and day > utc_now().date():
        raise PurchaseError(422, f"{field}: the date is in the future.", "in_future")
    return day


def record_key(context: TenantContext, *parts: object) -> str:
    return hashlib.sha256("\x1f".join((context.user_id, *(str(p) for p in parts))).encode()).hexdigest()


def money(value: Decimal) -> str:
    return str(value.quantize(CENT))


# -- purchases ---------------------------------------------------------------------------------------


def get_purchase(store: PurchaseStore, context: TenantContext, purchase_id: str, *, lock: bool = False) -> Purchase:
    if not SAFE_ID.match(purchase_id):
        raise not_found()
    if lock and callable(getattr(store, "lock_purchase", None)):
        store.lock_purchase(context, purchase_id)  # type: ignore[attr-defined]
    found = store.get_purchase(context, purchase_id)
    if found is None:
        raise not_found()
    return found


def preferences(store: PurchaseStore, context: TenantContext) -> Preferences:
    return store.get_preferences(context) or Preferences(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id)


def check_purchase(purchase: Purchase) -> Purchase:
    not_future_day(purchase.ordered_on, "Order date")
    if purchase.delivered_on is not None and purchase.delivered_on < purchase.ordered_on:
        raise PurchaseError(422, "The delivery date is before the order date.", "invalid_dates")
    if purchase.expected_delivery_on is not None and purchase.expected_delivery_on < purchase.ordered_on:
        raise PurchaseError(422, "The expected delivery is before the order date.", "invalid_dates")
    return purchase


def new_purchase(context: TenantContext, data: dict[str, Any], *, now: datetime, idem_key: str, currency: str,
                 source: str = "web", needs_review: bool = False) -> Purchase:
    merchant = clean(data.get("merchant"), 120)
    if not merchant:
        raise PurchaseError(422, "Name the shop or seller.", "merchant_required")
    return check_purchase(Purchase(
        id=new_id("pur"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        ownership=Ownership(data.get("ownership") or "personal"), order_ref=reference(data.get("order_ref"), "Order number"), merchant=merchant,
        ordered_on=data.get("ordered_on") or now.date(), currency=currency_code(data.get("currency"), currency), tax_amount=data.get("tax_amount"),
        shipping_amount=data.get("shipping_amount"), total=data.get("total"), payment_method=data.get("payment_method"),
        document_id=data.get("document_id"), expected_delivery_on=data.get("expected_delivery_on"), address_ref=clean(data.get("address_ref"), 80),
        warranty_until=data.get("warranty_until"), return_window_until=data.get("return_window_until"), source=source, needs_review=needs_review,  # type: ignore[arg-type]
        notes=clean(data.get("notes"), 1000), idem_key=idem_key, created_at=now, updated_at=now))


EDITABLE = ("order_ref", "merchant", "ordered_on", "currency", "tax_amount", "shipping_amount", "total", "payment_method", "document_id",
            "expected_delivery_on", "delivered_on", "address_ref", "warranty_until", "return_window_until", "notes", "needs_review")


def apply_changes(purchase: Purchase, changes: dict[str, Any], now: datetime) -> Purchase:
    data = purchase.model_dump()
    for key, value in changes.items():
        if key not in EDITABLE:
            continue
        if key == "merchant":
            value = clean(value, 120)
            if not value:
                raise PurchaseError(422, "Name the shop or seller.", "merchant_required")
        elif key == "order_ref":
            value = reference(value, "Order number")
        elif key == "address_ref":
            value = clean(value, 80)
        elif key == "notes":
            value = clean(value, 1000)
        elif key == "currency":
            value = currency_code(value, purchase.currency)
            if value != purchase.currency and purchase.payment is not None and purchase.payment.status != "void":
                raise PurchaseError(409, "The currency follows the linked payment. Remove the payment link first.", "payment_linked")
        data[key] = value
    data["updated_at"] = now
    return check_purchase(Purchase.model_validate(data))


def build_items(context: TenantContext, purchase: Purchase, rows: list[dict[str, Any]], existing: list[Item], now: datetime) -> list[Item]:
    """The purchase's lines from the input. An existing line keeps its id (a return points at it)."""
    if len(rows) > MAX_ITEMS:
        raise PurchaseError(422, f"A purchase has at most {MAX_ITEMS} lines.", "too_many_items")
    known = {item.id: item for item in existing}
    out: list[Item] = []
    for position, row in enumerate(rows):
        description = clean(row.get("description"), 200)
        if not description:
            raise PurchaseError(422, f"Line {position + 1}: describe the item.", "item_description")
        item_id = row.get("id")
        if item_id is not None and item_id not in known:
            raise PurchaseError(422, f"Line {position + 1} is not on this purchase.", "invalid_item")
        base = known.get(item_id) if item_id else None
        # Lines are rewritten by whoever edits the purchase (the insert policy names the writer); privacy keeps or removes them
        # with their purchase, never by this id.
        out.append(Item(id=item_id or new_id("puri"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        owner_user_id=context.user_id, purchase_id=purchase.id, visibility=purchase.ownership,
                        purchase_owner_id=purchase.owner_user_id, position=position, description=description, sku=clean(row.get("sku"), 60),
                        quantity=row.get("quantity") or Decimal(1), unit_price=row.get("unit_price"), warranty_until=row.get("warranty_until"),
                        created_at=base.created_at if base else now))
    return out


def items_total(items: list[Item]) -> Decimal | None:
    """The sum of the priced lines, a detail (never money in the ledger)."""
    priced = [i for i in items if i.unit_price is not None]
    if not priced:
        return None
    return sum((i.quantity * i.unit_price for i in priced), Decimal(0)).quantize(CENT)  # type: ignore[operator]


def in_returns(returns: list[Return]) -> dict[str, Decimal]:
    """Quantity of each line already in a return that is not rejected or cancelled."""
    out: dict[str, Decimal] = {}
    for item in returns:
        if item.status in ("rejected", "cancelled"):
            continue
        for line in item.items:
            out[line.item_id] = out.get(line.item_id, Decimal(0)) + line.quantity
    return out


def save_items(store: PurchaseStore, context: TenantContext, purchase: Purchase, rows: list[dict[str, Any]], now: datetime) -> list[Item]:
    existing = store.items(context, [purchase.id])
    items = build_items(context, purchase, rows, existing, now)
    kept = {item.id for item in items}
    taken = in_returns(store.returns(context, [purchase.id]))
    for item_id, quantity in taken.items():
        line = next((i for i in items if i.id == item_id), None)
        if item_id not in kept or (line is not None and line.quantity < quantity):
            raise PurchaseError(409, "A line that is part of a return cannot be removed or reduced below the returned quantity.", "item_in_return")
    store.replace_items(context, purchase.id, items)
    return items


# -- the history ----------------------------------------------------------------------------------------


def add_event(store: PurchaseStore, context: TenantContext, purchase: Purchase, kind: str, *, from_status: str | None = None,
              to_status: str | None = None, note: str | None = None, subject_id: str | None = None, source: str = "web",
              occurred_at: datetime | None = None, now: datetime | None = None) -> Event:
    now = now or utc_now()
    event = Event(id=new_id("pure"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                  purchase_id=purchase.id, visibility=purchase.ownership, purchase_owner_id=purchase.owner_user_id, kind=kind,
                  from_status=from_status, to_status=to_status, subject_id=subject_id, note=clean(note, 300), source=source,  # type: ignore[arg-type]
                  occurred_at=occurred_at or now, created_at=now)
    store.add_event(event)
    return event


def allowed_moves(status: str) -> list[str]:
    return [s for s in TRANSITIONS.get(status, ()) if s not in REFUND_DRIVEN]


def move(store: PurchaseStore, context: TenantContext, purchase: Purchase, to: str, *, expected_version: int | None, note: str | None,
         occurred_at: datetime | None, source: str = "web", system: bool = False, subject_id: str | None = None) -> Purchase:
    """Change the purchase's status along the state machine and record it. ``system`` moves (refund links) may set the
    refund statuses; a person may not, because the refunded amount comes from Finance."""
    now = utc_now()
    if to == purchase.status:
        return purchase
    if not system:
        if to in REFUND_DRIVEN:
            raise PurchaseError(409, "Refunded and partly refunded follow the refunds linked from Finance. Link the refund on the return.",
                                "refund_from_finance")
        if to not in TRANSITIONS.get(purchase.status, ()):
            raise PurchaseError(409, f"A purchase that is {purchase.status.replace('_', ' ')} cannot become {to.replace('_', ' ')}.",
                                "invalid_transition", {"allowed": allowed_moves(purchase.status)})
    moment = not_future(occurred_at, "Time") if occurred_at is not None else now
    if to == "delivered" and purchase.delivered_on is None and moment.date() < purchase.ordered_on:
        raise PurchaseError(422, "The delivery date is before the order date. Check the time, or correct the order date first.", "invalid_dates")
    before = purchase.status
    purchase.status = to
    if to == "delivered" and purchase.delivered_on is None:
        purchase.delivered_on = moment.date()
    purchase.updated_at = now
    version = purchase.version if expected_version is None else expected_version
    if not store.save_purchase(purchase, version):
        raise PurchaseError(409, "This purchase changed since you opened it. Reload and try again.", "stale")
    add_event(store, context, purchase, "status", from_status=before, to_status=to, note=note, source=source, occurred_at=moment, now=now,
              subject_id=subject_id)
    purchase.version = version + 1
    return purchase


def follow(store: PurchaseStore, context: TenantContext, purchase: Purchase, to: str, *, note: str | None, source: str = "web",
           subject_id: str | None = None, occurred_at: datetime | None = None) -> Purchase:
    """Move the purchase when its own machine allows it (a shipment or a return changed); otherwise only note it."""
    if to != purchase.status and to in TRANSITIONS.get(purchase.status, ()):
        return move(store, context, purchase, to, expected_version=None, note=note, occurred_at=occurred_at, source=source, subject_id=subject_id)
    return purchase


# -- shipments -------------------------------------------------------------------------------------------


def delivery_from_shipments(shipments: list[Shipment]) -> str | None:
    """What the shipments say about the purchase: delivered, partly delivered, out for delivery or shipped."""
    live = [s for s in shipments if s.status not in ("returned_to_sender",)]
    if not live:
        return None
    delivered = [s for s in live if s.status == "delivered"]
    if delivered and len(delivered) == len(live):
        return "delivered"
    if delivered:
        return "partially_delivered"
    if any(s.status == "out_for_delivery" for s in live):
        return "out_for_delivery"
    if any(s.status == "in_transit" for s in live):
        return "shipped"
    return None


# -- returns ---------------------------------------------------------------------------------------------


def return_lines(purchase_items: list[Item], other_returns: list[Return], rows: list[dict[str, Any]]) -> list[ReturnLine]:
    by_id = {item.id: item for item in purchase_items}
    taken = in_returns(other_returns)
    lines: list[ReturnLine] = []
    seen: set[str] = set()
    for row in rows:
        item = by_id.get(row.get("item_id") or "")
        if item is None:
            raise PurchaseError(422, "A returned item is not on this purchase.", "invalid_item")
        if item.id in seen:
            raise PurchaseError(422, "Each item appears once in a return.", "duplicate_item")
        seen.add(item.id)
        quantity = Decimal(str(row.get("quantity") or item.quantity))
        left = item.quantity - taken.get(item.id, Decimal(0))
        if quantity <= 0 or quantity > left:
            raise PurchaseError(409, f"Only {left.normalize()} of \"{item.description}\" can still be returned.", "quantity_exceeded",
                                {"item_id": item.id, "remaining": str(left.normalize())})
        lines.append(ReturnLine(item_id=item.id, quantity=quantity))
    return lines


def lines_amount(purchase_items: list[Item], lines: list[ReturnLine]) -> Decimal | None:
    by_id = {item.id: item for item in purchase_items}
    priced = [(by_id[line.item_id], line) for line in lines if by_id[line.item_id].unit_price is not None]
    if not priced or len(priced) != len(lines):
        return None
    return sum((item.unit_price * line.quantity for item, line in priced), Decimal(0)).quantize(CENT)  # type: ignore[operator]


def refunded_total(item: Return, currency: str | None = None) -> Decimal:
    """What active refund links paid back on a return (``needs_review`` and ``void`` do not count)."""
    return sum((r.amount for r in item.refunds if r.status == "active" and (currency is None or r.currency == currency)), Decimal(0))


def move_return(item: Return, to: str, *, system: bool = False) -> Return:
    if to == item.status:
        return item
    if not system:
        if to in REFUND_DRIVEN:
            raise PurchaseError(409, "Refunded and partly refunded follow the refunds linked from Finance. Link the refund on the return.",
                                "refund_from_finance")
        if to not in RETURN_TRANSITIONS.get(item.status, ()):
            raise PurchaseError(409, f"A return that is {item.status.replace('_', ' ')} cannot become {to.replace('_', ' ')}.", "invalid_transition",
                                {"allowed": [s for s in RETURN_TRANSITIONS.get(item.status, ()) if s not in REFUND_DRIVEN]})
    item.status = to
    today = utc_now().date()
    if to == "returned" and item.returned_on is None:
        item.returned_on = today
    if to == "refunded" and item.refunded_on is None:
        item.refunded_on = today
    return item


# -- payments: the purchase's payment and the refunds of its returns ----------------------------------------


def _link_error(exc: Any) -> PurchaseError:
    return PurchaseError(exc.status_code, exc.message, exc.code, exc.extra)


def payment_from_link(link: Any, *, original: str | None = None) -> PaymentRef:
    return PaymentRef(link_id=link.id, canonical_id=link.canonical_id, status=link.status.value if hasattr(link.status, "value") else link.status,
                      status_reason=link.status_reason, amount=Decimal(link.amount).quantize(CENT), currency=link.currency, paid_by=link.owner_user_id,
                      linked_at=link.created_at, original_canonical_id=original)


def is_expense(entry: Any) -> bool:
    """Money spent: an expense account is debited and no income account is touched."""
    postings = getattr(entry, "postings", []) or []
    return (any(p.ledger_account.startswith("expense:") and p.amount > 0 for p in postings)
            and not any(p.ledger_account.startswith("income:") for p in postings))


def is_refund(entry: Any) -> bool:
    """Money given back to an expense category (a refund lowers spending; it is never income)."""
    postings = getattr(entry, "postings", []) or []
    return (any(p.ledger_account.startswith("expense:") and p.amount < 0 for p in postings)
            and not any(p.ledger_account.startswith("income:") for p in postings))


def _chain(context: TenantContext, canonical_id: str) -> tuple[list[Any], dict[str, Decimal], list[Any]]:
    from ..finance import links as K
    from ..finance.links_store import open_link_stores

    with open_link_stores(context) as (_finance, store, links):
        chain = store.chain(context, canonical_id)
        caps = K.capacity(chain) if chain else {}
        live = links.live_links(context, canonical_id) if chain else []
    return chain, caps, live


def _currency(caps: dict[str, Decimal], wanted: str | None) -> str:
    if wanted is not None:
        wanted = wanted.upper()
        if wanted not in caps:
            raise PurchaseError(422, f"The payment is not in {wanted}. Link it in its own currency.", "currency_mismatch", {"currencies": sorted(caps)})
        return wanted
    if len(caps) == 1:
        return next(iter(caps))
    raise PurchaseError(422, "This payment has more than one currency: choose the currency and enter the amount.", "currency_required",
                        {"currencies": sorted(caps)})


def payment_target(context: TenantContext, purchase_id: str) -> Purchase:
    with open_purchase_store(context) as store:
        purchase = get_purchase(store, context, purchase_id)
    require_payer(context, purchase)
    return purchase


def link_payment(context: TenantContext, purchase_id: str, *, canonical_id: str, amount: Decimal | None,
                 currency: str | None = None) -> tuple[PaymentRef, bool]:
    """Link (part of) a canonical transaction as the purchase's payment. Returns the payment and whether it was a replay.

    The link's idempotency key names the purchase only, so a purchase has at most one live payment link: two tabs
    linking different payments at once are serialised by the owner's lock in ``finance.links.create`` and the second
    is refused (``already_linked``); the ``purchase`` dimension is registered with ``single_payment`` for every route."""
    from ..finance import links as K

    purchase = payment_target(context, purchase_id)
    current = purchase.payment
    if current is not None and current.status != "void":
        if current.canonical_id == canonical_id:
            return current, True  # the same payment again: nothing changes
        raise PurchaseError(409, "This purchase is already linked to another payment. Remove that link first.", "already_linked")
    if not SAFE_ID.match(canonical_id):
        raise PurchaseError(422, "The payment id is not valid.", "invalid_id")
    chain, caps, live = _chain(context, canonical_id)
    if not chain:
        raise PurchaseError(404, "Payment not found in your Finance ledger.", "transaction_not_found")
    entry = K.live_entry(chain)
    if entry is not None and not is_expense(entry):
        raise PurchaseError(422, "This transaction is not money spent (it is income, a refund or a transfer). Pick the payment of the purchase.",
                            "not_a_payment")
    currency = _currency(caps, currency)
    if amount is None:
        amount = caps[currency] - K.used(live, "purchase", currency)
        if amount <= 0:
            raise PurchaseError(409, "Nothing of this payment is left to link to a purchase.", "over_allocated", {"remaining": "0", "currency": currency})
    try:
        created = K.create(context, K.LinkRequest(canonical_id=canonical_id, dimension="purchase", role="payment", target_type=PAYMENT_TARGET,
                                                  target_id=purchase_id, amount=amount, currency=currency,
                                                  idempotency_key=f"purchases\x1fpayment\x1f{purchase_id}"))
    except K.LinkError as exc:
        if exc.code == "idempotency_conflict":
            raise PurchaseError(409, "This purchase was just linked to another payment. Reload to see it.", "already_linked") from None
        raise _link_error(exc) from None
    return payment_from_link(created.link), created.replayed


def _record_manual(context: TenantContext, *, seed_parts: tuple[object, ...], account_id: str, amount: Decimal, posted_on: date,
                   description: str, category: str) -> tuple[str, str | None]:
    """Record one hand-entered Finance transaction (``amount`` signed: negative spent, positive received) and return its
    canonical id and, if this request created it, the transaction id. The id is derived from the request, so a retry finds
    the transaction it made; an earlier one with other figures is never reused (the next id is tried)."""
    from fastapi import HTTPException

    from ..finance.api import TransactionInput, record_manual_transaction
    from ..finance.models import Category

    body = TransactionInput(account_id=account_id, posted_on=posted_on, amount=amount, description=description, category=Category(category),
                            notes="Recorded from Purchases")
    for attempt in range(8):
        transaction_id = "fin_txn_pur" + record_key(context, *seed_parts, attempt)[:32]
        existed = _transaction_exists(context, transaction_id)
        try:
            item, _decision, found = record_manual_transaction(context, body, transaction_id=transaction_id)
        except HTTPException as exc:
            raise PurchaseError(exc.status_code, str(exc.detail), "finance_refused") from None
        if item.account_id == account_id and item.amount == amount and item.posted_on == posted_on:
            return found, (None if existed else transaction_id)
    raise PurchaseError(409, "This cash payment could not be recorded. Try again with a new request.", "cash_conflict")


def _transaction_exists(context: TenantContext, transaction_id: str) -> bool:
    from ..finance.store import open_finance_store

    with open_finance_store(context) as store:
        return store.get_transaction(context, transaction_id) is not None


def _undo_manual(context: TenantContext, transaction_id: str, why: str) -> None:
    """Reverse a transaction this request recorded (the same path as deleting it in Finance)."""
    from ..finance.sms_service import owner_lock, reverse
    from ..finance.sms_store import open_sms_stores

    with owner_lock(context), open_sms_stores(context) as (store, sms):
        sms.lock_owner(context)
        entry = sms.entry_for_transaction(context, transaction_id)
        if entry is not None and entry.status.value == "posted":
            reverse(store, sms, context, entry, now=utc_now(), why=why)


def record_cash(context: TenantContext, purchase_id: str, *, account_id: str, amount: Decimal, posted_on: date,
                idempotency_key: str | None) -> tuple[PaymentRef, bool]:
    """A cash payment: one hand-entered Finance transaction (the ledger's own path), then the link."""
    if amount <= 0:
        raise PurchaseError(422, "The amount must be more than zero.", "invalid_amount")
    purchase = payment_target(context, purchase_id)
    current = purchase.payment
    if current is not None and current.status != "void":
        if current.recorded_as_cash and current.amount == amount:
            return current, True
        raise PurchaseError(409, "This purchase is already linked to a payment. Remove that link first.", "already_linked")
    seed = idempotency_key or f"v{purchase.version}"
    canonical, created = _record_manual(context, seed_parts=("cash", purchase_id, seed), account_id=account_id, amount=-amount, posted_on=posted_on,
                                        description=f"{purchase.merchant}, purchase"[:500], category="shopping")
    try:
        payment, replayed = link_payment(context, purchase_id, canonical_id=canonical, amount=None)
    except PurchaseError:
        # Another tab linked a payment first: the expense this request recorded is undone, so it never stays posted.
        if created is not None:
            _undo_manual(context, created, "cash payment not linked to the purchase")
        raise
    payment.recorded_as_cash = True
    with open_purchase_store(context) as store:
        store.set_payment(context, purchase_id, payment)
    return payment, replayed


def unlink_payment(context: TenantContext, purchase_id: str) -> None:
    """Remove the purchase's payment link (the Finance transaction stays). Repeating it changes nothing."""
    from ..finance import links as K

    purchase = payment_target(context, purchase_id)
    current = purchase.payment
    if current is None:
        return
    if current.status != "void":
        try:
            K.void(context, current.link_id, expected_version=None)
        except K.LinkError as exc:
            if exc.code != "link_not_found":
                raise _link_error(exc) from None
    with open_purchase_store(context) as store:
        store.set_payment(context, purchase_id, None)


def refund_target(context: TenantContext, return_id: str) -> tuple[Return, Purchase, list[Return]]:
    if not SAFE_ID.match(return_id):
        raise not_found("Return")
    with open_purchase_store(context) as store:
        item = store.get_return(context, return_id)
        if item is None:
            raise not_found("Return")
        purchase = get_purchase(store, context, item.purchase_id)
        siblings = store.returns(context, [purchase.id])
    require_payer(context, purchase)
    return item, purchase, siblings


def paid_amount(purchase: Purchase) -> tuple[Decimal | None, str]:
    """What was paid for the purchase: the active payment link, else the shop's total (a detail)."""
    if purchase.payment is not None and purchase.payment.status == "active":
        return purchase.payment.amount, purchase.payment.currency
    return purchase.total, purchase.currency


def refund_room(purchase: Purchase, returns: list[Return], currency: str, *, skip_link: str | None = None) -> Decimal | None:
    """How much more may be refunded on the purchase, or None when nothing says what was paid."""
    paid, paid_currency = paid_amount(purchase)
    if paid is None or paid_currency != currency:
        return None
    refunded = sum((r.amount for item in returns for r in item.refunds if r.status == "active" and r.currency == currency and r.link_id != skip_link),
                   Decimal(0))
    return paid - refunded


def link_refund(context: TenantContext, return_id: str, *, canonical_id: str, amount: Decimal | None,
                currency: str | None = None) -> tuple[PaymentRef, bool]:
    """Link (part of) a refund transaction to a return of the purchase. A refund is never income: the transaction must give
    money back to an expense category. The refund cannot be more than what was paid for the purchase."""
    from ..finance import links as K

    item, purchase, siblings = refund_target(context, return_id)
    if item.status in ("rejected", "cancelled"):
        raise PurchaseError(409, "This return was rejected or cancelled. Open it again before linking a refund.", "return_closed")
    for ref in item.refunds:
        if ref.canonical_id == canonical_id and ref.status != "void":
            return ref, True  # the same refund again
    if not SAFE_ID.match(canonical_id):
        raise PurchaseError(422, "The refund id is not valid.", "invalid_id")
    chain, caps, live = _chain(context, canonical_id)
    if not chain:
        raise PurchaseError(404, "Refund not found in your Finance ledger.", "transaction_not_found")
    entry = K.live_entry(chain)
    if entry is not None and not is_refund(entry):
        raise PurchaseError(422, ("This transaction is not a refund in Finance (it is income, a payment or a transfer). A refund is never income: "
                                  "correct it in Finance to a refund of the purchase's category, then link it."), "not_a_refund")
    if purchase.payment is not None and purchase.payment.status != "void" and currency is None and purchase.payment.currency in caps:
        currency = purchase.payment.currency
    currency = _currency(caps, currency)
    room = refund_room(purchase, siblings, currency)
    left_on_return = (item.amount - refunded_total(item, currency)) if item.amount is not None and item.currency == currency else None
    if amount is None:
        candidates = [caps[currency] - K.used(live, "purchase", currency)]
        candidates += [value for value in (room, left_on_return) if value is not None]
        amount = min(candidates)
        if amount <= 0:
            raise PurchaseError(409, "Nothing is left to refund on this purchase from this transaction.", "over_allocated",
                                {"remaining": "0", "currency": currency})
    if room is not None and amount > room:
        raise PurchaseError(409, f"The refunds would be more than was paid for this purchase. Only {money(max(room, Decimal(0)))} {currency} is left.",
                            "refund_over_paid", {"remaining": money(max(room, Decimal(0))), "currency": currency})
    try:
        created = K.create(context, K.LinkRequest(canonical_id=canonical_id, dimension="purchase", role="refund", target_type=REFUND_TARGET,
                                                  target_id=return_id, amount=amount, currency=currency,
                                                  idempotency_key=f"purchases\x1frefund\x1f{return_id}\x1f{canonical_id}"))
    except K.LinkError as exc:
        raise _link_error(exc) from None
    original = purchase.payment.canonical_id if purchase.payment is not None and purchase.payment.status != "void" else None
    return payment_from_link(created.link, original=original), created.replayed


def record_cash_refund(context: TenantContext, return_id: str, *, account_id: str, amount: Decimal, posted_on: date,
                       idempotency_key: str | None) -> tuple[PaymentRef, bool]:
    """Money given back in cash (or to an account by hand): one Finance transaction that **lowers the purchase's
    category** (never income), then the refund link."""
    if amount <= 0:
        raise PurchaseError(422, "The amount must be more than zero.", "invalid_amount")
    item, purchase, _siblings = refund_target(context, return_id)
    seed = idempotency_key or f"v{item.version}"
    category = _payment_category(context, purchase)
    canonical, created = _record_manual(context, seed_parts=("refund", return_id, seed), account_id=account_id, amount=amount, posted_on=posted_on,
                                        description=f"{purchase.merchant}, refund"[:500], category=category)
    for ref in item.refunds:
        if ref.canonical_id == canonical and ref.status != "void":
            return ref, True  # a retry of this same refund
    try:
        refund, replayed = link_refund(context, return_id, canonical_id=canonical, amount=amount)
    except PurchaseError:
        if created is not None:
            _undo_manual(context, created, "refund not linked to the return")
        raise
    refund.recorded_as_cash = True
    with open_purchase_store(context) as store:
        found = store.get_return(context, return_id)
        if found is not None:
            store.set_refunds(context, return_id, [r.model_copy(update={"recorded_as_cash": True}) if r.link_id == refund.link_id else r
                                                   for r in found.refunds])
    return refund, replayed


def _payment_category(context: TenantContext, purchase: Purchase) -> str:
    """The category of the purchase's own payment (a refund lowers it), else shopping."""
    from ..finance import links as K
    from ..finance.models import Category

    if purchase.payment is None or purchase.payment.status == "void" or purchase.payment.paid_by != context.user_id:
        return Category.SHOPPING.value
    chain, _caps, _live = _chain(context, purchase.payment.canonical_id)
    entry = K.live_entry(chain) if chain else None
    category = (entry.category if entry is not None else None) or Category.SHOPPING.value
    return category if category in {c.value for c in Category} - {Category.INCOME.value, Category.TRANSFER.value} else Category.SHOPPING.value


def unlink_refund(context: TenantContext, return_id: str, link_id: str) -> None:
    """Remove one refund link (the Finance transaction stays). Repeating it changes nothing."""
    from ..finance import links as K

    item, _purchase, _siblings = refund_target(context, return_id)
    ref = next((r for r in item.refunds if r.link_id == link_id), None)
    if ref is None:
        return
    if ref.status != "void":
        try:
            K.void(context, link_id, expected_version=None)
        except K.LinkError as exc:
            if exc.code != "link_not_found":
                raise _link_error(exc) from None
    with open_purchase_store(context) as store:
        found = store.get_return(context, return_id)
        if found is not None:
            store.set_refunds(context, return_id, [r for r in found.refunds if r.link_id != link_id])
            purchase = store.get_purchase(context, found.purchase_id)
            if purchase is not None:
                settle_refunds(store, context, purchase)


# -- the read copies: kept by the links -----------------------------------------------------------------


def observe(links_store: Any, link: Any) -> None:
    """A finance link of a purchase was made or changed (status, canonical id): update the read copy in the same unit of
    work. A link the owner removed clears it; an undone payment or refund leaves it as ``void``. A new refund link that
    would make the refunds more than was paid is refused here too, under the owner's lock, so two tabs cannot both pass."""
    if link.target_type not in TARGET_TYPES or link.amount is None:
        return
    context = TenantContext(tenant_id=link.tenant_id, workspace_id=link.workspace_id, user_id=link.owner_user_id)
    store = store_for_session(getattr(links_store, "session", None), context)
    status = link.status.value if hasattr(link.status, "value") else link.status
    if link.target_type == PAYMENT_TARGET:
        purchase = store.get_purchase(context, link.target_id)
        if purchase is None:
            return
        current = purchase.payment
        if current is not None and current.link_id != link.id and current.status != "void" and status == "void":
            return  # an older link was voided after the purchase was linked again: keep the current one
        if status == "void" and link.status_reason == "removed_by_owner":
            payment = None
        else:
            payment = payment_from_link(link)
            if current is not None and current.link_id == link.id:
                payment.recorded_as_cash = current.recorded_as_cash
        store.set_payment(context, link.target_id, payment)
        purchase = store.get_purchase(context, link.target_id)
        if purchase is not None:
            settle_refunds(store, context, purchase)
        return
    item = store.get_return(context, link.target_id)
    if item is None:
        return
    purchase = store.get_purchase(context, item.purchase_id)
    if purchase is None:
        return
    refs = [r for r in item.refunds if r.link_id != link.id]
    previous = next((r for r in item.refunds if r.link_id == link.id), None)
    if not (status == "void" and link.status_reason == "removed_by_owner"):
        original = previous.original_canonical_id if previous is not None else (
            purchase.payment.canonical_id if purchase.payment is not None and purchase.payment.status != "void" else None)
        ref = payment_from_link(link, original=original)
        if previous is not None:
            ref.recorded_as_cash = previous.recorded_as_cash
        if previous is None and status == "active":
            siblings = store.returns(context, [purchase.id])
            room = refund_room(purchase, siblings, ref.currency, skip_link=link.id)
            if room is not None and ref.amount > room:
                _refuse_fresh_link(links_store, link)
                from ..finance.links import LinkError

                raise LinkError(409, f"The refunds would be more than was paid for this purchase. Only {money(max(room, Decimal(0)))} "
                                     f"{ref.currency} is left.", "refund_over_paid",
                                {"remaining": money(max(room, Decimal(0))), "currency": ref.currency})
        refs.append(ref)
    store.set_refunds(context, item.id, refs)
    settle_refunds(store, context, purchase)


def _refuse_fresh_link(links_store: Any, link: Any) -> None:
    """In PostgreSQL the refusal rolls the whole unit of work back. The in-memory store has no transaction: drop the link."""
    if getattr(links_store, "session", None) is None:
        rows = getattr(links_store, "links", None)
        if isinstance(rows, dict):
            rows.pop(link.id, None)


def settle_refunds(store: PurchaseStore, context: TenantContext, purchase: Purchase) -> None:
    """Return and purchase statuses follow the refunds linked from Finance: partly refunded, refunded, or back to waiting for
    the refund when a refund was undone. A dispute stays a dispute until the person moves it."""
    returns = store.returns(context, [purchase.id])
    for item in returns:
        if item.status in ("rejected", "cancelled", "disputed", "requested", "approved", "pickup_scheduled") and not item.refunds:
            continue
        total = refunded_total(item, item.currency)
        # A refund in another currency than the return's (the card's, not the shop's) cannot be compared with the
        # expected amount: currencies are never converted, so it settles the return.
        elsewhere = refunded_total(item) - total
        before = item.status
        if (total > 0 or elsewhere > 0) and item.status not in ("disputed", "rejected", "cancelled"):
            full = item.amount is None or total >= item.amount or (total == 0 and elsewhere > 0)
            move_return(item, "refunded" if full else "partially_refunded", system=True)
        elif total == 0 and elsewhere == 0 and item.status in REFUND_DRIVEN:
            move_return(item, "refund_pending", system=True)
            item.refunded_on = None
        if item.status != before:
            item.updated_at = utc_now()
            if store.save_return(item, item.version):
                add_event(store, context, purchase, "refund", note=f"Return {before.replace('_', ' ')} to {item.status.replace('_', ' ')}",
                          subject_id=item.id, source="system")
    if purchase.status == "disputed":
        return
    paid, paid_currency = paid_amount(purchase)
    refunded = sum((r.amount for item in returns for r in item.refunds if r.status == "active" and r.currency == paid_currency), Decimal(0))
    if refunded > 0:
        target = "refunded" if paid is not None and refunded >= paid else "partially_refunded"
        move(store, context, purchase, target, expected_version=None, note=None, occurred_at=None, source="system", system=True)
    elif purchase.status in REFUND_DRIVEN:
        move(store, context, purchase, "refund_pending", expected_version=None, note=None, occurred_at=None, source="system", system=True)


def link_target_exists(context: TenantContext, target_type: str, target_id: str) -> bool:
    """``finance.links`` asks whether a purchase target exists and the caller may link their payment or refund to it."""
    if target_type not in TARGET_TYPES or not SAFE_ID.match(target_id):
        return False
    try:
        with open_purchase_store(context) as store:
            if target_type == PAYMENT_TARGET:
                purchase = store.get_purchase(context, target_id)
            else:
                item = store.get_return(context, target_id)
                purchase = store.get_purchase(context, item.purchase_id) if item is not None else None
            return purchase is not None and purchase.owner_user_id == context.user_id
    except PurchaseError:
        return False


def plug_finance_links() -> None:
    """Register the ``purchase`` link dimension with the finance links foundation."""
    from ..finance import links as K

    K.register_target("purchase", link_target_exists, observe, single_payment=True)


# -- the payment picker ----------------------------------------------------------------------------------


def payment_candidates(context: TenantContext, *, kind: str, query: str | None, around: date | None, amount: Decimal | None,
                       limit: int) -> list[dict[str, Any]]:
    """The caller's own live transactions near a date: money spent (``payment``) or given back to an expense category
    (``refund``), with what is left to link to a purchase. A suggestion only: linking checks again under the owner's lock."""
    from ..finance import links as K
    from ..finance.links_store import LinkStatus, open_link_stores

    centre = around or utc_now().date()
    start, end = (centre - timedelta(days=60), centre + timedelta(days=15)) if kind == "payment" else (centre - timedelta(days=30),
                                                                                                         centre + timedelta(days=150))
    needle = (query or "").casefold().strip()
    with open_link_stores(context) as (_finance, store, links):
        entries = store.list_entries(context, start=start, end=end, limit=5_000)
        purchase_links, _ = links.list_links(context, canonical_id=None, dimension="purchase", target_type=None, target_id=None, status=None,
                                             limit=10_000, offset=0)
    chains: dict[str, list[Any]] = {}
    for entry in entries:
        chains.setdefault(entry.canonical_id or entry.id, []).append(entry)
    live_by: dict[str, list[Any]] = {}
    for link in purchase_links:
        if link.status != LinkStatus.VOID:
            live_by.setdefault(link.canonical_id, []).append(link)
    wanted = is_expense if kind == "payment" else is_refund
    out: list[dict[str, Any]] = []
    for canonical, chain in chains.items():
        current = K.live_entry(chain)
        if current is None or not wanted(current):
            continue
        if needle and needle not in f"{current.merchant or ''} {current.description}".casefold():
            continue
        live = live_by.get(canonical, [])
        for currency, cap in K.capacity(chain).items():
            remaining = cap - K.used(live, "purchase", currency)
            out.append({"canonical_id": canonical, "posted_on": current.posted_on.isoformat(), "merchant": (current.merchant or current.description)[:80],
                        "category": current.category, "amount": money(cap), "currency": currency, "remaining": money(max(remaining, Decimal(0))),
                        "linked_to_purchase": bool(live), "_day": current.posted_on, "_cap": cap})

    def rank(item: dict[str, Any]) -> tuple[Any, ...]:
        gap = abs(item["_cap"] - amount) if amount is not None else Decimal(0)
        return (gap, abs((item["_day"] - centre).days), item["canonical_id"])

    out.sort(key=rank)
    return [{k: v for k, v in item.items() if not k.startswith("_")} for item in out[:limit]]


# -- what is due -------------------------------------------------------------------------------------------

MISSING_CONFIRMATION_DAYS = 3
REFUND_GRACE_DAYS = 14


def due_items(purchase: Purchase, returns: list[Return], items: list[Item], *, today: date, lead_days: int,
              warranty_days: int) -> list[dict[str, Any]]:
    """What needs the person's attention for one purchase (docs/purchases.md#reminders). Nothing once it is closed."""
    if purchase.archived_at is not None or purchase.status in CLOSED:
        return []
    out: list[dict[str, Any]] = []

    def add(kind: str, source_id: str, due: date, **extra: Any) -> None:
        out.append({"purchase_id": purchase.id, "merchant": purchase.merchant, "order_ref": purchase.order_ref, "kind": kind, "source_id": source_id,
                    "due_on": due.isoformat(), "days_left": (due - today).days, **extra})

    if purchase.status in AWAITING_DELIVERY and purchase.expected_delivery_on is not None:
        days = (purchase.expected_delivery_on - today).days
        if days < 0:
            add("delivery_late", purchase.id, purchase.expected_delivery_on)
        elif days <= 1:
            add("delivery_expected", purchase.id, purchase.expected_delivery_on)
    if purchase.status == "ordered" and (today - purchase.ordered_on).days >= MISSING_CONFIRMATION_DAYS:
        add("missing_confirmation", purchase.id, purchase.ordered_on + timedelta(days=MISSING_CONFIRMATION_DAYS))
    open_returns = [r for r in returns if r.status in OPEN_RETURN]
    if (purchase.status in ("delivered", "partially_delivered") and not open_returns and purchase.return_window_until is not None
            and 0 <= (purchase.return_window_until - today).days <= lead_days):
        add("return_window", purchase.id, purchase.return_window_until)
    for item in open_returns:
        if item.deadline_on is not None and item.status in ("requested", "approved", "pickup_scheduled") and (item.deadline_on - today).days <= lead_days:
            add("return_window", item.id, item.deadline_on, return_id=item.id)
        if item.status == "pickup_scheduled" and item.pickup_on is not None and (item.pickup_on - today).days <= 1:
            add("pickup_due", item.id, item.pickup_on, return_id=item.id)
        if item.status in REFUND_WAITING:
            due = item.expected_refund_on or ((item.returned_on or item.requested_on) + timedelta(days=REFUND_GRACE_DAYS))
            if due < today:
                add("refund_overdue", item.id, due, return_id=item.id)
        if item.status == "returned" and not item.confirmation_ref and item.returned_on is not None and (today - item.returned_on).days >= 1:
            add("missing_confirmation", item.id, item.returned_on + timedelta(days=1), return_id=item.id)
    if purchase.status not in ("returned", "refund_pending"):
        warranties = [(purchase.id, purchase.warranty_until)] + [(i.id, i.warranty_until) for i in items if i.purchase_id == purchase.id]
        for source_id, until in warranties:
            if until is not None and 0 <= (until - today).days <= warranty_days:
                add("warranty", source_id, until)
    out.sort(key=lambda item: (item["days_left"], item["kind"]))
    return out


def clear_memory() -> None:
    purchase_memory_store.clear()


__all__ = [
    "PurchaseError",
    "add_event",
    "can_edit_purchase",
    "can_edit_row",
    "due_items",
    "follow",
    "is_expense",
    "is_refund",
    "link_payment",
    "link_refund",
    "move",
    "move_return",
    "observe",
    "payment_candidates",
    "plug_finance_links",
    "record_cash",
    "record_cash_refund",
    "settle_refunds",
    "unlink_payment",
    "unlink_refund",
]
