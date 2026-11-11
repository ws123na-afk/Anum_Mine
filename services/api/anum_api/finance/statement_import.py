"""The statement matching engine over HTTP: preview views, the balance check and own transfers.

docs/finance.md#statement-matching. CSV (``api.py``) and PDF (``pdf_api.py``) previews show every row's match,
FX details and bill or transfer; ``api.record_import`` runs the same engine again when the rows are imported.
This module also serves the "Find transfers" pass over rows already in the ledger:
``GET /api/v1/finance/transfers/candidates`` (a preview, nothing saved) and ``POST /api/v1/finance/transfers``
(the pairs the person ticked). Both need ``finance:manage``; nothing pairs on its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from . import fx_text
from . import statement_ledger as SL
from . import statement_match as M
from .money import MoneyError, quantize

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAX_TRANSFER_DAYS = 400
MAX_PAIRS = 200


# -- views -----------------------------------------------------------------------------------------------


class MatchedRecordView(BaseModel):
    record_id: str
    canonical_id: str | None
    transaction_id: str | None
    account_id: str
    source: str  # "sms", "wallet", "manual", "statement"
    last4: str | None
    posted_on: date
    amount: Decimal
    currency: str
    description: str


class StatementBillView(BaseModel):
    bill_id: str
    name: str
    due_on: date
    expected: Decimal
    currency: str
    strength: Literal["strong", "weak"]
    reasons: list[str]


class TransferView(BaseModel):
    direction: Literal["to", "from"]
    account_id: str
    account_name: str
    account_last4: str | None
    strength: Literal["strong", "weak"]
    reasons: list[str]
    lag_days: int
    other: MatchedRecordView


class FxView(BaseModel):
    """The row's foreign currency details, as printed (or worked out from two printed amounts: ``implied``)."""

    original_amount: Decimal | None
    original_currency: str | None
    rate: Decimal | None
    rate_source: Literal["printed", "implied", "column"] | None
    fee: Decimal | None
    fee_currency: str | None
    markup_percent: Decimal | None
    expected: Decimal | None  # original x rate (+ the printed fee or markup when that is closer)
    difference: Decimal | None  # billed minus expected
    check: Literal["ties", "differs"] | None
    implied_markup_percent: Decimal | None  # billed over original x rate, less 100 %: computed, not printed


class RowMatchView(BaseModel):
    status: Literal["new", "exact_duplicate", "matched_existing", "possible_duplicate"]
    code: str
    reason: str
    record: MatchedRecordView | None
    candidates: list[MatchedRecordView]
    lag_days: int | None
    amount_difference: Decimal | None
    bill: StatementBillView | None
    transfer: TransferView | None
    default_import: bool
    # Groups of recorded payments that add up to this row exactly (docs/finance.md#one-row-for-several-recorded-payments).
    splits: list[list[MatchedRecordView]] = Field(default_factory=list)


class MatchCounts(BaseModel):
    new: int = 0
    exact_duplicate: int = 0
    matched_existing: int = 0
    possible_duplicate: int = 0
    bills: int = 0
    transfers: int = 0
    splits: int = 0  # rows that several recorded payments add up to


class ReconciliationView(BaseModel):
    status: Literal["tied", "differs", "unknown"]
    opening: Decimal | None
    closing: Decimal | None
    rows_total: Decimal
    expected_closing: Decimal | None
    difference: Decimal | None
    opening_source: str
    closing_source: str
    breaks: list[int]


def record_view(record: M.Recorded) -> MatchedRecordView:
    return MatchedRecordView(record_id=record.record_id, canonical_id=record.canonical_id, transaction_id=record.transaction_id,
                             account_id=record.account_id, source=record.primary, last4=record.last4, posted_on=record.posted_on,
                             amount=record.amount, currency=record.currency, description=record.description[:120])


def match_view(match: M.Match) -> RowMatchView:
    bill = match.bill
    transfer = match.transfer
    return RowMatchView(
        status=match.status, code=match.code, reason=M.explain(match),  # type: ignore[arg-type]
        record=record_view(match.record) if match.record else None, candidates=[record_view(c) for c in match.candidates],
        lag_days=match.lag_days, amount_difference=match.amount_difference,
        bill=StatementBillView(bill_id=bill.bill.bill_id, name=bill.bill.name, due_on=bill.bill.due_on, expected=bill.bill.amount,
                               currency=bill.bill.currency, strength=bill.strength, reasons=list(bill.reasons)) if bill else None,  # type: ignore[arg-type]
        transfer=TransferView(direction=transfer.direction, account_id=transfer.other_account.id, account_name=transfer.other_account.name,  # type: ignore[arg-type]
                              account_last4=transfer.other_account.last4, strength=transfer.strength, reasons=list(transfer.reasons),  # type: ignore[arg-type]
                              lag_days=transfer.lag_days, other=record_view(transfer.other)) if transfer else None,
        default_import=match.default_import, splits=[[record_view(r) for r in group] for group in match.splits],
    )


def counts(matches: list[M.Match]) -> MatchCounts:
    out = MatchCounts()
    for match in matches:
        setattr(out, match.status, getattr(out, match.status) + 1)
        out.bills += match.bill is not None
        out.transfers += match.transfer is not None
        out.splits += bool(match.splits)
    return out


def fx_view(description: str, billed: Decimal, currency: str, *, original_amount: Decimal | None = None, original_currency: str | None = None,
            rate: Decimal | None = None, fee: Decimal | None = None, fee_currency: str | None = None) -> FxView | None:
    """Columns win over the text; the text fills what the columns left empty. ``None`` when the row is not foreign."""
    read = fx_text.read_fx(description, currency)
    rate_source: str | None = "column" if rate is not None else None
    if original_amount is None and read.original_amount is not None:
        original_amount, original_currency = read.original_amount, read.original_currency
    if rate is None and read.rate is not None:
        rate, rate_source = read.rate, read.rate_source
    if fee is None and read.fee is not None:
        fee, fee_currency = read.fee, read.fee_currency
    if original_amount is None and rate is None:
        return None
    if rate is None and original_amount and original_currency and original_currency != currency and billed:
        rate, rate_source = (abs(billed) / abs(original_amount)).quantize(Decimal("0.000001")), "implied"
    result = fx_text.check(billed, abs(original_amount) if original_amount is not None else None, rate, currency, fee=fee,
                           fee_currency=fee_currency or currency, markup_percent=read.markup_percent)
    implied = None
    if original_amount and rate and rate_source != "implied":
        base = abs(original_amount) * rate
        if base > 0:
            implied = ((abs(billed) / base - 1) * 100).quantize(Decimal("0.01"))
    return FxView(
        original_amount=abs(original_amount) if original_amount is not None else None, original_currency=original_currency, rate=rate,
        rate_source=rate_source, fee=abs(fee) if fee is not None else None, fee_currency=(fee_currency or currency) if fee is not None else None,  # type: ignore[arg-type]
        markup_percent=read.markup_percent, expected=result.expected if result else None, difference=result.difference if result else None,
        check=result.status if result else None, implied_markup_percent=implied,  # type: ignore[arg-type]
    )


def reconcile(items: list[tuple[int, Decimal, Decimal | None]], opening: Decimal | None, closing: Decimal | None, currency: str) -> ReconciliationView:
    """Opening plus every statement row (new, already recorded or matched alike) against the closing balance. With a
    running balance column and no stated balances, the first and last rows stand in (``source: rows``). Rows listed
    newest first are put in date order first."""
    from . import statement_pdf as pdf

    check = pdf.verify(items, opening, closing)
    statuses = {"matched": "tied", "mismatch": "differs", "unknown": "unknown"}

    def q(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        try:
            return quantize(value, currency)
        except MoneyError:
            return value

    return ReconciliationView(
        status=statuses[check.status], opening=q(check.opening), closing=q(check.closing), rows_total=q(check.rows_total) or Decimal(0),  # type: ignore[arg-type]
        expected_closing=q(check.expected_closing), difference=q(check.difference), opening_source=check.opening_source,
        closing_source=check.closing_source, breaks=check.breaks[:50],
    )


def stored(view: ReconciliationView | None) -> dict[str, str | None] | None:
    if view is None:
        return None
    return {"status": view.status, "opening": _s(view.opening), "closing": _s(view.closing), "rows_total": _s(view.rows_total),
            "difference": _s(view.difference)}


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def chronological(items: list[tuple[int, Any, Decimal, Decimal | None]]) -> list[tuple[int, Decimal, Decimal | None]]:
    """(line, date, amount, balance) in the statement's own date order: a newest-first export is turned around."""
    if len(items) > 1 and items[0][1] > items[-1][1]:
        items = list(reversed(items))
    return [(line, amount, balance) for line, _, amount, balance in items]


# -- own transfers over the ledger -----------------------------------------------------------------------


class TransferPairView(BaseModel):
    out: MatchedRecordView
    into: MatchedRecordView
    strength: Literal["strong", "weak"]
    reasons: list[str]
    lag_days: int
    out_account_name: str
    into_account_name: str
    into_last4: str | None


class TransferCandidates(BaseModel):
    start: date
    end: date
    pairs: list[TransferPairView]
    strong: int
    weak: int


class TransferChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    out_record_id: str = Field(min_length=1, max_length=200)
    into_record_id: str = Field(min_length=1, max_length=200)


class TransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    pairs: list[TransferChoice] = Field(min_length=1, max_length=MAX_PAIRS)


class TransferResult(BaseModel):
    paired: int
    recategorized: int
    failed: list[str]


def _window(start: date | None, end: date | None) -> tuple[date, date]:
    from ..finance.api import _invalid

    end = end or utc_now().date()
    start = start or end - timedelta(days=90)
    if start > end or (end - start).days > MAX_TRANSFER_DAYS:
        raise _invalid(f"Pick a date range of at most {MAX_TRANSFER_DAYS} days.")
    return start, end


def _pairs(context: TenantContext, start: date, end: date) -> tuple[list[M.Pair], dict[str, M.AccountInfo]]:
    from .links_store import open_link_stores

    with open_link_stores(context) as (finance, sms, links):
        accounts = {a.id: SL.account_info(a) for a in finance.list_accounts(context) if not a.archived}
        paired = SL.paired_canonicals(links, context)
        found = SL.records(finance, sms, context, start, end + timedelta(days=M.TRANSFER_DAYS), paired=paired)
    pairs = [p for p in M.find_transfers(found, list(accounts.values())) if start <= p.out.posted_on <= end]
    return pairs, accounts


@router.get("/transfers/candidates", response_model=TransferCandidates)
def transfer_candidates(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    context: TenantContext = Depends(tenant_context),
) -> TransferCandidates:
    """Money out of one of your accounts and the same money into another, already in the ledger but not yet paired.
    A suggestion only: nothing changes until you pair them."""
    from .api import _manage

    _manage(context)
    start, end = _window(start, end)
    pairs, accounts = _pairs(context, start, end)
    views = [TransferPairView(out=record_view(p.out), into=record_view(p.into), strength=p.strength, reasons=list(p.reasons), lag_days=p.lag_days,  # type: ignore[arg-type]
                              out_account_name=accounts[p.out.account_id].name, into_account_name=accounts[p.into.account_id].name,
                              into_last4=accounts[p.into.account_id].last4) for p in pairs]
    return TransferCandidates(start=start, end=end, pairs=views[:MAX_PAIRS], strong=sum(p.strength == "strong" for p in pairs),
                              weak=sum(p.strength == "weak" for p in pairs))


@router.post("/transfers", response_model=TransferResult, status_code=status.HTTP_201_CREATED)
def pair_transfers(body: TransferInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> TransferResult:
    """Pair the transfers you ticked. Each pair is checked again; both sides become ``transfer`` (out of spend and
    income) and are linked as one transfer."""
    from .api import _audit, _manage
    from .links_store import open_link_stores
    from .sms_service import owner_lock

    _manage(context)
    start, end = _window(body.start, body.end)
    wanted = {(c.out_record_id, c.into_record_id) for c in body.pairs}
    jobs: list[SL.LinkJob] = []
    failed: list[str] = []
    recategorized = 0
    now = utc_now()
    with owner_lock(context), open_link_stores(context) as (finance, sms, links):
        sms.lock_owner(context)
        accounts = {a.id: SL.account_info(a) for a in finance.list_accounts(context) if not a.archived}
        paired = SL.paired_canonicals(links, context)
        found = SL.records(finance, sms, context, start, end + timedelta(days=M.TRANSFER_DAYS), paired=paired)
        by_id = {r.record_id: r for r in found}
        used: set[str] = set()
        for out_id, into_id in sorted(wanted):
            out, into = by_id.get(out_id), by_id.get(into_id)
            if out is None or into is None or out.transfer_paired or into.transfer_paired or {out_id, into_id} & used:
                failed.append("not_available")
                continue
            if out.account_id not in accounts or into.account_id not in accounts or out.canonical_id is None or into.canonical_id is None:
                failed.append("not_available")
                continue
            if M.transfer_pair(M.side_of(out), M.side_of(into), accounts[out.account_id], accounts[into.account_id]) is None:
                failed.append("not_a_transfer")
                continue
            used.update({out_id, into_id})
            # The owner picked this pair, so the rows are filed as the owner's choice (audit C4: only machines never overwrite).
            recategorized += (SL.recategorize_transfer(finance, sms, context, out, now=now, by_owner=True)
                              + SL.recategorize_transfer(finance, sms, context, into, now=now, by_owner=True))
            jobs.append(SL.LinkJob("transfer", out.canonical_id, into.canonical_id, -out.amount, out.currency))
    made = SL.make_links(context, jobs)
    failed.extend(made.failed)
    _audit(repository, context, "finance.transfer.pair", context.user_id, {"paired": made.transfers, "failed": len(failed)})
    return TransferResult(paired=made.transfers, recategorized=recategorized, failed=failed)


__all__ = [
    "FxView",
    "MatchCounts",
    "MatchedRecordView",
    "ReconciliationView",
    "RowMatchView",
    "chronological",
    "counts",
    "fx_view",
    "match_view",
    "reconcile",
    "router",
    "stored",
]
