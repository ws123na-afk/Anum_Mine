"""HTTP routes of the business books under ``/api/v1/business/books`` (docs/business-books.md#api).

Who can do what (checked here for every route):

* ``business:read`` (owners and members): the P&L, the dashboard, the lists and the exports.
* ``business:write`` (owners and members): vendors, bills and their payments, cost centres,
  budgets, how an allocation row is classified.
* ``business:manage`` (owners): the book settings, exchange rates, journal adjustments, voiding a
  bill or a payment, and intercompany entries (which also need ``business:manage`` in the other
  business).
* Viewers have no Business access: the answer says who can give it.

Nothing here posts to the Finance ledger or writes an invoice. Refusals answer
``{"detail": "<plain words>", "code": "<code>"}`` plus figures where they help. Audit rows hold ids
and codes only, never a vendor, an amount or a description. Every route is a plain ``def``.
"""

from __future__ import annotations

import csv
import functools
import io
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..finance import fx
from ..finance.csv_import import safe_cell
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import books_ledger as L
from . import books_report as R
from . import books_service as S
from .books_models import (
    AdjustmentKind,
    AllocationTag,
    Basis,
    Bill,
    BillStatus,
    BookSettings,
    Budget,
    CostCentre,
    Direction,
    IntercompanyKind,
    PaymentKind,
    PaymentMethod,
    Rate,
    RateOrigin,
    Recurrence,
    Section,
    Vendor,
    dump,
)
from .books_store import MAX_PAGE, MAX_SCAN, BooksStore, DuplicateError, PeriodLocked, Unbalanced, open_books_store

router = APIRouter(prefix="/api/v1/business/books", tags=["business"])

READ_DENIED = "The business books are for workspace owners and members. Ask a workspace owner to make you a member."
WRITE_DENIED = "Only workspace owners and members can change the books."
MANAGE_DENIED = "Only workspace owners can do this (settings, exchange rates, adjustments, voiding and intercompany entries)."
MAX_SPAN_DAYS = 3 * 366
Amount = Field(min_length=1, max_length=30)
OptionalId = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


def refusals(route: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except S.BooksError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})
        except DuplicateError as exc:
            return JSONResponse(status_code=409, content={"detail": _duplicate(str(exc)), "code": "duplicate"})
        except L.LedgerError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code, **exc.extra})
        except PeriodLocked:
            return JSONResponse(status_code=409, content={"detail": "This is dated in a closed period. An owner can reopen the period with a reason.",
                                                          "code": "period_locked"})
        except Unbalanced:
            return JSONResponse(status_code=422, content={"detail": "Debits and credits must be equal, on two lines or more.", "code": "unbalanced"})

    return wrapper


def _duplicate(text: str) -> str:
    if "tags" in text:
        return "This allocation was classified in another tab at the same time. Reload and try again."
    if "allocation" in text:
        return "That allocation already pays another bill."
    if "name" in text:
        return "A vendor with this name already exists."
    if "code" in text:
        return "A cost centre with this code already exists."
    if "day" in text:
        return "A rate for this pair and day already exists. Remove it first to record another."
    return "This was already recorded. Reload the page."


def _need(context: TenantContext, permission: Permission, message: str) -> None:
    if not has_permission(context, permission):
        raise S.BooksError(403, message, "forbidden")


def _read(context: TenantContext) -> None:
    _need(context, Permission.BUSINESS_READ, READ_DENIED)


def _write(context: TenantContext) -> None:
    _read(context)
    _need(context, Permission.BUSINESS_WRITE, WRITE_DENIED)


def _manage(context: TenantContext) -> None:
    _read(context)
    _need(context, Permission.BUSINESS_MANAGE, MANAGE_DENIED)


ACCOUNTING_DENIED = "Only an owner or an accountant of this business can do this. An owner can name accountants in Books, Settings."


def _accountant(context: TenantContext) -> bool:
    if not has_permission(context, Permission.BUSINESS_WRITE):
        return False
    with open_books_store(context) as store:
        return store.first("accountants", user_id=context.user_id) is not None


def _accounting(context: TenantContext) -> None:
    """Owners, and members an owner named accountant (adjustments, approving draft bills, the journal and closing periods)."""
    _read(context)
    if not (has_permission(context, Permission.BUSINESS_MANAGE) or _accountant(context)):
        raise S.BooksError(403, ACCOUNTING_DENIED, "forbidden")


def _summary(record: Any, *names: str) -> dict[str, str]:
    """A before or after summary for the audit trail: figures, dates, codes and states, never a description, a note or a name."""
    out: dict[str, str] = {}
    for name in names:
        value = getattr(record, name, None)
        value = getattr(value, "value", value)
        out[name] = "" if value is None else str(value)
    return out


BILL_FIELDS = ("bill_date", "due_date", "currency", "subtotal", "tax", "total", "category", "section", "status", "project_id", "cost_centre_id",
               "useful_life_months")


def _changes(before: Any, after: Any, names: tuple[str, ...]) -> dict[str, Any]:
    old, new = _summary(before, *names), _summary(after, *names)
    changed = [name for name in names if old[name] != new[name]]
    return {"before": {name: old[name] for name in changed}, "after": {name: new[name] for name in changed}}


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    """Ids and codes only: never a vendor, an amount or a description."""
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=f"business.books.{action}", target=target, outcome="success", correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    ))


def _page(items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _today() -> date:
    return utc_now().date()


def _found(store: BooksStore, kind: str, item_id: str, label: str, *, for_update: bool = False) -> Any:
    found = store.get(kind, item_id, for_update=for_update)
    if found is None:
        raise S.BooksError(404, f"{label} not found.", f"{kind.rstrip('s')}_not_found")
    return found


def _stale() -> S.BooksError:
    return S.BooksError(409, "Someone changed this since you opened it. Reload and try again.", "stale")


def _csv(rows: list[list[Any]], filename: str) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([safe_cell(str(cell)) if cell is not None else "" for cell in row])
    return Response(content=out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


# -- setup and settings -----------------------------------------------------------------------------------


def _permissions(context: TenantContext) -> dict[str, bool]:
    manage = has_permission(context, Permission.BUSINESS_MANAGE)
    accountant = _accountant(context)
    return {"read": True, "write": has_permission(context, Permission.BUSINESS_WRITE), "manage": manage, "accountant": accountant,
            "account": manage or accountant}


@router.get("/setup")
@refusals
def setup(context: TenantContext = Depends(tenant_context)) -> Any:
    """The book settings (``null`` until an owner sets them), what the caller may do and the choices the forms offer."""
    _read(context)
    with open_books_store(context) as store:
        settings = store.get_settings()
        counts = {kind: store.find(kind, limit=1)[1] for kind in ("vendors", "bills", "cost_centres", "budgets", "rates", "adjustments", "intercompany")}
        centres, _ = store.find("cost_centres", where={"archived": False}, order_by="code", descending=False, limit=MAX_PAGE)
    allocations = S.allocations_by_id(context)
    period = R.default_period(settings, _today()) if settings else None
    with open_books_store(context) as store:
        locked = L.locked_through(store)
    return {
        "closed_through": locked.isoformat() if locked else None,
        "settings": dump(settings) if settings else None,
        "permissions": _permissions(context),
        "counts": {**counts, "allocations": len(allocations)},
        "default_period": {"from": period[0].isoformat(), "to": period[1].isoformat()} if period else None,
        "cost_centres": [dump(item) for item in centres],
        "allocation_categories": sorted({row.category for row in allocations.values()}),
        "sections": [item.value for item in Section],
        "adjustment_kinds": [item.value for item in AdjustmentKind],
    }


class SettingsInput(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)
    country: str | None = Field(default=None, max_length=2)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    basis: Basis = Basis.ACCRUAL
    tax_label: str = Field(default="", max_length=40)
    category_sections: dict[str, str] = Field(default_factory=dict)
    rate_max_age_days: int = Field(default=31, ge=1, le=366)
    policy_note: str = Field(default="", max_length=1000)
    version: int | None = None


SETTINGS_FIELDS = ("base_currency", "country", "fiscal_year_start_month", "basis", "rate_max_age_days")


@router.put("/settings")
@refusals
def put_settings(payload: SettingsInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    now = utc_now()
    with open_books_store(context) as store:
        current = store.get_settings()
        if (current.version if current else None) != payload.version:
            raise _stale()
        value = BookSettings(
            id=current.id if current else new_id("bset"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            created_at=current.created_at if current else now, base_currency=S.currency_of(payload.base_currency), country=S.country_of(payload.country),
            fiscal_year_start_month=payload.fiscal_year_start_month, basis=payload.basis, tax_label=S.clean(payload.tax_label, 40),
            category_sections=S.section_map(payload.category_sections), rate_max_age_days=payload.rate_max_age_days,
            policy_note=(payload.policy_note or "").strip()[:1000], version=(current.version + 1) if current else 1, updated_by=context.user_id,
            updated_at=now,
        )
        if current is not None:
            _frozen_settings(store, current, value)
        if not store.save_settings(value, expected_version=payload.version):
            raise _stale()
    _audit(repository, context, "settings_saved", value.id, {"basis": value.basis.value, **(_changes(current, value, SETTINGS_FIELDS) if current else {})})
    L.post_quietly(context)
    return dump(value)


def _frozen_settings(store: BooksStore, current: BookSettings, value: BookSettings) -> None:
    """Once a period is closed the base currency, the financial year and the basis stay (defect B6); once anything is posted the
    base currency stays (the journal is kept in it)."""
    if L.locked_through(store) is not None or store.find("closes", limit=1)[1]:
        for name, label in (("base_currency", "base currency"), ("fiscal_year_start_month", "first month of the financial year"), ("basis", "basis")):
            if getattr(current, name) != getattr(value, name):
                raise S.BooksError(409, f"A period has been closed, so the {label} stays as it is.", "period_closed", field=name)
    if current.base_currency != value.base_currency and store.find("journal_entries", limit=1)[1]:
        raise S.BooksError(409, f"The journal is kept in {current.base_currency}, so the base currency stays as it is.", "journal_exists",
                           field="base_currency")


# -- cost centres ------------------------------------------------------------------------------------------


class CostCentreInput(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=120)


class CostCentreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    archived: bool | None = None
    version: int


@router.get("/cost-centres")
@refusals
def list_cost_centres(archived: bool = False, limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                      context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("cost_centres", where={"archived": archived}, order_by="code", descending=False, limit=limit, offset=offset)
    return _page([dump(r) for r in rows], total, limit, offset)


@router.post("/cost-centres", status_code=status.HTTP_201_CREATED)
@refusals
def create_cost_centre(payload: CostCentreInput, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    now = utc_now()
    item = CostCentre(id=new_id("bcc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, code=payload.code,
                      name=S.clean(payload.name, 120), created_by=context.user_id, updated_at=now)
    with open_books_store(context) as store:
        store.add("cost_centres", item)
    _audit(repository, context, "cost_centre_created", item.id)
    return dump(item)


@router.patch("/cost-centres/{centre_id}")
@refusals
def update_cost_centre(centre_id: str, payload: CostCentreUpdate, context: TenantContext = Depends(tenant_context),
                       repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_books_store(context) as store:
        current = _found(store, "cost_centres", centre_id, "Cost centre")
        changes: dict[str, Any] = {"version": current.version + 1, "updated_at": utc_now()}
        if payload.name is not None:
            changes["name"] = S.clean(payload.name, 120)
        if payload.archived is not None:
            changes["archived"] = payload.archived
        item = current.model_copy(update=changes)
        if not store.save("cost_centres", item, expected_version=payload.version):
            raise _stale()
    _audit(repository, context, "cost_centre_updated", item.id)
    return dump(item)


# -- vendors -------------------------------------------------------------------------------------------------


class VendorInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    tax_number: str = Field(default="", max_length=60)
    country: str | None = Field(default=None, max_length=2)
    default_category: str | None = Field(default=None, max_length=40)
    default_section: Section | None = None
    notes: str = Field(default="", max_length=1000)


class VendorUpdate(VendorInput):
    archived: bool = False
    version: int


def _vendor_fields(payload: VendorInput) -> dict[str, Any]:
    name = S.clean(payload.name, 200)
    if len(name) < 2:
        raise S.BooksError(422, "Give the vendor's name.", "invalid_name")
    return {"name": name, "name_key": name.casefold(), "tax_number": S.clean(payload.tax_number, 60), "country": S.country_of(payload.country),
            "default_category": S.category_of(payload.default_category, required=False), "default_section": payload.default_section,
            "notes": (payload.notes or "").strip()[:1000]}


@router.get("/vendors")
@refusals
def list_vendors(q: str | None = Query(default=None, max_length=120), archived: bool = False, limit: int = Query(default=50, ge=1, le=MAX_PAGE),
                 offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("vendors", where={"archived": archived}, text=(q or "").strip() or None, text_fields=("name", "tax_number"),
                                 order_by="name_key", descending=False, limit=limit, offset=offset)
    return _page([dump(r, "name_key") for r in rows], total, limit, offset)


@router.post("/vendors", status_code=status.HTTP_201_CREATED)
@refusals
def create_vendor(payload: VendorInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    now = utc_now()
    item = Vendor(id=new_id("bven"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, created_by=context.user_id,
                  updated_at=now, **_vendor_fields(payload))
    with open_books_store(context) as store:
        store.add("vendors", item)
    _audit(repository, context, "vendor_created", item.id)
    return dump(item, "name_key")


@router.patch("/vendors/{vendor_id}")
@refusals
def update_vendor(vendor_id: str, payload: VendorUpdate, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_books_store(context) as store:
        current = _found(store, "vendors", vendor_id, "Vendor")
        item = current.model_copy(update={**_vendor_fields(payload), "archived": payload.archived, "version": current.version + 1, "updated_at": utc_now()})
        if not store.save("vendors", item, expected_version=payload.version):
            raise _stale()
    _audit(repository, context, "vendor_updated", item.id)
    return dump(item, "name_key")


# -- bills -----------------------------------------------------------------------------------------------------


class BillInput(BaseModel):
    vendor_id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    reference: str = Field(default="", max_length=80)
    description: str = Field(min_length=2, max_length=300)
    bill_date: date
    due_date: date | None = None
    currency: str = Field(min_length=3, max_length=3)
    subtotal: str = Amount
    tax: str = Field(default="0", max_length=30)
    category: str | None = Field(default=None, max_length=40)
    section: Section | None = None
    cost_centre_id: str | None = OptionalId
    project_id: str | None = OptionalId
    recurrence: Recurrence | None = None
    useful_life_months: int | None = Field(default=None, ge=1, le=600)


class BillUpdate(BillInput):
    version: int


class PaymentInput(BaseModel):
    kind: PaymentKind = PaymentKind.RECORDED
    amount: str | None = Field(default=None, max_length=30)
    paid_on: date | None = None
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)
    allocation_id: str | None = OptionalId


class VoidInput(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class VersionInput(BaseModel):
    version: int


def bill_view(context: TenantContext, bill: Bill, payments: list[Any], live: set[str], vendors: dict[str, str], today: date,
              *, detail: bool = False, holding: set[str] | None = None) -> dict[str, Any]:
    """``live``: active allocation rows (what counts as paid); ``holding``: every allocation row that still exists (an
    allocation in review still holds the bill's money and keeps it from being voided)."""
    counted = [p for p in payments if p.voided_at is None and (p.kind != PaymentKind.ALLOCATION or p.allocation_id in live)]
    held = [p for p in payments if p.voided_at is None and (p.kind != PaymentKind.ALLOCATION or p.allocation_id in (holding if holding is not None else live))]
    paid = S.paid_amount(counted)
    balance = bill.total - paid if bill.status == BillStatus.OPEN else Decimal(0)
    state = S.payment_state(bill, paid)
    data = dump(bill)
    live_bill = bill.status in (BillStatus.OPEN, BillStatus.DRAFT)
    data.update({
        "vendor_name": vendors.get(bill.vendor_id, ""), "amount_paid": str(paid), "balance": str(balance), "payment_state": state,
        "overdue": state in ("unpaid", "partly_paid") and bill.due_date < today, "days_overdue": max(0, (today - bill.due_date).days) if state in ("unpaid", "partly_paid") else 0,
        "can": {"edit": has_permission(context, Permission.BUSINESS_WRITE) and live_bill,
                "edit_money": not held and live_bill,
                "pay": has_permission(context, Permission.BUSINESS_WRITE) and state in ("unpaid", "partly_paid"),
                "approve": has_permission(context, Permission.BUSINESS_WRITE) and bill.status == BillStatus.DRAFT,
                "void": has_permission(context, Permission.BUSINESS_MANAGE) and live_bill and not [p for p in payments if p.voided_at is None]},
    })
    if detail:
        data["payments"] = [{**dump(p), "counts": p in counted, "allocation_removed": p.kind == PaymentKind.ALLOCATION and p.allocation_id not in live
                             and p.voided_at is None} for p in payments]
    return data


def _vendor_names(store: BooksStore) -> dict[str, str]:
    rows, _ = store.find("vendors", limit=MAX_SCAN)
    return {row.id: row.name for row in rows}


BILL_FILTERS = ("unpaid", "partly_paid", "paid", "void", "overdue", "open", "recurring", "draft")


@router.get("/bills")
@refusals
def list_bills(q: str | None = Query(default=None, max_length=120), status_filter: str | None = Query(default=None, alias="status"),
               vendor_id: str | None = Query(default=None, max_length=80), project_id: str | None = Query(default=None, max_length=80),
               limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
               context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    if status_filter is not None and status_filter not in BILL_FILTERS:
        raise S.BooksError(422, "Unknown bill filter.", "invalid_filter", allowed=list(BILL_FILTERS))
    rows, summary = _bills_filtered(context, q, status_filter, vendor_id, project_id)
    return {**_page(rows[offset:offset + limit], len(rows), limit, offset), "summary": summary}


def _bills_filtered(context: TenantContext, q: str | None, status_filter: str | None, vendor_id: str | None,
                    project_id: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    where: dict[str, Any] = {}
    if vendor_id:
        where["vendor_id"] = vendor_id
    if project_id:
        where["project_id"] = project_id
    today = _today()
    allocations = S.allocations_by_id(context)
    live = {key for key, row in allocations.items() if row.status == "active"}
    with open_books_store(context) as store:
        bills, found_total = store.find("bills", where=where, text=(q or "").strip() or None, text_fields=("reference", "description", "category"),
                              order_by="bill_date", limit=MAX_SCAN)
        ids = [bill.id for bill in bills]
        payments, _ = store.find("bill_payments", where={"bill_id": ids, "voided_at": None}, limit=MAX_SCAN * 10) if ids else ([], 0)
        vendors = _vendor_names(store)
    by_bill: dict[str, list[Any]] = {}
    for payment in payments:
        by_bill.setdefault(payment.bill_id, []).append(payment)
    views = [bill_view(context, bill, by_bill.get(bill.id, []), live, vendors, today, holding=set(allocations)) for bill in bills]
    open_balance: dict[str, Decimal] = {}
    overdue: dict[str, Decimal] = {}
    for view in views:
        if view["payment_state"] in ("unpaid", "partly_paid"):
            open_balance[view["currency"]] = open_balance.get(view["currency"], Decimal(0)) + Decimal(view["balance"])
            if view["overdue"]:
                overdue[view["currency"]] = overdue.get(view["currency"], Decimal(0)) + Decimal(view["balance"])
    if status_filter == "overdue":
        views = [v for v in views if v["overdue"]]
    elif status_filter == "open":
        views = [v for v in views if v["payment_state"] in ("unpaid", "partly_paid")]
    elif status_filter == "recurring":
        views = [v for v in views if (v["recurrence"] or v["template_id"]) and v["status"] != "void"]
    elif status_filter is not None:
        views = [v for v in views if v["payment_state"] == status_filter]
    summary = {"open": [{"currency": c, "amount": str(a)} for c, a in sorted(open_balance.items())],
               "overdue": [{"currency": c, "amount": str(a)} for c, a in sorted(overdue.items())], "truncated": found_total > MAX_SCAN}
    return views, summary


@router.post("/bills", status_code=status.HTTP_201_CREATED)
@refusals
def create_bill(payload: BillInput, response: Response, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _write(context)
    now = utc_now()
    key = S.idem_hash(idempotency_key, "bill")
    with open_books_store(context) as store:
        bill = S.bill_from(context, store, payload.model_dump(), now=now, idem_key=key)
        if key and (earlier := store.first("bills", idem_key=key)) is not None:
            if not S.same_bill(earlier, bill):
                raise S.BooksError(409, "This request was already used for another bill. Reload the page.", "idempotency_mismatch")
            response.status_code = status.HTTP_200_OK
            return {**bill_view(context, earlier, [], set(), _vendor_names(store), _today()), "replayed": True}
        L.require_open(store, bill.bill_date, what="This bill")
        store.add("bills", bill)
        vendors = _vendor_names(store)
    _audit(repository, context, "bill_created", bill.id, {"section": bill.section.value, "after": _summary(bill, *BILL_FIELDS)})
    L.post_quietly(context)
    return bill_view(context, bill, [], set(), vendors, _today())


def _bill_detail(context: TenantContext, bill_id: str) -> dict[str, Any]:
    allocations = S.allocations_by_id(context)
    live = {key for key, row in allocations.items() if row.status == "active"}
    with open_books_store(context) as store:
        bill = _found(store, "bills", bill_id, "Bill")
        payments = S.live_payments(store, bill.id, None)
        voided, _ = store.find("bill_payments", where={"bill_id": bill.id}, order_by="created_at", limit=50 + len(payments))
        payments += [p for p in voided if p.voided_at is not None][:50]  # the newest voided ones, for the trail
        payments.sort(key=lambda p: (p.paid_on, p.created_at, p.id))
        vendors = _vendor_names(store)
    return bill_view(context, bill, payments, live, vendors, _today(), detail=True, holding=set(allocations))


@router.get("/bills/{bill_id}")
@refusals
def get_bill(bill_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    return _bill_detail(context, bill_id)


@router.patch("/bills/{bill_id}")
@refusals
def update_bill(bill_id: str, payload: BillUpdate, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    holding = set(S.allocations_by_id(context))  # an allocation in review still holds the bill's money
    with open_books_store(context) as store:
        current = _found(store, "bills", bill_id, "Bill", for_update=True)
        if current.version != payload.version:
            raise _stale()
        if current.status == BillStatus.VOID:
            raise S.BooksError(409, "A void bill does not change.", "bill_void")
        updated = S.bill_from(context, store, payload.model_dump(exclude={"version"}), now=utc_now(), idem_key=current.idem_key, existing=current)
        money_changed = (updated.currency, updated.subtotal, updated.tax) != (current.currency, current.subtotal, current.tax)
        if money_changed and S.live_payments(store, current.id, holding):
            raise S.BooksError(409, "This bill has payments: its amounts and currency stay as paid. Void the payments first.", "has_payments")
        trail = _changes(current, updated, (*BILL_FIELDS, "vendor_id"))
        if trail["after"]:  # dates, money, category or section of a bill in a closed period stay (defect B6); its words may change
            L.require_open(store, current.bill_date, updated.bill_date, what="This bill")
        if not store.save("bills", updated, expected_version=current.version):
            raise _stale()
    _audit(repository, context, "bill_updated", bill_id, trail)
    L.post_quietly(context)
    return _bill_detail(context, bill_id)


@router.post("/bills/{bill_id}/approve")
@refusals
def approve_bill(bill_id: str, payload: VersionInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    """A draft bill (made by a recurring bill set to drafts) becomes a normal open bill: it counts from now on.

    Maker and checker (defect B8): an owner or an accountant approves, and never one who made the draft (who created or last
    changed its recurring bill, or the bill itself)."""
    _accounting(context)
    with open_books_store(context) as store:
        current = _found(store, "bills", bill_id, "Bill", for_update=True)
        if current.version != payload.version:
            raise _stale()
        if current.status != BillStatus.DRAFT:
            raise S.BooksError(409, "Only a draft bill is approved.", "not_draft")
        makers = {current.created_by}
        if current.template_id and (template := store.get("templates", current.template_id)) is not None:
            makers |= {template.created_by, template.updated_by}
        if context.user_id in makers:
            raise S.BooksError(403, "You made this draft (or its recurring bill), so someone else approves it: another owner or an accountant.",
                               "own_draft")
        L.require_open(store, current.bill_date, what="This bill")
        item = current.model_copy(update={"status": BillStatus.OPEN, "version": current.version + 1, "updated_at": utc_now()})
        if not store.save("bills", item, expected_version=current.version):
            raise _stale()
    _audit(repository, context, "bill_approved", bill_id, _changes(current, item, ("status",)))
    L.post_quietly(context)
    return _bill_detail(context, bill_id)


@router.post("/bills/{bill_id}/void")
@refusals
def void_bill(bill_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    with open_books_store(context) as store:
        current = _found(store, "bills", bill_id, "Bill", for_update=True)
        if current.status == BillStatus.VOID:
            raise S.BooksError(409, "This bill is already void.", "bill_void")  # a draft may be voided: it was never counted
        if S.live_payments(store, current.id, None):  # every payment not voided, an allocation in review included
            raise S.BooksError(409, "Void this bill's payments first.", "has_payments")
        L.require_open(store, current.bill_date, what="This bill")
        now = utc_now()
        item = current.model_copy(update={"status": BillStatus.VOID, "voided_by": context.user_id, "voided_at": now,
                                          "void_reason": S.clean(payload.reason, 300), "version": current.version + 1, "updated_at": now})
        if not store.save("bills", item, expected_version=current.version):
            raise _stale()
    _audit(repository, context, "bill_voided", bill_id, _changes(current, item, ("status", "total", "currency", "bill_date")))
    L.post_quietly(context)
    return _bill_detail(context, bill_id)


@router.post("/bills/{bill_id}/payments", status_code=status.HTTP_201_CREATED)
@refusals
def pay(bill_id: str, payload: PaymentInput, response: Response, context: TenantContext = Depends(tenant_context),
        repository: AnumRepository = Depends(repository_context),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _write(context)
    if payload.kind == PaymentKind.ALLOCATION and not payload.allocation_id:
        raise S.BooksError(422, "Choose the allocation that paid this bill.", "allocation_required")
    if payload.kind == PaymentKind.RECORDED and not payload.amount:
        raise S.BooksError(422, "Enter the amount paid.", "invalid_amount")
    with open_books_store(context) as store:
        payment, replayed = S.pay_bill(context, store, bill_id, kind=payload.kind, amount=payload.amount, paid_on=payload.paid_on,
                                       method=payload.method.value, reference=payload.reference, note=payload.note, allocation_id=payload.allocation_id,
                                       idem_key=idempotency_key, today=_today(), now=utc_now())
    if replayed:
        response.status_code = status.HTTP_200_OK
    else:
        _audit(repository, context, "bill_paid", bill_id, {"payment_id": payment.id, "kind": payment.kind.value,
                                                           "after": _summary(payment, "amount", "currency", "paid_on")})
        L.post_quietly(context)
    return {**_bill_detail(context, bill_id), "payment_id": payment.id, "replayed": replayed}


@router.post("/bills/{bill_id}/payments/{payment_id}/void")
@refusals
def void_payment(bill_id: str, payment_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    _manage(context)
    with open_books_store(context) as store:
        _found(store, "bills", bill_id, "Bill", for_update=True)
        payment = store.get("bill_payments", payment_id)
        if payment is None or payment.bill_id != bill_id:
            raise S.BooksError(404, "Payment not found.", "payment_not_found")
        voided = payment.voided_at is None
        if voided:
            L.require_open(store, payment.paid_on, what="This payment")
            now = utc_now()
            store.save("bill_payments", payment.model_copy(update={"voided_at": now, "voided_by": context.user_id,
                                                                   "void_reason": S.clean(payload.reason, 300)}))
    if voided:
        _audit(repository, context, "bill_payment_voided", bill_id, {"payment_id": payment_id, "before": _summary(payment, "amount", "currency", "paid_on")})
        L.post_quietly(context)
    return _bill_detail(context, bill_id)


@router.get("/bills.csv")
@refusals
def export_bills(context: TenantContext = Depends(tenant_context)) -> Response:
    _read(context)
    rows, _ = _bills_filtered(context, None, None, None, None)
    out: list[list[Any]] = [["bill_id", "vendor", "reference", "description", "bill_date", "due_date", "currency", "net", "tax", "total", "paid",
                             "balance", "state", "category", "section", "recurrence"]]
    for row in rows:
        out.append([row["id"], row["vendor_name"], row["reference"], row["description"], row["bill_date"], row["due_date"], row["currency"],
                    row["subtotal"], row["tax"], row["total"], row["amount_paid"], row["balance"], row["payment_state"], row["category"], row["section"],
                    row["recurrence"] or ""])
    return _csv(out, "business-bills.csv")


# -- allocations seen from the books ----------------------------------------------------------------------------


class TagInput(BaseModel):
    cost_centre_id: str | None = OptionalId
    section: Literal["cost_of_sales", "operating_expense", "capital", "excluded"] | None = None
    note: str = Field(default="", max_length=300)
    version: int | None = None


@router.get("/allocations")
@refusals
def list_allocations(status_filter: str | None = Query(default=None, alias="status", pattern=r"^(active|needs_review|unpaid_bill|untagged)$"),
                     q: str | None = Query(default=None, max_length=60), currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"),
                     limit: int = Query(default=25, ge=1, le=MAX_PAGE),
                     offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    """The allocation rows of this business with how the books count them (never the member's ledger)."""
    _read(context)
    rows = sorted(S.allocations_by_id(context).values(), key=lambda a: (a.posted_on, a.created_at, a.id), reverse=True)
    with open_books_store(context) as store:
        tags = {t.allocation_id: t for t in store.find("allocation_tags", limit=MAX_SCAN)[0]}
        payments = store.find("bill_payments", where={"kind": PaymentKind.ALLOCATION.value, "voided_at": None}, limit=MAX_SCAN)[0]
        settings = store.get_settings()
    paying = {p.allocation_id: p.bill_id for p in payments}
    needle = (q or "").strip().casefold()
    if needle:
        rows = [a for a in rows if needle in a.category or needle in (a.memo or "").casefold() or needle in a.source_ref.casefold()]
    if currency:
        rows = [a for a in rows if a.currency == currency]
    if status_filter in ("active", "needs_review"):
        rows = [a for a in rows if a.status == status_filter]
    elif status_filter == "unpaid_bill":
        rows = [a for a in rows if a.id not in paying and a.status == "active"]
    elif status_filter == "untagged":
        rows = [a for a in rows if a.id not in tags]
    sections = settings.category_sections if settings else {}
    items = []
    for row in rows[offset:offset + limit]:
        tag = tags.get(row.id)
        items.append({
            "id": row.id, "source_ref": row.source_ref, "posted_on": row.posted_on.isoformat(), "amount": str(row.amount), "currency": row.currency,
            "category": row.category, "project_id": row.project_id, "memo": row.memo, "treatment": row.treatment, "status": row.status,
            "allocated_by": row.allocated_by, "section": (tag.section if tag and tag.section else sections.get(row.category, "operating_expense")),
            "tag": dump(tag) if tag else None, "paying_bill_id": paying.get(row.id),
        })
    return _page(items, len(rows), limit, offset)


@router.put("/allocations/{allocation_id}/tag")
@refusals
def tag_allocation(allocation_id: str, payload: TagInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    allocation = S.allocations_by_id(context).get(allocation_id)
    if allocation is None:
        raise S.BooksError(404, "Allocation not found in this business.", "allocation_not_found")
    now = utc_now()
    with open_books_store(context) as store:
        L.require_open(store, allocation.posted_on, what="This allocation")
        current = store.first("allocation_tags", allocation_id=allocation_id)
        centre = S.require_cost_centre(store, payload.cost_centre_id, keep=current.cost_centre_id if current else None)
        if current is None:
            if payload.version is not None:
                raise _stale()
            tag = AllocationTag(id=new_id("btag"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now,
                                allocation_id=allocation_id, cost_centre_id=centre, section=payload.section, note=S.clean(payload.note, 300),
                                updated_by=context.user_id, updated_at=now)
            store.add("allocation_tags", tag)
        else:
            if payload.version != current.version:
                raise _stale()
            tag = current.model_copy(update={"cost_centre_id": centre, "section": payload.section, "note": S.clean(payload.note, 300),
                                             "version": current.version + 1, "updated_by": context.user_id, "updated_at": now})
            if not store.save("allocation_tags", tag, expected_version=current.version):
                raise _stale()
    _audit(repository, context, "allocation_tagged", allocation_id, {"section": payload.section or "",
                                                                    "before": {"section": (current.section or "") if current else ""}})
    L.post_quietly(context)
    return dump(tag)


# -- budgets --------------------------------------------------------------------------------------------------------


class BudgetInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    period_start: date
    months: Literal[1, 3, 6, 12] = 1
    amount: str = Amount
    section: Section | None = None
    category: str | None = Field(default=None, max_length=40)
    project_id: str | None = OptionalId
    cost_centre_id: str | None = OptionalId


class BudgetUpdate(BudgetInput):
    version: int


def _budget_fields(context: TenantContext, store: BooksStore, payload: BudgetInput, keep: str | None = None) -> dict[str, Any]:
    settings = S.settings_or_409(store)
    if payload.section == Section.CAPITAL:
        raise S.BooksError(422, "Budgets cover costs: cost of sales or operating expenses.", "invalid_section")
    return {"name": S.clean(payload.name, 120), "period_start": payload.period_start.replace(day=1), "months": payload.months,
            "currency": settings.base_currency, "amount": S.amount_of(payload.amount, settings.base_currency), "section": payload.section,
            "category": S.category_of(payload.category, required=False), "project_id": S.require_project(context, payload.project_id),
            "cost_centre_id": S.require_cost_centre(store, payload.cost_centre_id, keep=keep)}


@router.get("/budgets")
@refusals
def list_budgets(limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                 context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("budgets", order_by="period_start", limit=limit, offset=offset)
    return _page([dump(r) for r in rows], total, limit, offset)


@router.post("/budgets", status_code=status.HTTP_201_CREATED)
@refusals
def create_budget(payload: BudgetInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    now = utc_now()
    with open_books_store(context) as store:
        item = Budget(id=new_id("bbud"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, created_by=context.user_id,
                      updated_at=now, **_budget_fields(context, store, payload))
        store.add("budgets", item)
    _audit(repository, context, "budget_created", item.id)
    return dump(item)


@router.patch("/budgets/{budget_id}")
@refusals
def update_budget(budget_id: str, payload: BudgetUpdate, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Any:
    _write(context)
    with open_books_store(context) as store:
        current = _found(store, "budgets", budget_id, "Budget")
        item = current.model_copy(update={**_budget_fields(context, store, payload, current.cost_centre_id), "version": current.version + 1, "updated_at": utc_now()})
        if not store.save("budgets", item, expected_version=payload.version):
            raise _stale()
    _audit(repository, context, "budget_updated", budget_id)
    return dump(item)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
@refusals
def delete_budget(budget_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    _write(context)
    with open_books_store(context) as store:
        if not store.delete("budgets", budget_id):
            raise S.BooksError(404, "Budget not found.", "budget_not_found")
    _audit(repository, context, "budget_deleted", budget_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- exchange rates --------------------------------------------------------------------------------------------------


class RateInput(BaseModel):
    base: str = Field(min_length=3, max_length=3)
    quote: str = Field(min_length=3, max_length=3)
    rate: str = Field(min_length=1, max_length=30)
    as_of: date
    source: str = Field(min_length=3, max_length=200)


@router.get("/rates")
@refusals
def list_rates(limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0), origin: RateOrigin | None = None,
               context: TenantContext = Depends(tenant_context)) -> Any:
    """Newest first; ``origin`` keeps the rates typed by owners (``manual``) or fetched from the ECB (``ecb``)."""
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("rates", where={"origin": origin.value} if origin else None, order_by="as_of", limit=limit, offset=offset)
    return _page([dump(r) for r in rows], total, limit, offset)


@router.post("/rates", status_code=status.HTTP_201_CREATED)
@refusals
def create_rate(payload: RateInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """A rate the business uses: 1 ``base`` = ``rate`` ``quote`` on ``as_of``, from ``source`` (ANUM never invents one)."""
    _manage(context)
    base, quote = S.currency_of(payload.base), S.currency_of(payload.quote)
    if base == quote:
        raise S.BooksError(422, "A rate is between two different currencies.", "same_currency")
    try:
        value = fx.check_rate(payload.rate)
    except fx.FxError as exc:
        raise S.BooksError(422, str(exc), "invalid_rate") from None
    source = S.clean(payload.source, 200)
    if len(source) < 3:
        raise S.BooksError(422, "Say where the rate comes from, for example the central bank and the day.", "source_required")
    S.not_future(payload.as_of, _today(), "rate date")
    item = Rate(id=new_id("brate"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=utc_now(), base=base, quote=quote,
                rate=value, as_of=payload.as_of, source=source, created_by=context.user_id, origin=RateOrigin.MANUAL)
    replaced = None
    with open_books_store(context) as store:
        # A rate typed by an owner always wins: a fetched (ECB) rate for the same pair and day makes way for it.
        fetched = store.first("rates", base=base, quote=quote, as_of=payload.as_of, origin=RateOrigin.ECB.value)
        if fetched is not None:
            store.delete("rates", fetched.id)
            replaced = fetched.id
        store.add("rates", item)
    _audit(repository, context, "rate_recorded", item.id, {"pair": f"{base}{quote}", "after": _summary(item, "rate", "as_of"),
                                                           **({"replaced": replaced} if replaced else {})})
    L.post_quietly(context)  # an item waiting for this rate can be posted now
    return dump(item)


@router.delete("/rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
@refusals
def delete_rate(rate_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    with open_books_store(context) as store:
        if not store.delete("rates", rate_id):
            raise S.BooksError(404, "Rate not found.", "rate_not_found")
    _audit(repository, context, "rate_removed", rate_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- journal adjustments -----------------------------------------------------------------------------------------------


class AdjustmentInput(BaseModel):
    kind: AdjustmentKind
    entry_date: date
    currency: str = Field(min_length=3, max_length=3)
    amount: str = Amount
    section: Literal["cost_of_sales", "operating_expense"] | None = None
    category: str | None = Field(default=None, max_length=40)
    cost_centre_id: str | None = OptionalId
    project_id: str | None = OptionalId
    description: str = Field(min_length=3, max_length=300)
    start_month: date | None = None
    months: int | None = Field(default=None, ge=1, le=600)
    reverse_on: date | None = None


@router.get("/adjustments")
@refusals
def list_adjustments(kind: AdjustmentKind | None = None, include_void: bool = False, limit: int = Query(default=25, ge=1, le=MAX_PAGE),
                     offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    where: dict[str, Any] = {}
    if kind is not None:
        where["kind"] = kind.value
    if not include_void:
        where["status"] = "active"
    with open_books_store(context) as store:
        rows, total = store.find("adjustments", where=where, order_by="entry_date", limit=limit, offset=offset)
    return _page([dump(r) for r in rows], total, limit, offset)


@router.post("/adjustments", status_code=status.HTTP_201_CREATED)
@refusals
def create_adjustment(payload: AdjustmentInput, response: Response, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context),
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120)) -> Any:
    _accounting(context)
    key = S.idem_hash(idempotency_key, "adjustment")
    with open_books_store(context) as store:
        S.settings_or_409(store)
        item = S.adjustment_from(context, store, payload.model_dump(), idem_key=key, today=_today(), now=utc_now())
        if key and (earlier := store.first("adjustments", idem_key=key)) is not None:
            if (earlier.kind, earlier.amount, earlier.currency, earlier.entry_date) != (item.kind, item.amount, item.currency, item.entry_date):
                raise S.BooksError(409, "This request was already used for another adjustment. Reload the page.", "idempotency_mismatch")
            response.status_code = status.HTTP_200_OK
            return {**dump(earlier), "replayed": True}
        L.require_open(store, item.entry_date, item.start_month, what="This adjustment")
    S.check_settlement(context, item)  # never pay back more than is owed (defect B4)
    with open_books_store(context) as store:
        store.add("adjustments", item)
    _audit(repository, context, "adjustment_recorded", item.id, {"kind": item.kind.value, "after": _summary(item, "entry_date", "currency", "amount")})
    L.post_quietly(context)
    return dump(item)


@router.post("/adjustments/{adjustment_id}/void")
@refusals
def void_adjustment(adjustment_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _accounting(context)
    with open_books_store(context) as store:
        current = _found(store, "adjustments", adjustment_id, "Adjustment", for_update=True)
        if current.status == "void":
            raise S.BooksError(409, "This adjustment is already void.", "already_void")
        L.require_open(store, current.entry_date, current.start_month, current.reverse_on, what="This adjustment")
        item = current.model_copy(update={"status": "void", "voided_by": context.user_id, "voided_at": utc_now(), "void_reason": S.clean(payload.reason, 300)})
        store.save("adjustments", item)
    _audit(repository, context, "adjustment_voided", adjustment_id, {"kind": current.kind.value, **_changes(current, item, ("status",)),
                                                                     "amount": str(current.amount), "currency": current.currency})
    L.post_quietly(context)
    return dump(item)


# -- intercompany ---------------------------------------------------------------------------------------------------------


class IntercompanyInput(BaseModel):
    workspace_id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    kind: IntercompanyKind
    direction: Direction
    entry_date: date
    currency: str = Field(min_length=3, max_length=3)
    amount: str = Amount
    description: str = Field(min_length=3, max_length=300)


@router.get("/intercompany/counterparties")
@refusals
def list_counterparties(context: TenantContext = Depends(tenant_context)) -> Any:
    """Other businesses of this organisation where the caller is an owner (``business:manage``)."""
    _manage(context)
    return {"items": S.counterparties(context)}


@router.get("/intercompany")
@refusals
def list_intercompany(include_void: bool = False, limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                      context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("intercompany", where={} if include_void else {"status": "active"}, order_by="entry_date", limit=limit, offset=offset)
    return _page([dump(r) for r in rows], total, limit, offset)


@router.post("/intercompany", status_code=status.HTTP_201_CREATED)
@refusals
def create_intercompany(payload: IntercompanyInput, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> Any:
    """Written in both businesses with one ``pair_id``: income on one side is a cost on the other, a loan a debt."""
    _manage(context)
    name = S.manageable_workspace(context, payload.workspace_id)
    if name is None:
        raise S.BooksError(403, "You can record an intercompany entry only with another business where you are an owner.", "workspace_not_allowed")
    mine, theirs = S.intercompany_pair(context, payload.model_dump(), name, today=_today(), now=utc_now())
    other = context.model_copy(update={"workspace_id": payload.workspace_id})
    with open_books_store(other) as store:
        L.require_open(store, theirs.entry_date, what="This entry (in the other business)")
    with open_books_store(context) as store:
        L.require_open(store, mine.entry_date, what="This entry")
        store.add("intercompany", mine)
        store.add_elsewhere("intercompany", theirs)
    _audit(repository, context, "intercompany_recorded", mine.id, {"pair_id": mine.pair_id, "kind": mine.kind.value,
                                                                    "after": _summary(mine, "entry_date", "currency", "amount", "direction")})
    L.post_quietly(context)
    L.post_quietly(other)
    return dump(mine)


@router.post("/intercompany/{entry_id}/void")
@refusals
def void_intercompany(entry_id: str, payload: VoidInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Any:
    """Voids both sides (the caller must still be an owner of the other business)."""
    _manage(context)
    with open_books_store(context) as store:
        mine = _found(store, "intercompany", entry_id, "Intercompany entry", for_update=True)
    if mine.status == "void":
        raise S.BooksError(409, "This entry is already void.", "already_void")
    if S.manageable_workspace(context, mine.counterparty_workspace_id) is None:
        raise S.BooksError(403, "Both businesses must agree: you need to be an owner of the other business to void this entry.", "workspace_not_allowed")
    now = utc_now()
    changes = {"status": "void", "voided_by": context.user_id, "voided_at": now, "void_reason": S.clean(payload.reason, 300)}
    other_scope = context.model_copy(update={"workspace_id": mine.counterparty_workspace_id})
    with open_books_store(other_scope) as store:
        L.require_open(store, mine.entry_date, what="This entry (in the other business)")
    with open_books_store(context) as store:
        L.require_open(store, mine.entry_date, what="This entry")
        current = _found(store, "intercompany", entry_id, "Intercompany entry", for_update=True)
        if current.status == "void":
            raise S.BooksError(409, "This entry is already void.", "already_void")
        item = current.model_copy(update=changes)
        store.save("intercompany", item)
        other = store.get_elsewhere("intercompany", current.counterparty_workspace_id, pair_id=current.pair_id)
        if other is not None and other.status != "void":
            store.save_elsewhere("intercompany", other.model_copy(update=changes))
    _audit(repository, context, "intercompany_voided", entry_id, {"pair_id": current.pair_id, **_changes(current, item, ("status",))})
    L.post_quietly(context)
    L.post_quietly(other_scope)
    return dump(item)


# -- the report, the dashboard and projects --------------------------------------------------------------------------------


def _period(store_settings: BookSettings, start: date | None, end: date | None) -> tuple[date, date]:
    first, last = R.default_period(store_settings, _today())
    start, end = start or first, end or last
    if start > end:
        raise S.BooksError(422, "The period starts after it ends.", "invalid_period")
    if (end - start).days > MAX_SPAN_DAYS:
        raise S.BooksError(422, "A report covers at most three years.", "period_too_long")
    return start, end


def rates_since(start: date | None, end: date | None) -> date:
    """The oldest rate a report can use: three years before the trend, the budgets and the period start (a payment's
    exchange difference looks back to its invoice's date)."""
    first = start or (_today() - timedelta(days=MAX_SPAN_DAYS))
    last = end or _today()
    return min(first, last - timedelta(days=366)) - timedelta(days=MAX_SPAN_DAYS)


def _report(context: TenantContext, start: date | None, end: date | None, basis: Basis | None) -> dict[str, Any]:
    inputs = S.gather(context, rates_since(start, end))
    first, last = _period(inputs.settings, start, end)
    return R.report(inputs, first, last, basis or inputs.settings.basis, inputs.cost_centres, _today())


@router.get("/report")
@refusals
def get_report(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
               basis: Basis | None = None, context: TenantContext = Depends(tenant_context)) -> Any:
    """The P&L, tax, cash flow, balances, owner equity and the dashboard breakdowns for one period."""
    _read(context)
    return _report(context, start, end, basis)


@router.get("/report.csv")
@refusals
def export_report(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                  basis: Basis | None = None, context: TenantContext = Depends(tenant_context)) -> Response:
    _read(context)
    data = _report(context, start, end, basis)
    pnl = data["pnl"]
    base = data["base_currency"]
    rows: list[list[Any]] = [["section", "line", "currency", "amount"],
                             ["period", f"{data['period']['from']} to {data['period']['to']}", "", data["period"]["basis"]]]
    for section in ("revenue", "cost_of_sales", "operating_expenses", "other_income", "finance_costs"):
        for line in pnl[section]["lines"]:
            rows.append([section, line["key"], base, line["amount"]])
        rows.append([section, "total", base, pnl[section]["total"]])
    for key in ("gross_profit", "operating_profit", "fx_differences", "net_profit"):
        rows.append([key, "", base, pnl[key]])
    for item in pnl["not_converted"]:
        for group, amount in item["groups"].items():
            rows.append(["not_converted", group, item["currency"], amount])
    for item in data["tax"]["by_currency"]:
        rows.append(["tax", "output", item["currency"], item["output"]])
        rows.append(["tax", "input", item["currency"], item["input"]])
    for line, values in data["cash_flow"]["lines"].items():
        for value in values:
            rows.append(["cash_flow", line, value["currency"], value["amount"]])
    for line, values in data["cash_flow"]["paid_by_members"].items():
        for value in values:
            rows.append(["paid_by_members", line, value["currency"], value["amount"]])
    # Completeness first-hand in the file (defect B2): whether every figure is in, and why not.
    rows.append(["complete", "yes" if pnl["complete"] and not data["truncated"] and not data["allocations_in_review"] else "no", "", ""])
    rows.append(["truncated", "yes" if data["truncated"] else "no", "", ""])
    rows.append(["allocations_in_review", "", "", data["allocations_in_review"]])
    for note in data["notes"]:
        rows.append(["note", note, "", ""])
    balances = data["balances"]
    for key in ("receivables", "payables"):
        for item in balances[key]:
            rows.append([f"owed_{key}", f"as of {balances['as_of']}", item["currency"], item["total"]])
    for key in ("owed_to_members", "debt", "interest_payable"):
        for item in balances.get(key, []):
            rows.append([key, f"as of {balances['as_of']}", item["currency"], item["amount"]])
    for item in balances["intercompany"]:
        rows.append(["intercompany", item["name"] or item["workspace_id"], item["currency"], item["amount"]])
    for key in ("contributions", "drawings"):
        for item in data["equity"][key]:
            rows.append(["equity", key, item["currency"], item["amount"]])
    for conversion in data["conversions"]:
        rows.append(["rate", f"1 {conversion['from_currency']} = {conversion['rate']} {conversion['to_currency']} on {conversion['as_of']}",
                     conversion["source"], ""])
    return _csv(rows, f"business-pnl-{data['period']['from']}-{data['period']['to']}.csv")


@router.get("/projects")
@refusals
def list_projects(limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                  context: TenantContext = Depends(tenant_context)) -> Any:
    """Each project's books figures for the period next to its contract profitability (``GET /business/projects/{id}/profitability``)."""
    _read(context)
    from .api_changes import project_profitability
    from .store import Query as BusinessQuery
    from .store import open_business_store

    inputs = S.gather(context, rates_since(start, end))
    first, last = _period(inputs.settings, start, end)
    data = R.report(inputs, first, last, inputs.settings.basis)
    # Every project's figures, not only the report's first rows (defect B1): a project beyond them is never shown as 0.
    book = R.Book(inputs)
    converter = R.Converter(inputs.rates, inputs.settings.base_currency, inputs.settings.rate_max_age_days)
    rows = R.entries(book, inputs.settings.basis, first, last, converter, set())
    figures = {row["id"]: row for row in R.breakdown(rows, converter, "project_id", {key: value[0] for key, value in inputs.projects.items()}, limit=None)}
    with open_business_store(context) as business:
        projects, total = business.find("projects", BusinessQuery(order_by="updated_at", limit=limit, offset=offset))
    items = []
    for project in projects:
        try:
            contract = project_profitability(project.id, context).model_dump(mode="json")
        except HTTPException:
            contract = None  # a project whose proposal was removed has no contract figures
        books = figures.get(project.id)
        items.append({"id": project.id, "name": project.name, "account_id": project.account_id, "status": getattr(project.status, "value", project.status),
                      "currency": getattr(project.currency, "value", project.currency),
                      "books": books or {"revenue": "0", "costs": "0", "margin": "0", "margin_pct": None, "complete": not inputs.truncated,
                                         "not_converted": [], "recorded": False},
                      "contract": None if contract is None else {key: contract.get(key) for key in (
                          "revenue", "contract_total", "cash_collected", "outstanding", "gross_margin", "gross_margin_pct", "currency")}})
    return {**_page(items, total, limit, offset), "base_currency": inputs.settings.base_currency, "period": data["period"],
            "complete": not inputs.truncated}


__all__ = ["router"]
