"""Card FX details and separating an FX fee under ``/api/v1/finance`` (docs/finance.md#card-fx-and-fees).

* ``GET /transactions/{id}/fx`` (``finance:read``): the original amount, billed amount, rate (printed, from a column or
  implied, never guessed), its date and source, the FX fee or markup and where it comes from, and for an own transfer
  between accounts in two currencies the realised rate and, with an earlier rate on record, the realised FX difference.
* ``POST /transactions/{id}/fx/separate`` (``finance:manage``, with the ``plan_hash`` shown): for a fee stated on the purchase
  row, the purchase is corrected to the billed amount less the fee and the fee becomes its own ``bank_fee`` line (same
  account, day and statement), through the audited correction path, so the account's balance is unchanged; for a separate
  fee row, that row becomes a ``bank_fee`` that points at its purchase. Nothing changes without the owner.

Rows recorded from an iPhone SMS or Apple Pay are read here, but corrected only under Finance, iPhone SMS, Review. Each
route does its work in one short unit of work under the owner's lock; audit rows hold ids and codes only.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import card_fx as C
from . import txn_types as TT
from .api import _audit, _manage, _read
from .models import Category, CategorySource, Transaction, TransactionType, TypeSource

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
SMS_SOURCES = {"sms", "correction", "clearing"}


class FeeView(BaseModel):
    amount: Decimal
    currency: str
    source: str
    transaction_id: str | None
    markup_percent: Decimal | None
    included_in_billed: bool
    linked: bool


class TransferView(BaseModel):
    role: str  # "out" or "in": which side this row is
    other_transaction_id: str | None
    out_amount: Decimal
    out_currency: str
    in_amount: Decimal
    in_currency: str
    on: date
    realised_rate: Decimal
    valuation_rate: Decimal | None
    valuation_date: date | None
    valuation_source: str | None
    valued_at: Decimal | None
    difference: Decimal | None
    policy: str = "The difference is shown, never posted: it is neither spending nor income."


class FxView(BaseModel):
    transaction_id: str
    foreign: bool
    original_amount: Decimal | None
    original_currency: str | None
    billed_amount: Decimal | None
    billed_currency: str | None
    purchase_amount: Decimal | None
    rate: Decimal | None
    rate_kind: str
    rate_date: date | None
    rate_source: str | None
    fee: FeeView | None
    split: str
    notes: list[str]
    plan_hash: str
    can_correct: bool  # False for SMS and Apple Pay rows (corrected under iPhone SMS, Review)
    transfer: TransferView | None = None


def _row(t: Transaction, entry: Any) -> C.Row:
    source = "statement" if t.import_id else "manual"
    original = original_currency = rate = fee = None
    if entry is not None:
        original, original_currency = entry.original_amount, entry.original_currency  # also kept for a statement row's printed original
        if str(getattr(entry.source, "value", entry.source)) in SMS_SOURCES:
            source, rate, fee = "sms", entry.fx_rate, entry.fx_fee
    return C.Row(id=t.id, account_id=t.account_id, posted_on=t.posted_on, amount=t.amount, currency=t.currency, description=t.description,
                 notes=t.notes, source=source, original_amount=original, original_currency=original_currency, sms_rate=rate, sms_fee=fee,
                 transaction_type=t.transaction_type.value if t.transaction_type else None, related_transaction_id=t.related_transaction_id)


def _read_all(finance: Any, sms: Any, links: Any, context: TenantContext, transaction_id: str) -> tuple[Transaction, Any, C.Details, list[Transaction]]:
    t = finance.get_transaction(context, transaction_id)
    if t is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found. It may have been deleted.")
    entry = sms.entry_for_transaction(context, t.id)
    near = [n for n in finance.transactions_between(context, t.posted_on - timedelta(days=1), t.posted_on + timedelta(days=C.FEE_ROW_DAYS + 1))
            if n.account_id == t.account_id]
    rows = [_row(n, sms.entry_for_transaction(context, n.id) if n.id != t.id else entry) for n in near]
    return t, entry, C.details(_row(t, entry), rows), near


def _transfer(finance: Any, sms: Any, links: Any, context: TenantContext, t: Transaction, entry: Any) -> TransferView | None:
    from .commitments import TRANSFER_DIMENSION
    from .links import live_entry
    from .links_store import LinkStatus

    if entry is None:
        return None
    canonical = entry.canonical_id or entry.id
    out_rows, _ = links.list_links(context, canonical_id=canonical, dimension=TRANSFER_DIMENSION, target_type=None, target_id=None, status=None,
                                   limit=5, offset=0)
    in_rows, _ = links.list_links(context, canonical_id=None, dimension=TRANSFER_DIMENSION, target_type=None, target_id=canonical, status=None,
                                  limit=5, offset=0)
    link = next((l for l in out_rows + in_rows if l.status != LinkStatus.VOID), None)
    if link is None:
        return None
    role = "out" if link.canonical_id == canonical else "in"
    other = live_entry(sms.chain(context, link.target_id if role == "out" else link.canonical_id))
    if other is None:
        return None
    mine = next((p for p in entry.postings if p.account_id and p.transaction_id == t.id), None) or next((p for p in entry.postings if p.account_id), None)
    theirs = next((p for p in other.postings if p.account_id), None)
    if mine is None or theirs is None:
        return None
    out_p, in_p = (mine, theirs) if role == "out" else (theirs, mine)
    plan = finance.get_plan(context)
    rates = finance.list_fx_rates(context, limit=10_000)
    found = C.transfer_fx(abs(out_p.amount), out_p.currency, abs(in_p.amount), in_p.currency, max(entry.posted_on, other.posted_on), rates,
                          getattr(plan, "fx_max_age_days", 7) if plan is not None else 7)
    if found is None:
        return None
    return TransferView(role=role, other_transaction_id=theirs.transaction_id, out_amount=found.out_amount, out_currency=found.out_currency,
                        in_amount=found.in_amount, in_currency=found.in_currency, on=found.on, realised_rate=found.realised_rate,
                        valuation_rate=found.valuation_rate, valuation_date=found.valuation_date, valuation_source=found.valuation_source,
                        valued_at=found.valued_at, difference=found.difference)


def _view(found: C.Details, entry: Any, transfer: TransferView | None) -> FxView:
    fee = found.fee
    source = str(getattr(entry.source, "value", entry.source)) if entry is not None else "manual"
    return FxView(
        transaction_id=found.transaction_id, foreign=found.foreign, original_amount=found.original_amount, original_currency=found.original_currency,
        billed_amount=found.billed_amount, billed_currency=found.billed_currency, purchase_amount=found.purchase_amount, rate=found.rate,
        rate_kind=found.rate_kind, rate_date=found.rate_date, rate_source=found.rate_source,
        fee=FeeView(amount=fee.amount, currency=fee.currency, source=fee.source, transaction_id=fee.transaction_id, markup_percent=fee.markup_percent,
                    included_in_billed=fee.included_in_billed, linked=fee.linked) if fee else None,
        split=found.split, notes=found.notes, plan_hash=C.plan_hash(found), can_correct=source not in SMS_SOURCES, transfer=transfer,
    )


@router.get("/transactions/{transaction_id}/fx", response_model=FxView)
def transaction_fx(transaction_id: str, context: TenantContext = Depends(tenant_context)) -> FxView:
    _read(context)
    from .links_store import open_link_stores

    with open_link_stores(context) as (finance, sms, links):
        t, entry, found, _near = _read_all(finance, sms, links, context, transaction_id)
        transfer = _transfer(finance, sms, links, context, t, entry)
    return _view(found, entry, transfer)


class SeparateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_hash: str = Field(min_length=64, max_length=64)


class SeparateResult(BaseModel):
    purchase: FxView
    fee_transaction_id: str
    mode: str  # "split" (a new bank_fee line) or "link" (the separate fee row now points at its purchase)


def _note(existing: str | None, text: str) -> str:
    return " | ".join(p for p in ((existing or "").strip(), text) if p)[:1000]


@router.post("/transactions/{transaction_id}/fx/separate", response_model=SeparateResult)
def separate_fee(transaction_id: str, body: SeparateInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> SeparateResult:
    """Make the FX fee its own ``bank_fee`` line, as shown (``plan_hash``). Never changes the account's balance."""
    _manage(context)
    from . import csv_import
    from .ledger import EntrySource
    from .links_store import open_link_stores
    from .sms_service import owner_lock, record_plain, repost_plain

    with owner_lock(context), open_link_stores(context) as (finance, sms, links):
        sms.lock_owner(context)
        t, entry, found, near = _read_all(finance, sms, links, context, transaction_id)
        if C.plan_hash(found) != body.plan_hash:
            raise HTTPException(status_code=409, detail="The figures changed since you looked at them. Look again, then separate the fee.")
        if found.fee is None or found.split not in ("available", "link"):
            raise HTTPException(status_code=409, detail="There is no FX fee to separate on this transaction.")
        account = finance.get_account(context, t.account_id)
        kind = account.kind if account is not None else None
        now = utc_now()
        if found.split == "link":
            fee_row = next((n for n in near if n.id == found.fee.transaction_id), None)
            if fee_row is None:
                raise HTTPException(status_code=409, detail="The fee row is no longer there. Reload the transaction.")
            fee_entry = sms.entry_for_transaction(context, fee_row.id)
            if fee_entry is not None and str(getattr(fee_entry.source, "value", fee_entry.source)) in SMS_SOURCES:
                raise HTTPException(status_code=409, detail="An iPhone SMS recorded the fee row. Correct it under Finance, iPhone SMS, Review.")
            TT.fill(fee_row, kind)
            before = (fee_row.category, fee_row.transaction_type)
            fee_row.category, fee_row.category_source = Category.FEES, CategorySource.MANUAL
            TT.apply(fee_row, TT.Typed(TransactionType.BANK_FEE, TypeSource.OWNER, Decimal(1), "fx_fee_linked"))
            fee_row.related_transaction_id, fee_row.updated_at = t.id, now
            finance.save_transaction(fee_row)
            if fee_entry is not None and (fee_row.category, fee_row.transaction_type) != before:
                repost_plain(finance, sms, context, fee_entry, fee_row, now=now)
            fee_id, mode = fee_row.id, "link"
        else:
            if entry is not None and str(getattr(entry.source, "value", entry.source)) not in (EntrySource.MANUAL.value, EntrySource.STATEMENT.value):
                raise HTTPException(status_code=409, detail="An iPhone SMS or Apple Pay recorded this transaction. Correct it under Finance, iPhone SMS, Review.")
            fee = found.fee.amount
            if account is None or fee <= 0 or fee >= abs(t.amount):
                raise HTTPException(status_code=409, detail="The fee cannot be separated from this amount.")
            sign = Decimal(-1) if t.amount < 0 else Decimal(1)
            t.amount = sign * (abs(t.amount) - fee)
            t.notes = _note(t.notes, f"FX fee separated: {fee} {t.currency}")
            t.updated_at = now
            finance.save_transaction(t)
            if entry is not None:
                repost_plain(finance, sms, context, entry, t, now=now)
            line = Transaction(
                id=new_id("fin_txn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                account_id=t.account_id, posted_on=t.posted_on, amount=sign * fee, currency=t.currency, description=f"FX fee: {t.description}"[:500],
                category=Category.FEES, category_source=CategorySource.MANUAL, notes="Separated from its purchase by the owner",
                import_id=t.import_id, dedupe_hash=csv_import.dedupe_hash(t.account_id, t.posted_on, sign * fee, t.currency, f"fxfee {t.id}", 0),
                created_at=now, updated_at=now, related_transaction_id=t.id,
            )
            TT.apply(line, TT.Typed(TransactionType.BANK_FEE, TypeSource.OWNER, Decimal(1), "fx_fee_split"))
            finance.save_transaction(line)
            record_plain(sms, context, line, account, EntrySource.STATEMENT if t.import_id else EntrySource.MANUAL, now=now)
            fee_id, mode = line.id, "split"
        t2, entry2, after, _ = _read_all(finance, sms, links, context, transaction_id)
        transfer = _transfer(finance, sms, links, context, t2, entry2)
    _audit(repository, context, "finance.transaction.fx_fee_separate", transaction_id, {"mode": mode, "fee_transaction": fee_id})
    return SeparateResult(purchase=_view(after, entry2, transfer), fee_transaction_id=fee_id, mode=mode)


__all__ = ["router"]
