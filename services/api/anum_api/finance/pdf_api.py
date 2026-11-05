"""PDF statement import over HTTP (docs/finance.md#pdf-statements).

``POST /api/v1/finance/imports/pdf/read`` reads a statement PDF (the raw file as the body) and answers
the rows, the period, the account's last four digits, the balances and the balance check. Nothing is
saved: the file and the password live in this request's memory only. The person then reviews the rows
(fixes the column mapping, edits or drops rows, picks the account) and sends them to
``POST /api/v1/finance/imports/pdf``, which records them through the same path as a CSV import
(``api.record_import``): duplicates skipped, payments already recorded from an SMS or Apple Pay attached
as a ``statement`` source, ambiguous ones held for Review, and the import can be undone.

Both routes need ``finance:manage``. The file is parsed off the event loop; no store is open while the
OCR service reads a scan. Audit rows hold ids and codes only: never amounts, text or the password.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..dependencies import UnitsOfWork, repository_context, tenant_context, units_of_work
from ..errors import ApplicationError, ErrorCode, ErrorDetail
from ..files import read_upload_body
from ..repository import AnumRepository
from ..schemas import TenantContext
from . import csv_import
from . import pdf_statement as pdf
from .api import ImportView, _audit, _clean, _invalid, _manage, _own_account, _view, record_import
from .categories import categorize
from .models import Category
from .money import MoneyError, quantize
from .store import open_finance_store
from .summary import settings_for

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

PASSWORD_HEADER = "X-Statement-Password"  # nosec B105
_STATUS = {
    "password_required": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "password_wrong": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "ocr_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _refuse(error: pdf.PdfStatementError) -> ApplicationError:
    code = _STATUS.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT)
    kind = {413: ErrorCode.PAYLOAD_TOO_LARGE, 503: ErrorCode.SERVICE_UNAVAILABLE}.get(code, ErrorCode.VALIDATION_ERROR)
    return ApplicationError(kind, error.message, status_code=code, details=[ErrorDetail(type=f"pdf_{error.code}", message=error.message)])


class PdfColumn(BaseModel):
    index: int
    label: str
    role: str


class PdfRow(BaseModel):
    line: int
    page: int
    posted_on: date | None
    description: str
    amount: Decimal | None
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None
    reference: str | None
    category: Category
    duplicate: bool
    flags: list[str]


class PdfCheck(BaseModel):
    status: Literal["matched", "mismatch", "unknown"]
    opening: Decimal | None
    closing: Decimal | None
    rows_total: Decimal
    expected_closing: Decimal | None
    difference: Decimal | None
    opening_source: str
    closing_source: str
    breaks: list[int]


class PdfStatementView(BaseModel):
    account_id: str
    currency: str
    filename: str
    file_sha256: str
    source: Literal["text", "ocr"]
    page_count: int
    layout: Literal["table", "lines"]
    columns: list[PdfColumn]
    roles: list[str]
    date_format: str
    date_formats: list[str]
    date_format_ambiguous: bool
    period_start: date | None
    period_end: date | None
    account_last4: str | None
    account_last4_matches: bool | None
    statement_currency: str | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    check: PdfCheck
    row_count: int
    duplicate_count: int
    skipped_lines: int
    warnings: list[str]
    rows: list[PdfRow]


def _roles(value: str | None) -> list[str] | None:
    if not value:
        return None
    roles = [part.strip() for part in value.split(",")]
    if len(roles) > pdf.MAX_COLUMNS or any(r not in pdf.ROLES for r in roles):
        raise _invalid("Unknown column role. Use date, description, debit, credit, amount, balance, reference or skip.")
    return roles


def _filename(value: str | None) -> str:
    name = _clean(value, 200) or "statement.pdf"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def _occurrence_hashes(account_id: str, currency: str, items: list[tuple[date, Decimal, str]]) -> list[str]:
    """The CSV import's dedupe hashes, so a PDF and a CSV of the same statement never double count."""
    seen: dict[tuple[date, Decimal, str], int] = {}
    out = []
    for posted, amount, description in items:
        key = (posted, amount, " ".join(description.lower().split()))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        out.append(csv_import.dedupe_hash(account_id, posted, amount, currency, description, occurrence))
    return out


@router.post("/imports/pdf/read", response_model=PdfStatementView)
async def read_pdf_statement(
    request: Request,
    account_id: str = Query(min_length=1, max_length=80),
    filename: str | None = Query(default=None, max_length=200),
    roles: str | None = Query(default=None, max_length=200, description="One role per detected column, comma separated"),
    date_format: str | None = Query(default=None, max_length=20),
    password: str | None = Header(default=None, alias=PASSWORD_HEADER, max_length=pdf.MAX_PASSWORD),
    context: TenantContext = Depends(tenant_context),
    uow: UnitsOfWork = Depends(units_of_work),
) -> PdfStatementView:
    """Read a statement PDF and show its rows and balance check. Nothing is saved."""
    _manage(context)
    wanted_roles = _roles(roles)

    def account() -> Any:
        with open_finance_store(context) as store:
            return _own_account(store, context, account_id)

    found = await asyncio.to_thread(account)
    content = await read_upload_body(request, pdf.MAX_PDF_BYTES)
    try:
        extracted = await asyncio.to_thread(pdf.extract, content, password or None)
        if extracted.source == "ocr":
            # No store or session is open while the OCR service reads the pages.
            pages = await pdf.read_scanned(extracted.images)
        else:
            pages = extracted.pages
        statement = await asyncio.to_thread(
            pdf.parse, pages, currency=found.currency, roles=wanted_roles, date_format=date_format, positioned=extracted.source == "text",
        )
    except pdf.PdfStatementError as exc:
        def refused() -> None:
            with uow(context) as repository:
                _audit(repository, context, "finance.import.pdf_read", account_id, {"outcome": exc.code})

        await asyncio.to_thread(refused)
        raise _refuse(exc) from None

    dated = [(r, r.posted_on, r.amount) for r in statement.rows]
    hashes = _occurrence_hashes(
        found.id, found.currency,
        [(posted, quantize(amount, found.currency), r.description) for r, posted, amount in dated if posted and amount is not None],
    )

    def lookup() -> tuple[set[str], Any]:
        with open_finance_store(context) as store:
            return store.existing_hashes(context, found.id, hashes), settings_for(store, context).category_rules

    existing, rules = await asyncio.to_thread(lookup)
    views: list[PdfRow] = []
    index = 0
    duplicates = 0
    for row in statement.rows:
        duplicate = False
        if row.posted_on and row.amount is not None:
            duplicate = hashes[index] in existing
            index += 1
        duplicates += duplicate
        category = categorize(row.description, row.amount or Decimal(0), rules)[0]
        views.append(PdfRow(
            line=row.line, page=row.page, posted_on=row.posted_on, description=row.description, amount=row.amount, debit=row.debit,
            credit=row.credit, balance=row.balance, reference=row.reference, category=category, duplicate=duplicate, flags=row.flags,
        ))
    check = statement.check

    def audit() -> None:
        with uow(context) as repository:
            _audit(repository, context, "finance.import.pdf_read", account_id, {
                "outcome": "read", "source": extracted.source, "pages": extracted.page_count, "rows": len(views), "check": check.status,
                "layout": statement.layout,
            })

    await asyncio.to_thread(audit)
    last4 = statement.account_last4
    return PdfStatementView(
        account_id=found.id, currency=found.currency, filename=_filename(filename), file_sha256=extracted.sha256, source=extracted.source,  # type: ignore[arg-type]
        page_count=extracted.page_count, layout=statement.layout,  # type: ignore[arg-type]
        columns=[PdfColumn(index=c.index, label=c.label, role=c.role) for c in statement.columns], roles=[c.role for c in statement.columns],
        date_format=statement.date_format, date_formats=statement.date_formats, date_format_ambiguous=statement.date_format_ambiguous,
        period_start=statement.period_start, period_end=statement.period_end, account_last4=last4,
        account_last4_matches=None if not (last4 and found.last4) else last4 == found.last4,
        statement_currency=statement.currency, opening_balance=statement.opening_balance, closing_balance=statement.closing_balance,
        check=PdfCheck(**check.__dict__), row_count=len(views), duplicate_count=duplicates, skipped_lines=statement.skipped_lines,
        warnings=statement.warnings, rows=views,
    )


class PdfImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posted_on: date
    description: str = Field(min_length=1, max_length=csv_import.MAX_DESCRIPTION)
    amount: Decimal
    balance: Decimal | None = None
    reference: str | None = Field(default=None, max_length=60)


class PdfImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(default="statement.pdf", min_length=1, max_length=200)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["text", "ocr"] = "text"
    rows: list[PdfImportRow] = Field(min_length=1, max_length=csv_import.MAX_ROWS)
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    accept_mismatch: bool = Field(default=False, description="Import even though the rows do not add up to the closing balance")

    @field_validator("rows")
    @classmethod
    def _years(cls, rows: list[PdfImportRow]) -> list[PdfImportRow]:
        for row in rows:
            if not 1990 <= row.posted_on.year <= 2100:
                raise ValueError("A row's date is out of range.")
        return rows


@router.post("/imports/pdf", response_model=ImportView, status_code=status.HTTP_201_CREATED)
def import_pdf_statement(
    body: PdfImportInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ImportView:
    """Record the reviewed rows of a PDF statement through the same path as a CSV import."""
    _manage(context)
    references: dict[str, str] = {}

    def build(store: Any, account: Any) -> csv_import.ParsedFile:
        currency = account.currency
        items = []
        for number, row in enumerate(body.rows, start=1):
            description = pdf.mask_numbers(csv_import.clean_text(row.description))
            if not description:
                raise _invalid(f"Row {number}: the description is empty.")
            try:
                amount = quantize(row.amount, currency)
                balance = quantize(row.balance, currency) if row.balance is not None else None
            except MoneyError as exc:
                raise _invalid(f"Row {number}: {exc}") from None
            if amount == 0:
                raise _invalid(f"Row {number}: the amount is zero. Fix it or drop the row.")
            items.append((row.posted_on, amount, description, balance, row.reference))
        try:
            opening = quantize(body.opening_balance, currency) if body.opening_balance is not None else None
            closing = quantize(body.closing_balance, currency) if body.closing_balance is not None else None
        except MoneyError as exc:
            raise _invalid(str(exc)) from None
        check = pdf.verify([(i, amount, balance) for i, (_, amount, _, balance, _) in enumerate(items, start=1)], opening, closing)
        if check.status == "mismatch" and not body.accept_mismatch:
            raise ApplicationError(
                ErrorCode.VALIDATION_ERROR,
                "The rows do not add up to the closing balance. Fix the rows, or confirm that you want to import them anyway.",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                details=[ErrorDetail(type="pdf_balance_mismatch", message=str(check.difference) if check.difference is not None else "")],
            )
        hashes = _occurrence_hashes(account.id, currency, [(posted, amount, description) for posted, amount, description, _, _ in items])
        rows = []
        for number, ((posted, amount, description, _, reference), digest) in enumerate(zip(items, hashes, strict=True), start=1):
            rows.append(csv_import.ParsedRow(number, posted, amount, description, digest))
            if reference:
                clean = re.sub(r"[^\w\-/]", "", reference)[:60]
                if clean:
                    references[digest] = clean
        return csv_import.ParsedFile(
            headers=[], preset="pdf_scan" if body.source == "ocr" else "pdf",
            mapping=csv_import.ColumnMapping(date="date", description="description", amount="amount"),
            date_format="pdf", date_formats=[], date_format_ambiguous=False, row_count=len(rows), rows=rows, errors=[],
            file_sha256=body.file_sha256,
        )

    record, added, breaks, reconciled = record_import(context, body.account_id, _filename(body.filename), build, references=references)
    _audit(repository, context, "finance.import.create", record.id, {
        "format": record.preset, "imported": added, "duplicates": record.duplicate_count, "rule_breaks": breaks,
        "reconciled": reconciled.attached, "review": reconciled.review, "accept_mismatch": body.accept_mismatch,
    })
    return ImportView(**_view(record), rule_breaks=breaks, reconciled=reconciled.attached, held_for_review=reconciled.review)


__all__ = ["PASSWORD_HEADER", "router"]
