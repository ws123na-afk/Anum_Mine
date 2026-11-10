"""HTTP routes of business tax under ``/api/v1/business/books/tax`` (docs/business-tax.md).

Who can do what:

* ``business:read`` (owners and members): the tax settings and rate table, the VAT return with its documents and CSV,
  the ZATCA readiness panel and the generated e-invoice data.
* Owners and accountants (``api_ledger.require_accounting``): change the tax settings (including the ZATCA switch),
  add or remove a rate, generate e-invoice data for a sent invoice or credit note.
* Nobody: sending anything to ZATCA. ``/zatca/documents/{id}/submit`` answers 409 ``zatca_onboarding_required``.

Nothing here files a return or calls an authority. Refusals answer ``{"detail": "<plain words>", "code": "<code>"}``.
Audit rows hold ids, codes and dates, never a registration number or an address. Every route is a plain ``def``.
"""

from __future__ import annotations

import functools
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import books_service as S
from . import tax_return as TR
from . import zatca as Z
from .api_books import _audit, _csv, _page, _read, _today, refusals
from .api_ledger import _period, can_account, require_accounting
from .books_models import TaxRate, TaxSettings
from .books_store import MAX_PAGE, open_books_store
from .tax_rules import (
    FILING_PRESETS,
    MAX_DUE_DAYS,
    NO_VAT_COUNTRIES,
    Frequency,
    Jurisdiction,
    TaxCode,
    TaxRuleError,
    check_registration,
    deadline_source,
    filing_country,
)
from .tax_service import rate_view, rates_of, seed_rates, settings_of, settings_view

router = APIRouter(prefix="/api/v1/business/books/tax", tags=["business"])


def _tax(route: Any) -> Any:
    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return route(*args, **kwargs)
        except TaxRuleError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.message, "code": exc.code})
        except Z.ZatcaError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})

    return wrapper


def _periods(settings: TaxSettings | None, today: date) -> list[dict[str, str]]:
    """The last eight filing periods (calendar months, or quarters starting in the business's cycle month)."""
    from .books_report import add_months, month_start

    monthly = settings is not None and settings.filing_frequency == Frequency.MONTHLY
    span = 1 if monthly else 3
    cycle = settings.period_start_month if settings else 1
    start = month_start(today)
    if not monthly:
        while (start.month - cycle) % 3:
            start = add_months(start, -1)
    out = []
    for _ in range(8):
        last = date.fromordinal(add_months(start, span).toordinal() - 1)
        out.append({"from": start.isoformat(), "to": last.isoformat()})
        start = add_months(start, -span)
    return out


def filing_deadline(settings: TaxSettings | None) -> dict[str, Any] | None:
    """Where the VAT return deadline comes from for these settings, and the rule it follows (``None`` before tax is set up)."""
    if settings is None:
        return None
    country = filing_country(settings.jurisdiction, settings.country)
    source = deadline_source(settings.jurisdiction, settings.country, settings.filing_due_days)
    preset = FILING_PRESETS.get(country or "")
    return {"source": source, "country": country, "due_days": settings.filing_due_days,
            "preset": None if preset is None else {"end_of_next_month": preset.end_of_next_month, "days_after": preset.days_after,
                                                   "usual_frequency": preset.usual_frequency, "url": preset.source}}


@router.get("")
@refusals
def overview(context: TenantContext = Depends(tenant_context)) -> Any:
    """The tax settings (``null`` until saved), the rate table, the choices the forms offer and the recent filing periods."""
    _read(context)
    with open_books_store(context) as store:
        S.settings_or_409(store)
        settings = settings_of(store)
        rates = rates_of(store, settings.jurisdiction) if settings else []
    return {
        "settings": settings_view(settings),
        "rates": [rate_view(row) for row in rates],
        "can_edit": can_account(context),
        "jurisdictions": [item.value for item in Jurisdiction],
        "codes": [item.value for item in TaxCode],
        "sales_codes": ["standard", "zero_rated", "export", "exempt", "out_of_scope"],
        "frequencies": [item.value for item in Frequency],
        "periods": _periods(settings, _today()),
        "filing_deadline": filing_deadline(settings),
        "filing_presets": sorted(FILING_PRESETS),
        "no_vat_countries": sorted(NO_VAT_COUNTRIES),
        "max_due_days": MAX_DUE_DAYS,
        "notice": {"en": Z.NOT_VERIFIED_EN, "ar": Z.NOT_VERIFIED_AR},
    }


class TaxSettingsInput(BaseModel):
    jurisdiction: Jurisdiction
    registered: bool = False
    registration_number: str = Field(default="", max_length=30)
    registration_date: date | None = None
    filing_frequency: Frequency = Frequency.QUARTERLY
    period_start_month: int = Field(default=1, ge=1, le=3)
    # The filing country of an ``OTHER`` business (two letters) and the owner's own due day, in days after the period ends.
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    filing_due_days: int | None = Field(default=None, ge=1, le=MAX_DUE_DAYS)
    zatca_enabled: bool = False
    seller_crn: str = Field(default="", max_length=20, pattern=r"^[0-9]*$")
    seller_street: str = Field(default="", max_length=120)
    seller_building: str = Field(default="", max_length=10)
    seller_district: str = Field(default="", max_length=120)
    seller_city: str = Field(default="", max_length=80)
    seller_postal_code: str = Field(default="", max_length=10)
    version: int | None = None


@router.put("/settings")
@refusals
@_tax
def put_settings(payload: TaxSettingsInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Any:
    require_accounting(context)
    now = utc_now()
    number = check_registration(payload.jurisdiction, payload.registration_number)
    if payload.registered and not number:
        raise TaxRuleError("Enter the VAT registration number of a registered business.", "vat_number_required")
    if payload.zatca_enabled and payload.jurisdiction != Jurisdiction.SA:
        raise TaxRuleError("ZATCA e-invoicing is for Saudi businesses only.", "zatca_not_sa")
    if payload.zatca_enabled and not payload.registered:
        raise TaxRuleError("Mark the business as VAT registered before turning e-invoicing on.", "zatca_needs_registration")
    if payload.registration_date and payload.registration_date > _today():
        raise TaxRuleError("The registration date cannot be in the future.", "invalid_registration_date")
    country = payload.country.upper() if payload.country and payload.jurisdiction == Jurisdiction.OTHER else None
    if country in (Jurisdiction.SA.value, Jurisdiction.AE.value):
        raise TaxRuleError("For Saudi Arabia or the UAE choose that jurisdiction, not Other.", "country_has_jurisdiction")
    fields = {
        "jurisdiction": payload.jurisdiction.value, "registered": payload.registered, "registration_number": number,
        "registration_date": payload.registration_date, "filing_frequency": payload.filing_frequency.value,
        "period_start_month": payload.period_start_month, "country": country, "filing_due_days": payload.filing_due_days,
        "zatca_enabled": payload.zatca_enabled,
        **{name: S.clean(getattr(payload, name), limit) for name, limit in (("seller_crn", 20), ("seller_street", 120), ("seller_building", 10),
                                                                              ("seller_district", 120), ("seller_city", 80), ("seller_postal_code", 10))},
        "updated_by": context.user_id, "updated_at": now,
    }
    with open_books_store(context) as store:
        S.settings_or_409(store)
        current = settings_of(store)
        if current is None:
            if payload.version is not None:
                raise S.BooksError(409, "Someone changed this since you opened it. Reload and try again.", "stale")
            value = TaxSettings(id=new_id("btax"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, **fields)
            store.add("tax_settings", value)
        else:
            if payload.version != current.version:
                raise S.BooksError(409, "Someone changed this since you opened it. Reload and try again.", "stale")
            value = current.model_copy(update={**fields, "version": current.version + 1})
            if not store.save("tax_settings", value, expected_version=current.version):
                raise S.BooksError(409, "Someone changed this since you opened it. Reload and try again.", "stale")
        seeded = seed_rates(store, context, payload.jurisdiction, context.user_id, now)
        rates = rates_of(store, value.jurisdiction)
    _audit(repository, context, "tax_settings_saved", value.id, {"jurisdiction": value.jurisdiction, "registered": value.registered,
                                                                 "zatca_enabled": value.zatca_enabled, "rates_seeded": seeded,
                                                                 "country": value.country or "", "filing_due_days": str(value.filing_due_days or "")})
    return {"settings": settings_view(value), "rates": [rate_view(row) for row in rates], "filing_deadline": filing_deadline(value)}


class RateInput(BaseModel):
    code: TaxCode
    rate_pct: str = Field(min_length=1, max_length=10)
    effective_from: date
    effective_to: date | None = None
    note: str = Field(default="", max_length=200)


@router.post("/rates", status_code=status.HTTP_201_CREATED)
@refusals
@_tax
def add_rate(payload: RateInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    """A new rate from a day (a rate change is a new row; the rows before it keep the old documents right)."""
    require_accounting(context)
    try:
        rate = Decimal(payload.rate_pct.strip())
    except InvalidOperation:
        raise TaxRuleError("Enter the rate as a number, for example 15.", "invalid_rate") from None
    if not rate.is_finite() or rate < 0 or rate > 100 or rate.as_tuple().exponent < -4:  # type: ignore[operator]
        raise TaxRuleError("A rate is between 0 and 100, with at most four decimals.", "invalid_rate")
    if payload.effective_to and payload.effective_to < payload.effective_from:
        raise TaxRuleError("The rate ends before it starts.", "invalid_period")
    now = utc_now()
    with open_books_store(context) as store:
        S.settings_or_409(store)
        settings = settings_of(store)
        if settings is None:
            raise S.BooksError(409, "Save the tax settings first.", "tax_not_set_up")
        row = TaxRate(id=new_id("btaxr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, jurisdiction=settings.jurisdiction,
                      code=payload.code.value, rate_pct=rate, effective_from=payload.effective_from, effective_to=payload.effective_to,
                      note=S.clean(payload.note, 200), created_by=context.user_id)
        store.add("tax_rates", row)
    _audit(repository, context, "tax_rate_added", row.id, {"code": row.code, "rate_pct": str(rate), "effective_from": row.effective_from.isoformat()})
    return rate_view(row)


@router.delete("/rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
@refusals
@_tax
def delete_rate(rate_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Only a rate that has not started yet is removed; a rate in use is ended by adding the next one."""
    require_accounting(context)
    with open_books_store(context) as store:
        row = store.get("tax_rates", rate_id)
        if row is None:
            raise S.BooksError(404, "Rate not found.", "rate_not_found")
        if row.effective_from <= _today():
            raise S.BooksError(409, "This rate is already in use. Add a new rate from the day it changes instead.", "rate_in_use")
        store.delete("tax_rates", rate_id)
    _audit(repository, context, "tax_rate_removed", rate_id, {"code": row.code})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- the VAT return ------------------------------------------------------------------------------------------------------


def _return(context: TenantContext, start: date | None, end: date | None) -> dict[str, Any]:
    _read(context)
    first, last = _period(context, start, end)
    return TR.compute(context, first, last, today=_today())


@router.get("/return")
@refusals
def vat_return(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
               context: TenantContext = Depends(tenant_context)) -> Any:
    """Every box, the warnings, the notes and the tie-out to the journal's VAT accounts. Documents are read with ``/return/documents``."""
    data = _return(context, start, end)
    data.pop("_documents")
    return data


@router.get("/return/documents")
@refusals
def return_documents(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                     box: str | None = Query(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"),
                     limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                     context: TenantContext = Depends(tenant_context)) -> Any:
    """The documents behind one box (or all of them), oldest first, paged."""
    data = _return(context, start, end)
    rows = [row for row in data["_documents"] if box is None or row.box == box]
    base = data["base_currency"]
    return _page([TR.document_view(row, base) for row in rows[offset:offset + limit]], len(rows), limit, offset)


@router.get("/return.csv")
@refusals
def return_csv(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
               context: TenantContext = Depends(tenant_context)) -> Response:
    """The return with its notes, warnings and documents. A working draft: ANUM files nothing."""
    data = _return(context, start, end)
    base = data["base_currency"]
    out: list[list[Any]] = [["VAT return (working draft, not filed)", data["period"]["from"], data["period"]["to"], data["jurisdiction"], base],
                            ["box", "label", "amount", "vat", "adjustment_amount", "adjustment_vat", "documents"]]
    out += [[row["box"], row["label"], row["amount"], row["vat"], row["adjustment_amount"], row["adjustment_vat"], row["documents"]] for row in data["boxes"]]
    out += [[row["key"], row["label"], row["amount"], row["vat"], "", "", row["documents"]] for row in data["info"]]
    out.append([])
    out.append(["complete", str(data["complete"]).lower(), "closed", str(data["closed"]).lower(), "ties_to_journal", str(data["journal"]["ties"]).lower()])
    out += [["warning", item["code"], item["text"]] for item in data["warnings"]]
    out += [["note", "", text] for text in data["notes"]]
    out.append([])
    out.append(["entry_id", "source_kind", "source_id", "description", "entry_date", "event_date", "box", "tax_code", "inferred", "adjustment", "net", "vat"])
    for row in data["_documents"]:
        view = TR.document_view(row, base)
        out.append([view[name] for name in ("entry_id", "source_kind", "source_id", "description", "entry_date", "event_date", "box", "tax_code",
                                            "inferred", "adjustment", "net", "vat")])
    return _csv(out, f"vat-return-{data['period']['from']}-{data['period']['to']}.csv")


# -- ZATCA readiness ------------------------------------------------------------------------------------------------------


@router.get("/zatca")
@refusals
def zatca_panel(limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                context: TenantContext = Depends(tenant_context)) -> Any:
    """Readiness (what is missing), the not-verified notice, the chain position and the sent invoices with their e-invoice data."""
    from .invoice_models import InvoiceStatus
    from .store import Query as BusinessQuery
    from .store import open_business_store

    _read(context)
    with open_business_store(context) as business:
        billing = business.get_settings().billing
        invoices, total = business.find("invoices", BusinessQuery(where={"status": {InvoiceStatus.SENT, InvoiceStatus.PAID}}, order_by="created_at",
                                                                  descending=True, limit=limit, offset=offset))
    with open_books_store(context) as store:
        S.settings_or_409(store)
        settings = settings_of(store)
        chain = store.first("zatca_chain")
        ids = [item.id for item in invoices]
        docs = {row.source_id: row for row in store.find("zatca_documents", where={"source_kind": "invoice", "source_id": ids}, limit=len(ids) + 1)[0]} if ids else {}
        _, generated = store.find("zatca_documents", limit=1)
    rows = [{"invoice_id": item.id, "number": item.number, "issue_date": item.issue_date.isoformat() if item.issue_date else None,
             "currency": item.currency.value, "total": str(item.total), "tax": str(item.tax), "buyer": item.bill_to_name,
             "kind": "standard" if item.bill_to_tax_number.strip() else "simplified",
             "uncoded_lines": sum(1 for line in item.lines if line.tax_code is None),
             "document": Z.document_view(docs[item.id]) if item.id in docs else None} for item in invoices]
    return {**Z.readiness(settings, billing), "can_edit": can_account(context), "counter": chain.last_counter if chain else 0,
            "last_hash": chain.last_hash if chain else "", "generated": generated, "invoices": _page(rows, total, limit, offset)}


class GenerateInput(BaseModel):
    source_kind: Literal["invoice", "credit_note"] = "invoice"
    source_id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/zatca/generate")
@refusals
@_tax
def zatca_generate(payload: GenerateInput, response: Response, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Any:
    require_accounting(context)
    record, made = Z.generate(context, payload.source_kind, payload.source_id, actor=context.user_id)
    if made:
        response.status_code = status.HTTP_201_CREATED
        _audit(repository, context, "zatca_generated", record.id, {"source_kind": record.source_kind, "source_id": record.source_id,
                                                                    "counter": record.counter})
    return {**Z.document_view(record), "replayed": not made}


@router.get("/zatca/documents")
@refusals
def zatca_documents(limit: int = Query(default=25, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                    context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    with open_books_store(context) as store:
        rows, total = store.find("zatca_documents", order_by="counter", descending=True, limit=limit, offset=offset)
    return _page([Z.document_view(row) for row in rows], total, limit, offset)


@router.get("/zatca/documents/{document_id}/xml")
@refusals
def zatca_xml(document_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _read(context)
    with open_books_store(context) as store:
        row = store.get("zatca_documents", document_id)
    if row is None:
        raise S.BooksError(404, "E-invoice data not found.", "document_not_found")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in row.document_number) or "invoice"
    return Response(content=row.xml, media_type="application/xml; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.xml"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.post("/zatca/documents/{document_id}/submit")
@refusals
@_tax
def zatca_submit(document_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    """Clearance or reporting with ZATCA: refused until the business is onboarded (never in this release)."""
    require_accounting(context)
    with open_books_store(context) as store:
        row = store.get("zatca_documents", document_id)
    if row is None:
        raise S.BooksError(404, "E-invoice data not found.", "document_not_found")
    gateway = Z.gateway()
    return gateway.clear(row) if row.subtype == Z.STANDARD else gateway.report(row)


__all__ = ["router"]


# The VAT return warnings in the Accountant review (docs/business-tax.md#warnings-in-the-accountant-review); the module
# registers its producer when imported.
from . import tax_review as _tax_review  # noqa: E402,F401
