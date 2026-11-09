"""Tamara refunds on the installment statement: preview, apply on the owner's confirmation, undo.

docs/finance.md#tamara-refunds.

* ``GET /installment-refunds``: every order of the owner's statement with its refund evidence (refund emails
  kept from Gmail imports, the statement's own refunded or lowered rows, Tamara credits in the ledger), the
  new schedule, and whether it can be applied.
* ``POST /installment-refunds/apply``: apply the plans the owner confirmed, each with the ``plan_hash`` the page
  showed (409 when the evidence changed since). Refunded installments stop being due: their statement rows
  record the refunded amount, and bills imported for them are paused (fully refunded) or lowered (partly).
* ``POST /installment-refunds/{id}/undo``: put an applied refund back exactly as it was.

Nothing here moves money, marks a payment as received or changes an email. The routes are plain ``def`` (run in
the thread pool), and each opens one short unit of work: never a database call on the event loop
(docs/realtime.md#event-loop).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .api import _audit, _manage, _read
from .installment_refunds import OrderRefundPlan, bill_installment, order_key, plan_refunds
from .models import (
    Bill,
    InstallmentRefundBillChange,
    InstallmentRefundChange,
    InstallmentRefundRecord,
    Planning,
)
from .money import quantize
from .store import FinanceStore, TransactionFilter, open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAX_LEDGER_CREDITS = 5000
MAX_RECORDS = 2000


class RefundRecordView(BaseModel):
    id: str
    provider: str
    order_id: str
    currency: str
    applied_at: str
    refund_total: Decimal
    credit_back: Decimal
    installments: int
    bills: int
    undone: bool
    can_undo: bool


class RefundPreview(BaseModel):
    today: date
    orders: list[OrderRefundPlan]
    pending: int
    needs_review: int
    records: list[RefundRecordView]


class ApplyOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=80)
    order_id: str = Field(min_length=1, max_length=160)
    plan_hash: str = Field(min_length=64, max_length=64)


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders: list[ApplyOrder] = Field(min_length=1, max_length=200)


class ApplyResult(BaseModel):
    applied: int
    installments: int
    bills_paused: int
    bills_lowered: int
    record_ids: list[str]
    preview: RefundPreview


def _credits(store: FinanceStore, context: TenantContext):
    found, _total = store.list_transactions(context, TransactionFilter(query="tamara", direction="in"), limit=MAX_LEDGER_CREDITS, offset=0)
    return found


def _bills(store: FinanceStore, context: TenantContext) -> dict[tuple[str, int], Bill]:
    """Bills imported from statement rows, by order and installment (the newest when two claim one)."""
    found: dict[tuple[str, int], Bill] = {}
    for bill in store.list_items(context, "bill"):
        if not isinstance(bill, Bill):
            continue
        key = bill_installment(bill)
        if key is not None and (key not in found or bill.updated_at > found[key].updated_at):
            found[key] = bill
    return found


def _latest_live(records: list[InstallmentRefundRecord]) -> dict[tuple[str, str], str]:
    latest: dict[tuple[str, str], InstallmentRefundRecord] = {}
    for record in records:
        if record.undone_at is None:
            key = order_key(record.provider, record.order_id)
            if key not in latest or record.applied_at >= latest[key].applied_at:
                latest[key] = record
    return {k: r.id for k, r in latest.items()}


def _preview(store: FinanceStore, context: TenantContext, planning: Planning) -> RefundPreview:
    plans = plan_refunds(planning.installment_entries, planning.installment_refund_notices, _credits(store, context))
    live = set(_latest_live(planning.installment_refunds).values())
    records = sorted(planning.installment_refunds, key=lambda r: r.applied_at, reverse=True)[:50]
    return RefundPreview(
        today=today_in(settings_for(store, context)), orders=plans, pending=sum(1 for p in plans if p.pending),
        needs_review=sum(1 for p in plans if p.state == "needs_review"),
        records=[RefundRecordView(
            id=r.id, provider=r.provider, order_id=r.order_id, currency=r.currency, applied_at=r.applied_at.isoformat(),
            refund_total=r.refund_total, credit_back=r.credit_back, installments=len(r.changes), bills=len(r.bills),
            undone=r.undone_at is not None, can_undo=r.id in live,
        ) for r in records],
    )


@router.get("/installment-refunds", response_model=RefundPreview)
def get_installment_refunds(context: TenantContext = Depends(tenant_context)) -> RefundPreview:
    _read(context)
    with open_finance_store(context) as store:
        return _preview(store, context, store.get_planning(context))


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


@router.post("/installment-refunds/apply", response_model=ApplyResult)
def apply_installment_refunds(
    body: ApplyInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ApplyResult:
    """Apply confirmed refund plans, all or nothing. Strong evidence only; estimates are never applied."""
    _manage(context)
    wanted = {order_key(o.provider, o.order_id): o for o in body.orders}
    if len(wanted) != len(body.orders):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each order can be listed once.")
    record_ids: list[str] = []
    changed_rows = paused = lowered = 0
    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        plans = {order_key(p.provider, p.order_id): p for p in plan_refunds(planning.installment_entries, planning.installment_refund_notices,
                                                                             _credits(store, context))}
        for key, request in wanted.items():
            plan = plans.get(key)
            if plan is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "That order is not on your installment statement.")
            if plan.plan_hash != request.plan_hash:
                raise _conflict("The refund evidence for this order changed. Reload and check it again.")
            if not plan.applicable:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    "This order has nothing to apply, or only an estimate that needs your review.")
        if len(planning.installment_refunds) + len(wanted) > MAX_RECORDS:
            raise _conflict("Too many refund records are kept. Contact support.")
        bills = _bills(store, context)
        now = utc_now()
        rows_by_key = {(order_key(r.provider, r.order_id), r.installment): i for i, r in enumerate(planning.installment_entries)}
        for key in wanted:
            plan = plans[key]
            changes: list[InstallmentRefundChange] = []
            bill_changes: list[InstallmentRefundBillChange] = []
            for item in plan.installments:
                if item.refunded_after == item.refunded_before:
                    continue
                index = rows_by_key[(key, item.installment)]
                row = planning.installment_entries[index]
                planning.installment_entries[index] = row.model_copy(update={"refunded_amount": item.refunded_after})
                changes.append(InstallmentRefundChange(installment=item.installment, previous_refunded=item.refunded_before, refunded=item.refunded_after))
                changed_rows += 1
                bill = bills.get((key[1], item.installment))
                # Only the one-time bill for this very due date; a bill already moved by the person is left alone.
                if bill is None or bill.cadence != "once" or bill.next_due_on != item.due_on or bill.currency != plan.currency:
                    continue
                active, amount = (False, bill.amount) if item.due_after <= 0 else (bill.active, quantize(item.due_after, bill.currency))
                if active == bill.active and amount == bill.amount:
                    continue
                bill_changes.append(InstallmentRefundBillChange(bill_id=bill.id, previous_amount=bill.amount, previous_active=bill.active,
                                                                amount=amount, active=active))
                store.save_item("bill", bill.model_copy(update={"active": active, "amount": amount, "updated_at": now}))
                if not active:
                    paused += 1
                else:
                    lowered += 1
            record = InstallmentRefundRecord(
                id=new_id("fin_refund"), provider=plan.provider, order_id=plan.order_id, currency=plan.currency, applied_at=now,
                applied_by=context.user_id, refund_total=plan.refund_total, credit_back=plan.credit_back,
                evidence=[e.id for e in plan.evidence if e.strength == "strong"][:200], changes=changes, bills=bill_changes,
            )
            planning.installment_refunds.append(record)
            record_ids.append(record.id)
        store.save_planning(context, planning)
        preview = _preview(store, context, planning)
    _audit(repository, context, "finance.installment_refunds.apply", context.user_id,
           {"orders": len(record_ids), "installments": changed_rows, "bills_paused": paused, "bills_lowered": lowered})
    return ApplyResult(applied=len(record_ids), installments=changed_rows, bills_paused=paused, bills_lowered=lowered,
                       record_ids=record_ids, preview=preview)


@router.post("/installment-refunds/{record_id}/undo", response_model=RefundPreview)
def undo_installment_refund(
    record_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> RefundPreview:
    """Put the latest applied refund of one order back: the statement rows and the bills it changed."""
    _manage(context)
    with open_finance_store(context) as store:
        planning = store.get_planning(context)
        record = next((r for r in planning.installment_refunds if r.id == record_id), None)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund record not found.")
        if record.undone_at is not None:
            raise _conflict("This refund was already undone.")
        key = order_key(record.provider, record.order_id)
        if _latest_live(planning.installment_refunds).get(key) != record.id:
            raise _conflict("Undo the newer refund on this order first.")
        rows = {(order_key(r.provider, r.order_id), r.installment): i for i, r in enumerate(planning.installment_entries)}
        for change in record.changes:
            index = rows.get((key, change.installment))
            if index is not None:
                row = planning.installment_entries[index]
                planning.installment_entries[index] = row.model_copy(update={"refunded_amount": change.previous_refunded})
        now = utc_now()
        for change in record.bills:
            bill = store.get_item(context, "bill", change.bill_id)
            # A bill the person changed since is theirs now: leave it as they set it.
            if isinstance(bill, Bill) and bill.active == change.active and bill.amount == change.amount:
                store.save_item("bill", bill.model_copy(update={"active": change.previous_active, "amount": change.previous_amount, "updated_at": now}))
        index = planning.installment_refunds.index(record)
        planning.installment_refunds[index] = record.model_copy(update={"undone_at": now, "undone_by": context.user_id})
        store.save_planning(context, planning)
        preview = _preview(store, context, planning)
    _audit(repository, context, "finance.installment_refunds.undo", record_id, {"installments": len(record.changes), "bills": len(record.bills)})
    return preview
