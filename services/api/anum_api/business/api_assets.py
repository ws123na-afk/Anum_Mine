"""HTTP routes of the fixed asset register, loan schedules, period-end revaluation and the group balance sheet under
``/api/v1/business/books`` (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation,
#group-balance-sheet).

Who can do what:

* ``business:read`` (owners and members): the register and its roll-forward, each asset's schedule, the loans with their
  schedules and repayments, the revaluation preview.
* Owners and accountants (``api_ledger.require_accounting``): an asset's depreciation settings, disposals, impairment
  requests and decisions (never on one's own request: maker and checker), loans and repayments and voiding them,
  revaluing on demand.
* Owners only (``business:manage`` here and in each business shown): the group balance sheet.

Refusals answer ``{"detail": "<plain words>", "code": "<code>"}``. Audit rows hold ids, codes, dates and figures, never a
lender's name, a description or a reason. Every route is a plain ``def``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import books_assets as A
from . import books_group_bs as GB
from . import books_ledger as L
from . import books_loans as LN
from . import books_register as REG
from . import books_report as R
from . import books_revalue as RV
from . import books_service as S
from .api_books import _audit, _csv, _found, _manage, _page, _read, _stale, _today, refusals
from .api_books_more import _candidates, _has_books
from .api_ledger import _ledger, _period, can_account, require_accounting
from .books_models import AdjustmentKind, AssetImpairment, BillStatus, DepreciationMethod, Loan, LoanMethod, LoanRepayment, Section
from .books_store import MAX_PAGE, MAX_SCAN, AtMost, DuplicateError, open_books_store

router = APIRouter(prefix="/api/v1/business/books", tags=["business"])

MAKER_CHECKER = "Another owner or accountant must approve an impairment: not the person who asked for it."
MAX_GROUP = 20


def _pct(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise S.BooksError(422, "Enter the rate as a number, for example 20.", "invalid_rate") from None
    if not number.is_finite() or number <= 0 or number > 100:
        raise S.BooksError(422, "The rate is a percentage above 0 and at most 100.", "invalid_rate")
    return number.quantize(Decimal("0.0001"))


def _locked(store: Any) -> date | None:
    return L.locked_through(store)


# -- the fixed asset register ---------------------------------------------------------------------------------------------


@router.get("/assets")
@refusals
@_ledger
def list_assets(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                status_filter: Literal["active", "disposed", "all"] = Query(default="all", alias="status"), q: str | None = Query(default=None, max_length=80),
                limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                context: TenantContext = Depends(tenant_context)) -> Any:
    """The register on the period's last day (net book values) and the roll-forward of the period, per currency. Opening it
    registers any capital bill or capital item not registered yet."""
    _read(context)
    first, last = _period(context, start, end)
    now = utc_now()
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        REG.sync(store, context, now, "system:books")
        assets, _ = store.find("assets", order_by="acquired_on", limit=MAX_SCAN)
        impairments, _ = store.find("asset_impairments", limit=MAX_SCAN)
        live = {asset.id: REG.source_live(store, asset) for asset in assets}
        locked = _locked(store)
    rows = [asset for asset in assets if status_filter == "all" or asset.status == status_filter]
    if q:
        needle = q.casefold()
        rows = [asset for asset in rows if needle in asset.name.casefold() or needle in asset.category.casefold()]
    approved = [row for row in impairments if row.status == "approved"]
    day = min(last, _today())
    items = [REG.asset_view(asset, impairments, day, source_live=live[asset.id]) for asset in rows[offset:offset + limit]]
    return {**_page(items, len(rows), limit, offset), "period": {"from": first.isoformat(), "to": last.isoformat()}, "as_of": day.isoformat(),
            "roll_forward": REG.roll_forward_view([asset for asset in assets if live[asset.id]], approved, first, last),
            "base_currency": settings.base_currency, "closed_through": locked.isoformat() if locked else None, "can_edit": can_account(context),
            "pending_impairments": sum(1 for row in impairments if row.status == "pending")}


@router.get("/assets.csv")
@refusals
@_ledger
def export_assets(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                  context: TenantContext = Depends(tenant_context)) -> Response:
    data = list_assets(start=start, end=end, status_filter="all", q=None, limit=MAX_PAGE, offset=0, context=context)
    rows: list[list[Any]] = [["section", "name", "category", "acquired_on", "currency", "cost", "method", "useful_life_months", "residual_value",
                              "accumulated_depreciation", "impairment", "net_book_value", "status"]]
    for item in data["items"]:
        rows.append(["register", item["name"], item["category"], item["acquired_on"], item["currency"], item["cost"], item["method"],
                     item["useful_life_months"] or "", item["residual_value"], item["accumulated_depreciation"], item["impairment"],
                     item["net_book_value"], item["status"]])
    for item in data["roll_forward"]:
        for key in ("opening_cost", "additions", "disposals_cost", "closing_cost", "opening_depreciation", "depreciation", "impairment",
                    "disposals_depreciation", "closing_depreciation", "opening_nbv", "closing_nbv"):
            rows.append(["roll_forward", key, "", "", item["currency"], item[key], "", "", "", "", "", "", ""])
    return _csv(rows, f"fixed-assets-{data['period']['from']}-{data['period']['to']}.csv")


@router.get("/assets/{asset_id}")
@refusals
@_ledger
def get_asset(asset_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    today = _today()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        asset = _found(store, "assets", asset_id, "Asset")
        impairments, _ = store.find("asset_impairments", where={"asset_id": asset_id}, order_by="entry_date", limit=MAX_SCAN)
        live = REG.source_live(store, asset)
        locked = _locked(store)
    sold = A.disposal(asset, impairments)
    return {**REG.asset_view(asset, impairments, today, source_live=live), "schedule": REG.schedule_view(asset, impairments, today, locked),
            "impairments": [REG.impairment_view(row) for row in impairments], "can_edit": can_account(context),
            "disposal": {key: str(value) for key, value in sold.items()} if sold else None,
            "closed_through": locked.isoformat() if locked else None, "me": context.user_id}


class AssetUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(default="equipment", min_length=2, max_length=40)
    useful_life_months: int | None = Field(default=None, ge=1, le=600)
    residual_value: str = Field(default="0", max_length=30)
    method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    rate_pct: str | None = Field(default=None, max_length=12)
    depreciation_start: date
    version: int


def _same_through(old: Any, new: Any, impairments: list[Any], locked: date | None) -> bool:
    """Whether the depreciation of the closed months stays the same."""
    if locked is None:
        return True
    before = [(row.month, row.amount) for row in A.schedule(old, impairments) if row.month <= locked]
    after = [(row.month, row.amount) for row in A.schedule(new, impairments) if row.month <= locked]
    return before == after


@router.patch("/assets/{asset_id}")
@refusals
@_ledger
def update_asset(asset_id: str, payload: AssetUpdate, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    """How an asset is depreciated: owners and accountants. Changing the depreciation of a closed month needs the month reopened."""
    require_accounting(context)
    now = utc_now()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        current = _found(store, "assets", asset_id, "Asset", for_update=True)
        if current.version != payload.version:
            raise _stale()
        if current.status == "disposed":
            raise S.BooksError(409, "This asset is disposed of: its depreciation is final.", "asset_disposed")
        residual = S.amount_of(payload.residual_value, current.currency, field="residual_value", allow_zero=True)
        if residual >= current.cost:
            raise S.BooksError(422, "The residual value is less than the cost.", "invalid_residual")
        rate = _pct(payload.rate_pct)
        if payload.method == DepreciationMethod.REDUCING_BALANCE and rate is None:
            raise S.BooksError(422, "Enter the yearly rate of the reducing balance method, for example 25.", "rate_required")
        start = R.month_start(payload.depreciation_start)
        if start < R.month_start(current.acquired_on):
            raise S.BooksError(422, "Depreciation starts in the month the asset was acquired or later.", "start_before_acquisition")
        item = current.model_copy(update={"name": S.clean(payload.name, 120), "category": S.clean(payload.category, 40) or "equipment",
                                          "useful_life_months": payload.useful_life_months, "residual_value": residual, "method": payload.method,
                                          "rate_pct": rate if payload.method == DepreciationMethod.REDUCING_BALANCE else None,
                                          "depreciation_start": start, "version": current.version + 1, "updated_at": now})
        impairments, _ = store.find("asset_impairments", where={"asset_id": asset_id}, limit=MAX_SCAN)
        locked = _locked(store)
        if not _same_through(current, item, impairments, locked):
            raise S.BooksError(409, f"This changes depreciation already in a closed period (closed through {locked.isoformat()}). "  # type: ignore[union-attr]
                               "An owner can reopen the period with a reason.", "period_locked", locked_through=locked.isoformat())  # type: ignore[union-attr]
        if not store.save("assets", item, expected_version=current.version):
            raise _stale()
    _audit(repository, context, "asset_updated", asset_id, {"before": {"method": str(current.method), "life": current.useful_life_months or 0,
                                                                       "residual": str(current.residual_value)},
                                                            "after": {"method": str(item.method), "life": item.useful_life_months or 0,
                                                                      "residual": str(item.residual_value)}})
    L.post_quietly(context)
    return get_asset(asset_id, context=context)


class DisposeInput(BaseModel):
    disposed_on: date
    kind: Literal["sale", "write_off"]
    proceeds: str = Field(default="0", max_length=30)
    version: int


@router.post("/assets/{asset_id}/dispose")
@refusals
@_ledger
def dispose_asset(asset_id: str, payload: DisposeInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    """Sell or write off an asset: the cost and its depreciation leave the books and the gain or loss is posted."""
    require_accounting(context)
    today = _today()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        current = _found(store, "assets", asset_id, "Asset", for_update=True)
        if current.version != payload.version:
            raise _stale()
        if current.status == "disposed":
            raise S.BooksError(409, "This asset is already disposed of.", "asset_disposed")
        if not REG.source_live(store, current):
            raise S.BooksError(409, "The bill or entry that bought this asset is voided: it is not in the books.", "source_voided")
        S.not_future(payload.disposed_on, today, "disposal date")
        if payload.disposed_on < current.acquired_on:
            raise S.BooksError(422, "An asset is disposed of after it was acquired.", "disposal_before_acquisition")
        L.require_open(store, payload.disposed_on, what="The disposal")
        proceeds = S.amount_of(payload.proceeds, current.currency, field="proceeds", allow_zero=True) if payload.kind == "sale" else Decimal(0)
        if payload.kind == "sale" and proceeds <= 0:
            raise S.BooksError(422, "Enter what the asset was sold for, or choose write-off.", "invalid_amount", field="proceeds")
        item = current.model_copy(update={"status": "disposed", "disposed_on": payload.disposed_on, "disposal_kind": payload.kind, "proceeds": proceeds,
                                          "version": current.version + 1, "updated_at": utc_now()})
        if not store.save("assets", item, expected_version=current.version):
            raise _stale()
    _audit(repository, context, "asset_disposed", asset_id, {"disposed_on": payload.disposed_on.isoformat(), "kind": payload.kind,
                                                             "proceeds": str(proceeds), "currency": current.currency})
    L.post_quietly(context)
    return get_asset(asset_id, context=context)


class ImpairmentInput(BaseModel):
    entry_date: date
    amount: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=5, max_length=300)


@router.post("/assets/{asset_id}/impairments", status_code=status.HTTP_201_CREATED)
@refusals
@_ledger
def request_impairment(asset_id: str, payload: ImpairmentInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    """Ask for an impairment (owners and accountants). It posts only once another owner or accountant approved it."""
    require_accounting(context)
    today = _today()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        asset = _found(store, "assets", asset_id, "Asset", for_update=True)
        if asset.status == "disposed":
            raise S.BooksError(409, "This asset is already disposed of.", "asset_disposed")
        S.not_future(payload.entry_date, today, "impairment date")
        if payload.entry_date < asset.acquired_on:
            raise S.BooksError(422, "An impairment is dated after the asset was acquired.", "impairment_before_acquisition")
        L.require_open(store, payload.entry_date, what="The impairment")
        amount = S.amount_of(payload.amount, asset.currency)
        impairments, _ = store.find("asset_impairments", where={"asset_id": asset_id}, limit=MAX_SCAN)
        value = A.net_book_value(asset, impairments, payload.entry_date)
        if amount > value:
            raise S.BooksError(422, f"An impairment is at most the net book value on that day ({value} {asset.currency}).", "more_than_value",
                               remaining=str(value))
        reason = S.clean(payload.reason, 300)
        if len(reason) < 5:
            raise S.BooksError(422, "Say why the asset is impaired (it stays in the trail).", "reason_required")
        row = AssetImpairment(id=new_id("bimp"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=utc_now(), asset_id=asset_id,
                              entry_date=payload.entry_date, amount=amount, currency=asset.currency, reason=reason, requested_by=context.user_id)
        store.add("asset_impairments", row)
    _audit(repository, context, "impairment_requested", row.id, {"asset_id": asset_id, "entry_date": row.entry_date.isoformat(), "amount": str(amount),
                                                                  "currency": asset.currency})
    return REG.impairment_view(row)


class DecisionInput(BaseModel):
    version: int


def _decide(asset_id: str, impairment_id: str, payload: DecisionInput, context: TenantContext, repository: AnumRepository, decision: str) -> Any:
    require_accounting(context)
    now = utc_now()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        asset = _found(store, "assets", asset_id, "Asset", for_update=True)
        row = _found(store, "asset_impairments", impairment_id, "Impairment", for_update=True)
        if row.asset_id != asset_id:
            raise S.BooksError(404, "Impairment not found.", "impairment_not_found")
        if row.version != payload.version:
            raise _stale()
        if row.status != "pending":
            raise S.BooksError(409, "This impairment is already decided.", "already_decided")
        if row.requested_by == context.user_id:
            raise S.BooksError(403, MAKER_CHECKER, "same_person")
        if decision == "approved":
            if asset.status == "disposed":
                raise S.BooksError(409, "This asset is already disposed of.", "asset_disposed")
            L.require_open(store, row.entry_date, what="The impairment")
            others, _ = store.find("asset_impairments", where={"asset_id": asset_id}, limit=MAX_SCAN)
            value = A.net_book_value(asset, others, row.entry_date)
            if row.amount > value:
                raise S.BooksError(422, f"An impairment is at most the net book value on that day ({value} {asset.currency}).", "more_than_value",
                                   remaining=str(value))
        item = row.model_copy(update={"status": decision, "decided_by": context.user_id, "decided_at": now, "version": row.version + 1})
        if not store.save("asset_impairments", item, expected_version=row.version):
            raise _stale()
    _audit(repository, context, f"impairment_{decision}", impairment_id, {"asset_id": asset_id, "entry_date": row.entry_date.isoformat(),
                                                                          "amount": str(row.amount), "currency": row.currency})
    if decision == "approved":
        L.post_quietly(context)
    return REG.impairment_view(item)


@router.post("/assets/{asset_id}/impairments/{impairment_id}/approve")
@refusals
@_ledger
def approve_impairment(asset_id: str, impairment_id: str, payload: DecisionInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    return _decide(asset_id, impairment_id, payload, context, repository, "approved")


@router.post("/assets/{asset_id}/impairments/{impairment_id}/reject")
@refusals
@_ledger
def reject_impairment(asset_id: str, impairment_id: str, payload: DecisionInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Any:
    return _decide(asset_id, impairment_id, payload, context, repository, "rejected")


# -- loans ---------------------------------------------------------------------------------------------------------------------


@router.get("/loans")
@refusals
@_ledger
def list_loans(status_filter: Literal["active", "void", "all"] = Query(default="active", alias="status"),
               limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
               context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    today = _today()
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        where = {} if status_filter == "all" else {"status": status_filter}
        loans, total = store.find("loans", where=where, order_by="start_date", limit=limit, offset=offset)
        ids = {loan.id for loan in loans}
        repayments, _ = store.find("loan_repayments", where={"loan_id": ids}, limit=MAX_SCAN) if ids else ([], 0)
        candidates, _ = store.find("adjustments", where={"kind": {AdjustmentKind.LOAN_RECEIVED.value, AdjustmentKind.LOAN_REPAYMENT.value},
                                                         "status": "active"}, order_by="entry_date", limit=200)
        bills, _ = store.find("bills", where={"status": BillStatus.OPEN.value, "tax": Decimal(0)}, order_by="bill_date", limit=200)
        used_bills, _ = store.find("loan_repayments", where={"status": "active"}, limit=MAX_SCAN)
    taken_bills = {row.bill_id for row in used_bills if row.bill_id}
    taken_adjustments = {row.adjustment_id for row in used_bills if row.adjustment_id}
    return {**_page([REG.loan_view(loan, repayments, today) for loan in loans], total, limit, offset), "base_currency": settings.base_currency,
            "can_edit": can_account(context),
            "match": {
                "received": [_adjustment_option(row) for row in candidates if row.kind == AdjustmentKind.LOAN_RECEIVED],
                "repayments": [_adjustment_option(row) for row in candidates if row.kind == AdjustmentKind.LOAN_REPAYMENT and row.id not in taken_adjustments],
                "bills": [{"id": row.id, "date": row.bill_date.isoformat(), "amount": str(row.total), "currency": row.currency, "reference": row.reference,
                           "description": row.description} for row in bills if row.section != Section.CAPITAL and row.id not in taken_bills],
            }}


def _adjustment_option(row: Any) -> dict[str, str]:
    return {"id": row.id, "date": row.entry_date.isoformat(), "amount": str(row.amount), "currency": row.currency, "description": row.description}


class LoanInput(BaseModel):
    lender: str = Field(min_length=2, max_length=120)
    principal: str = Field(min_length=1, max_length=30)
    currency: str = Field(min_length=3, max_length=3)
    annual_rate: str = Field(min_length=1, max_length=12)
    start_date: date
    term_months: int = Field(ge=1, le=600)
    frequency: Literal["monthly", "quarterly", "yearly"] = "monthly"
    method: LoanMethod = LoanMethod.ANNUITY
    early_repayment: Literal["reduce_term", "reduce_payment"] = "reduce_term"
    received_adjustment_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


def _rate(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise S.BooksError(422, "Enter the yearly interest rate as a number, for example 6.5.", "invalid_rate") from None
    if not number.is_finite() or number < 0 or number > 100:
        raise S.BooksError(422, "The yearly interest rate is a percentage from 0 to 100.", "invalid_rate")
    return number.quantize(Decimal("0.0001"))


@router.post("/loans", status_code=status.HTTP_201_CREATED)
@refusals
@_ledger
def create_loan(payload: LoanInput, response: Response, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    """A loan the business received, with its schedule: owners and accountants."""
    require_accounting(context)
    today = _today()
    now = utc_now()
    currency = S.currency_of(payload.currency)
    principal = S.amount_of(payload.principal, currency, field="principal")
    rate = _rate(payload.annual_rate)
    S.not_future(payload.start_date, today, "start date")
    months = LN.PERIOD_MONTHS[payload.frequency]
    if payload.term_months % months:
        raise S.BooksError(422, f"The term is a whole number of {payload.frequency} payments ({months} months each).", "invalid_term")
    key = S.idem_hash(idempotency_key, "loan", payload.model_dump_json())
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        if key and (earlier := store.first("loans", idem_key=key)) is not None:
            response.status_code = status.HTTP_200_OK
            return {**REG.loan_view(earlier, [], today), "replayed": True}
        L.require_open(store, payload.start_date, what="The loan")
        matched = None
        if payload.received_adjustment_id:
            matched = store.get("adjustments", payload.received_adjustment_id)
            if matched is None or matched.kind != AdjustmentKind.LOAN_RECEIVED or matched.status != "active":
                raise S.BooksError(422, "Choose a loan received entry of this business.", "invalid_match")
            if (matched.currency, matched.amount, matched.entry_date) != (currency, principal, payload.start_date):
                raise S.BooksError(422, "The loan received entry has another amount, currency or date.", "match_differs")
            if currency != settings.base_currency:
                raise S.BooksError(422, "Matching an existing entry works for loans in the base currency.", "match_base_only")
        loan = Loan(id=new_id("bloan"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, lender=S.clean(payload.lender, 120),
                    principal=principal, currency=currency, annual_rate=rate, start_date=payload.start_date, term_months=payload.term_months,
                    frequency=payload.frequency, method=payload.method, early_repayment=payload.early_repayment,
                    received_adjustment_id=matched.id if matched else None, idem_key=key, created_by=context.user_id, updated_at=now)
        try:
            store.add("loans", loan)
        except DuplicateError as exc:
            raise S.BooksError(409, "That loan received entry already belongs to another loan.", "duplicate") from exc
    _audit(repository, context, "loan_created", loan.id, {"principal": str(principal), "currency": currency, "start_date": loan.start_date.isoformat(),
                                                          "term_months": loan.term_months, "method": loan.method.value, "matched": bool(matched)})
    L.post_quietly(context)
    return REG.loan_view(loan, [], today)


@router.get("/loans/{loan_id}")
@refusals
@_ledger
def get_loan(loan_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        loan = _found(store, "loans", loan_id, "Loan")
        repayments, _ = store.find("loan_repayments", where={"loan_id": loan_id}, limit=MAX_SCAN)
    return {**REG.loan_detail(loan, repayments, _today()), "can_edit": can_account(context)}


class RepaymentInput(BaseModel):
    paid_on: date
    amount: str = Field(min_length=1, max_length=30)
    bill_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    adjustment_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/loans/{loan_id}/repayments", status_code=status.HTTP_201_CREATED)
@refusals
@_ledger
def record_repayment(loan_id: str, payload: RepaymentInput, response: Response, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context),
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    """A repayment made: split into interest accrued since the last one and principal. Matched to the bill or the loan repayment
    adjustment that already records its cash, when there is one (never posted twice)."""
    require_accounting(context)
    today = _today()
    if payload.bill_id and payload.adjustment_id:
        raise S.BooksError(422, "Match a repayment to a bill or to an adjustment, not both.", "invalid_match")
    key = S.idem_hash(idempotency_key, "loan_repayment", loan_id, payload.model_dump_json())
    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        loan = _found(store, "loans", loan_id, "Loan", for_update=True)
        if key and (earlier := store.first("loan_repayments", loan_id=loan_id, idem_key=key)) is not None:
            response.status_code = status.HTTP_200_OK
            return {**REG.repayment_view(earlier), "replayed": True}
        if loan.status != "active":
            raise S.BooksError(409, "This loan is voided.", "loan_void")
        S.not_future(payload.paid_on, today, "repayment date")
        if payload.paid_on < loan.start_date:
            raise S.BooksError(422, "A repayment is made after the loan started.", "before_start")
        L.require_open(store, payload.paid_on, what="The repayment")
        amount = S.amount_of(payload.amount, loan.currency)
        repayments, _ = store.find("loan_repayments", where={"loan_id": loan_id}, limit=MAX_SCAN)
        live = LN.live(repayments, loan_id)
        if live and payload.paid_on < live[-1].paid_on:
            raise S.BooksError(422, f"Record repayments in date order: the last one is dated {live[-1].paid_on.isoformat()}.", "out_of_order",
                               last=live[-1].paid_on.isoformat())
        most = LN.most_payable(loan, repayments, payload.paid_on)
        if amount > most:
            raise S.BooksError(422, f"That is more than is owed on that day ({most} {loan.currency}).", "more_than_owed", remaining=str(most))
        if (payload.bill_id or payload.adjustment_id) and loan.currency != settings.base_currency:
            raise S.BooksError(422, "Matching an existing record works for loans in the base currency.", "match_base_only")
        if payload.bill_id:
            bill = store.get("bills", payload.bill_id)
            if bill is None or bill.status != BillStatus.OPEN or bill.section == Section.CAPITAL:
                raise S.BooksError(422, "Choose an open bill of this business.", "invalid_match")
            if (bill.currency, bill.total, bill.tax) != (loan.currency, amount, Decimal(0)) or bill.bill_date != payload.paid_on:
                raise S.BooksError(422, "The bill has another amount, currency or date, or carries tax.", "match_differs")
        if payload.adjustment_id:
            adjustment = store.get("adjustments", payload.adjustment_id)
            if adjustment is None or adjustment.kind != AdjustmentKind.LOAN_REPAYMENT or adjustment.status != "active":
                raise S.BooksError(422, "Choose a loan repayment entry of this business.", "invalid_match")
            if (adjustment.currency, adjustment.amount, adjustment.entry_date) != (loan.currency, amount, payload.paid_on):
                raise S.BooksError(422, "The loan repayment entry has another amount, currency or date.", "match_differs")
        state, _ = LN.replay(loan, repayments)
        interest, principal = LN.split_payment(loan, state, payload.paid_on, amount)
        row = LoanRepayment(id=new_id("brepay"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=utc_now(), loan_id=loan_id,
                            paid_on=payload.paid_on, amount=amount, currency=loan.currency, interest=interest, principal=principal,
                            bill_id=payload.bill_id, adjustment_id=payload.adjustment_id, idem_key=key, created_by=context.user_id)
        try:
            store.add("loan_repayments", row)
        except DuplicateError as exc:
            raise S.BooksError(409, "That bill or adjustment already matches another repayment.", "duplicate") from exc
    _audit(repository, context, "loan_repaid", row.id, {"loan_id": loan_id, "paid_on": row.paid_on.isoformat(), "amount": str(amount),
                                                        "interest": str(interest), "principal": str(principal), "currency": loan.currency,
                                                        "matched": "bill" if row.bill_id else ("adjustment" if row.adjustment_id else "none")})
    L.post_quietly(context)
    return REG.repayment_view(row)


class VoidInput(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/loans/{loan_id}/repayments/{repayment_id}/void")
@refusals
@_ledger
def void_repayment(loan_id: str, repayment_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    """Void the latest repayment of a loan (with a reason). An earlier one would change every later split: void from the latest."""
    require_accounting(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        row = _found(store, "loan_repayments", repayment_id, "Repayment", for_update=True)
        if row.loan_id != loan_id:
            raise S.BooksError(404, "Repayment not found.", "loan_repayment_not_found")
        if row.status != "active":
            raise S.BooksError(409, "This repayment is already voided.", "already_void")
        repayments, _ = store.find("loan_repayments", where={"loan_id": loan_id}, limit=MAX_SCAN)
        live = LN.live(repayments, loan_id)
        if live and live[-1].id != repayment_id:
            raise S.BooksError(409, "Void the latest repayment first: each repayment's split depends on the ones before it.", "not_latest")
        L.require_open(store, row.paid_on, what="The repayment")
        item = row.model_copy(update={"status": "void", "voided_by": context.user_id, "voided_at": utc_now(), "void_reason": S.clean(payload.reason, 300)})
        store.save("loan_repayments", item)
    _audit(repository, context, "loan_repayment_voided", repayment_id, {"loan_id": loan_id, "paid_on": row.paid_on.isoformat()})
    L.post_quietly(context)
    return REG.repayment_view(item)


@router.post("/loans/{loan_id}/void")
@refusals
@_ledger
def void_loan(loan_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> Any:
    """Void a loan recorded by mistake (owners only), once it has no repayments."""
    _manage(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        loan = _found(store, "loans", loan_id, "Loan", for_update=True)
        if loan.status != "active":
            raise S.BooksError(409, "This loan is already voided.", "already_void")
        repayments, _ = store.find("loan_repayments", where={"loan_id": loan_id, "status": "active"}, limit=1)
        if repayments:
            raise S.BooksError(409, "Void its repayments first.", "has_repayments")
        L.require_open(store, loan.start_date, what="The loan")
        item = loan.model_copy(update={"status": "void", "voided_by": context.user_id, "voided_at": utc_now(), "void_reason": S.clean(payload.reason, 300),
                                       "version": loan.version + 1, "updated_at": utc_now()})
        store.save("loans", item)
    _audit(repository, context, "loan_voided", loan_id, {"start_date": loan.start_date.isoformat()})
    L.post_quietly(context)
    return REG.loan_view(item, [], _today())


# -- period-end revaluation ------------------------------------------------------------------------------------------------------


@router.get("/ledger/revaluation")
@refusals
@_ledger
def revaluation_preview(period_end: date | None = None, context: TenantContext = Depends(tenant_context)) -> Any:
    """Monetary balances in other currencies on a month end, their closing rate (or that there is none) and the difference."""
    _read(context)
    today = _today()
    day = period_end or L.month_end(R.add_months(R.month_start(today), -1))
    if day != L.month_end(day):
        raise S.BooksError(422, "Choose the last day of a month.", "not_month_end")
    with open_books_store(context) as store:
        S.settings_or_409(store)
        locked = _locked(store)
    return {**RV.preview(context, day), "closed_through": locked.isoformat() if locked else None, "can_edit": can_account(context),
            "ended": day < today}


class RevalueInput(BaseModel):
    period_end: date


@router.post("/ledger/revaluation")
@refusals
@_ledger
def revalue(payload: RevalueInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """Revalue on an open month end that has passed (owners and accountants). A close does the same for every month it covers."""
    require_accounting(context)
    result = L.revalue(context, payload.period_end, today=_today(), now=utc_now(), actor=context.user_id)
    _audit(repository, context, "revalued", context.workspace_id, {"period_end": payload.period_end.isoformat(), "posted": result["posted"],
                                                                   "reversed": result["reversed"], "missing": len(result["missing"])})
    return result


# -- group balance sheet -------------------------------------------------------------------------------------------------------


def _member(context: TenantContext, workspace_id: str, name: str, day: date) -> GB.BsMember | None:
    scoped = context.model_copy(update={"workspace_id": workspace_id})
    with open_books_store(scoped) as store:
        settings = store.get_settings()
        if settings is None:
            return None
        read = L.reader(store)
        accounts = read.accounts
        cumulative = read.totals(day)
        equity_ids = {a.id for a in accounts if a.type.value in ("equity", "revenue", "expense")}
        ic_ids = {a.id for a in accounts if a.system_key in ("ic_receivable", "ic_payable")}
        equity_days = store.ledger_totals({"entry_date": AtMost(day), "account_id": equity_ids}, ("account_id", "entry_date")) if equity_ids else []
        intercompany = store.ledger_totals({"entry_date": AtMost(day), "account_id": ic_ids}, ("account_id", "counterparty_workspace_id")) if ic_ids else []
        rates, _ = store.find("rates", order_by="as_of", limit=RV.RATE_SCAN)
    pending = L.status(scoped, _today())
    notes = [] if pending["complete"] else ["not_posted"]
    return GB.BsMember(workspace_id=workspace_id, name=name, base=settings.base_currency, accounts=accounts, cumulative=cumulative,
                       equity_days=equity_days, intercompany=intercompany, rates=rates, max_age=settings.rate_max_age_days, complete=pending["complete"],
                       notes=notes)


@router.get("/group/balance-sheet")
@refusals
def group_balance_sheet(as_of: date | None = None, currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
                        workspace: list[str] | None = Query(default=None, max_length=200), context: TenantContext = Depends(tenant_context)) -> Any:
    """The consolidated balance sheet of the caller's businesses from their journals, with eliminations and a translation reserve."""
    _manage(context)
    candidates = _candidates(context)
    allowed = dict(candidates)
    if workspace:
        wanted = list(dict.fromkeys(workspace))
        if any(item not in allowed for item in wanted):
            raise S.BooksError(403, "You can combine only businesses where you are an owner.", "workspace_not_allowed")
        if len(wanted) > MAX_GROUP:
            raise S.BooksError(422, f"Choose at most {MAX_GROUP} businesses for one view.", "too_many_businesses", limit=MAX_GROUP)
        selected = [(item, allowed[item]) for item in wanted]
    else:
        selected = candidates[:MAX_GROUP]
    day = min(as_of or _today(), _today())
    members: list[GB.BsMember] = []
    skipped = []
    for workspace_id, name in selected:
        member = _member(context, workspace_id, name, day) if _has_books(context, workspace_id) else None
        if member is None:
            skipped.append({"workspace_id": workspace_id, "name": name, "reason": "books_not_set_up"})
            continue
        members.append(member)
    available = [{"workspace_id": w, "name": n} for w, n in candidates]
    if len(members) < 2:
        return {"enough": False, "skipped": skipped, "available": available, "as_of": day.isoformat()}
    anchor = next((m for m in members if m.workspace_id == context.workspace_id), members[0])
    presentation = S.currency_of(currency) if currency else anchor.base
    return {"enough": True, "skipped": skipped, "available": available, **GB.consolidate(members, presentation, day)}


__all__ = ["router"]
