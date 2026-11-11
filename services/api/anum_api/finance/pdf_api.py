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
import logging
import re
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..dependencies import UnitsOfWork, repository_context, tenant_context, units_of_work
from ..errors import ApplicationError, ErrorCode, ErrorDetail
from ..files import read_upload_body
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from ..vault.ocr_client import OcrUnavailable
from . import csv_import
from . import statement_pdf as pdf
from . import statement_import as SI
from .api import ImportView, _audit, _clean, _invalid, _manage, _own_account, preview_matches, record_import
from .statement_match import Line
from .categories import categorize
from .models import Category
from .money import MoneyError, quantize
from .pdf_templates import MAX_TEMPLATES, StatementTemplate, open_template_store
from .store import open_finance_store
from .summary import settings_for, zone

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
logger = logging.getLogger(__name__)

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
    currency: str | None
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    fee: Decimal | None
    vat: Decimal | None
    kind: str | None
    time: str | None
    counterparty: str | None
    parent_line: int | None
    category: Category
    duplicate: bool
    # 0 to 1: the reading's certainty (OCR quality, how the sign was found, whether the balance agrees).
    confidence: float
    flags: list[str]
    # The matching engine's answer (docs/finance.md#statement-matching); None for a row without a date or amount.
    match: SI.RowMatchView | None = None
    fx: SI.FxView | None = None


class PdfSummaryCheck(BaseModel):
    name: str
    stated: Decimal
    found: Decimal
    ok: bool


class PdfSkippedPage(BaseModel):
    page: int
    reason: Literal["not_transactions", "no_text"]


class PdfDroppedRow(BaseModel):
    """A row or line the reader left out, and why (docs/finance-statements.md#rows-left-out): shown, never silent."""

    page: int
    reason: Literal["outside_period", "unattached_line"]
    text: str
    posted_on: date | None
    amount: Decimal | None


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
    # Pages whose balance brought forward is not the balance the page before ended with.
    page_breaks: list[int]
    # The statement's own counts and totals (deposits, withdrawals, amount due) against the rows read.
    summary: list[PdfSummaryCheck]


class PdfStatementView(BaseModel):
    account_id: str
    currency: str
    filename: str
    file_sha256: str
    source: Literal["text", "ocr"]
    page_count: int
    layout: Literal["table", "columns", "lines"]
    columns: list[PdfColumn]
    roles: list[str]
    # The layout's fingerprint (header words and column positions) and the saved template used, if any.
    fingerprint: str | None
    template_id: str | None
    confidence: float
    kind: Literal["account", "card"]
    statement_date: date | None
    due_date: date | None
    skipped_pages: list[PdfSkippedPage]
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
    counts: SI.MatchCounts
    # Rows dated outside the statement's period and lines no row could take: left out of the rows, listed here.
    dropped_rows: list[PdfDroppedRow] = []


def _roles(value: str | None) -> list[str] | None:
    if not value:
        return None
    roles = [part.strip() for part in value.split(",")]
    if len(roles) > 12 or any(r not in pdf.ROLES for r in roles):
        raise _invalid("Unknown column role. Use date, description, debit, credit, amount, balance, reference, currency or skip.")
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
            words = [w for page in pages for w in page]
            positioned = bool(words) and sum(w.positioned for w in words) * 2 > len(words)
            widths = [max((w.x1 for w in page), default=0.0) for page in pages]
        else:
            pages, positioned, widths = list(extracted.pages), True, list(extracted.widths)
            for number, image in extracted.image_pages.items():
                # A page that is only a picture: read it when OCR is there; the reader decides if it holds rows.
                try:
                    found_words = await pdf.ocr.ocr_page(image.data, image.content_type, number)
                except OcrUnavailable:
                    # No OCR here, or it failed: the page stays unread and the preview lists it as such.
                    logger.info("finance pdf picture page unread outcome=ocr_unavailable")
                    found_words = []
                if found_words and all(w.positioned for w in found_words):
                    pages[number - 1] = found_words
                    widths[number - 1] = max(w.x1 for w in found_words)

        def read() -> tuple[Any, StatementTemplate | None]:
            statement = pdf.parse(pages, currency=found.currency, roles=wanted_roles, date_format=date_format, positioned=positioned, page_widths=widths)
            template = None
            if wanted_roles is None and date_format is None and statement.fingerprint:
                # A layout the owner taught before: read it with their mapping.
                with open_template_store(context) as templates:
                    template = templates.by_fingerprint(context, statement.fingerprint)
                if template is not None and len(template.roles) == len(statement.columns):
                    try:
                        statement = pdf.parse(pages, currency=found.currency, roles=template.roles, date_format=template.date_format,
                                              positioned=positioned, page_widths=widths)
                    except pdf.PdfStatementError:
                        template = None
                else:
                    template = None
            return statement, template

        statement, template = await asyncio.to_thread(read)
    except pdf.PdfStatementError as exc:
        def refused() -> None:
            with uow(context) as repository:
                _audit(repository, context, "finance.import.pdf_read", account_id, {"outcome": exc.code})

        await asyncio.to_thread(refused)
        raise _refuse(exc) from None

    dated = [(r, r.posted_on, r.amount) for r in statement.rows]
    # The import hashes the same rows (unticked ones too) with the same cleaned text, so a row keeps its occurrence number.
    hashes = _occurrence_hashes(
        found.id, found.currency,
        [(posted, quantize(amount, found.currency), pdf.mask_numbers(csv_import.clean_text(r.description))) for r, posted, amount in dated
         if posted and amount is not None],
    )

    readable = [(r, posted, quantize(amount, found.currency)) for r, posted, amount in dated if posted and amount is not None]
    lines = [Line(key=position, posted_on=posted, amount=amount, description=r.description, dedupe_hash=digest, reference=r.reference,
                  original_amount=abs(r.original_amount) if r.original_amount is not None else None, original_currency=r.original_currency)
             for position, ((r, posted, amount), digest) in enumerate(zip(readable, hashes, strict=True))]

    def lookup() -> tuple[list[Any], Any]:
        # The matching engine runs in its own short unit of work, off the event loop.
        matches = preview_matches(context, found, lines)
        with open_finance_store(context) as store:
            from .rules_store import owner_rules

            return matches, owner_rules(store, context)

    matches, rules = await asyncio.to_thread(lookup)
    views: list[PdfRow] = []
    index = 0
    duplicates = 0
    for row in statement.rows:
        duplicate = False
        match = None
        if row.posted_on and row.amount is not None:
            match = matches[index]
            duplicate = match.status == "exact_duplicate"
            index += 1
        duplicates += duplicate
        category = categorize(row.description, row.amount or Decimal(0), rules, found.kind, account_id=found.id, currency=found.currency)[0]
        views.append(PdfRow(
            line=row.line, page=row.page, posted_on=row.posted_on, description=row.description, amount=row.amount, debit=row.debit,
            credit=row.credit, balance=row.balance, reference=row.reference, currency=row.currency, original_amount=row.original_amount,
            original_currency=row.original_currency, fx_rate=row.fx_rate, fee=row.fee, vat=row.vat, kind=row.kind,
            time=row.time.isoformat() if row.time else None, counterparty=row.counterparty, parent_line=row.parent_line,
            category=category, duplicate=duplicate, confidence=row.confidence, flags=row.flags,
            match=SI.match_view(match) if match is not None else None,
            fx=SI.fx_view(row.description, row.amount, found.currency, original_amount=row.original_amount, original_currency=row.original_currency,
                          rate=row.fx_rate, fee=row.fee) if row.amount is not None else None,
        ))
    check = statement.check

    def audit() -> None:
        with uow(context) as repository:
            _audit(repository, context, "finance.import.pdf_read", account_id, {
                "outcome": "read", "source": extracted.source, "pages": extracted.page_count, "rows": len(views), "check": check.status,
                "layout": statement.layout, "template": bool(template),
            })

    await asyncio.to_thread(audit)
    last4 = statement.account_last4
    return PdfStatementView(
        account_id=found.id, currency=found.currency, filename=_filename(filename), file_sha256=extracted.sha256, source=extracted.source,  # type: ignore[arg-type]
        page_count=extracted.page_count, layout=statement.layout,  # type: ignore[arg-type]
        columns=[PdfColumn(index=c.index, label=c.label, role=c.role) for c in statement.columns], roles=[c.role for c in statement.columns],
        fingerprint=statement.fingerprint, template_id=template.id if template else None, confidence=statement.confidence,
        date_format=statement.date_format, date_formats=statement.date_formats, date_format_ambiguous=statement.date_format_ambiguous,
        period_start=statement.period_start, period_end=statement.period_end, account_last4=last4,
        account_last4_matches=None if not (last4 and found.last4) else last4 == found.last4,
        statement_currency=statement.currency, opening_balance=statement.opening_balance, closing_balance=statement.closing_balance,
        check=PdfCheck(**{**check.__dict__, "summary": [PdfSummaryCheck(**c.__dict__) for c in check.summary]}),
        kind=statement.kind, statement_date=statement.statement_date, due_date=statement.due_date,  # type: ignore[arg-type]
        skipped_pages=[PdfSkippedPage(page=p, reason=r) for p, r in statement.skipped_pages],  # type: ignore[arg-type]
        row_count=len(views), duplicate_count=duplicates, skipped_lines=statement.skipped_lines,
        warnings=statement.warnings, rows=views, counts=SI.counts(matches),
        dropped_rows=[PdfDroppedRow(page=d.page, reason=d.reason, text=d.text, posted_on=d.posted_on, amount=d.amount)  # type: ignore[arg-type]
                      for d in statement.dropped],
    )


class PdfImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posted_on: date
    description: str = Field(min_length=1, max_length=csv_import.MAX_DESCRIPTION)
    amount: Decimal
    balance: Decimal | None = None
    reference: str | None = Field(default=None, max_length=60)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    original_amount: Decimal | None = None
    original_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    billed_amount: Decimal | None = None
    billed_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    fx_rate: Decimal | None = None
    fee: Decimal | None = None
    vat: Decimal | None = None
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$", description="The time the statement prints for the row")
    # The person's choice on the preview's match (docs/finance.md#statement-matching); None follows the default.
    action: Literal["import", "skip"] | None = None
    # False: the person unticked the row. It is still sent, so every row keeps the occurrence number it had in the
    # preview (two identical rows stay two), but it is left out of the balance check and never imported.
    include: bool = True
    bill: bool | None = None
    transfer: bool | None = None
    correct: bool | None = None
    # The recorded payments (their ``record_id`` from the preview's ``splits``) this one row stands for: all of them are
    # attached to it or, if any changed since the preview, none (docs/finance.md#one-row-for-several-recorded-payments).
    split: list[str] | None = Field(default=None, min_length=2, max_length=4)


class PdfStatedFact(BaseModel):
    """One of the statement's printed counts or totals, checked again against the rows imported."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["deposits_count", "withdrawals_count", "deposits_total", "withdrawals_total", "total_due"]
    stated: Decimal


class PdfLayout(BaseModel):
    """The layout the rows were read with, kept as a template when ``remember`` is true."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(pattern=r"^[0-9a-f]{32}$")
    labels: list[str] = Field(max_length=12)
    roles: list[str] = Field(max_length=12)
    date_format: str | None = Field(default=None, max_length=20)
    remember: bool = False

    @field_validator("roles")
    @classmethod
    def _known(cls, roles: list[str]) -> list[str]:
        if any(r not in pdf.ROLES for r in roles):
            raise ValueError("Unknown column role.")
        return roles

    @field_validator("labels")
    @classmethod
    def _short(cls, labels: list[str]) -> list[str]:
        return [csv_import.clean_text(label, 60) for label in labels]


class PdfDroppedInput(BaseModel):
    """A row the reader left out, sent back from the preview so the import lists it (never imported)."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1, le=1000)
    reason: Literal["outside_period", "unattached_line"]
    text: str = Field(default="", max_length=500)
    posted_on: date | None = None
    amount: Decimal | None = None


class PdfImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(default="statement.pdf", min_length=1, max_length=200)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["text", "ocr"] = "text"
    rows: list[PdfImportRow] = Field(min_length=1, max_length=csv_import.MAX_ROWS)
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    accept_mismatch: bool = Field(default=False, description="Import even though the rows do not add up or disagree with the statement's summary")
    layout: PdfLayout | None = None
    summary: list[PdfStatedFact] = Field(default_factory=list, max_length=8, description="The statement's printed counts and totals, from the read")
    dropped: list[PdfDroppedInput] = Field(default_factory=list, max_length=500, description="The rows the reader left out, from the read")
    period_start: date | None = Field(default=None, description="The statement period printed on the statement, from the read")
    period_end: date | None = None

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
    times: dict[str, datetime] = {}
    checks: dict[str, Any] = {}

    def build(store: Any, account: Any) -> csv_import.ParsedFile:
        currency = account.currency
        items = []
        for number, row in enumerate(body.rows, start=1):
            description = pdf.mask_numbers(csv_import.clean_text(row.description))
            if not row.include:
                # Left out by the person: kept only for its place among identical rows, never checked or imported.
                try:
                    amount = quantize(row.billed_amount if row.billed_amount is not None else row.amount, currency)
                except MoneyError:
                    amount = row.amount
                items.append((row.posted_on, amount, description or "-", None, None, None, None, None, None, None))
                continue
            if not description:
                raise _invalid(f"Row {number}: the description is empty.")
            try:
                foreign = row.currency is not None and row.currency != currency
                if foreign and (row.billed_amount is None or row.billed_currency != currency):
                    raise MoneyError(
                        f"Foreign {row.currency} row needs the actual bank-billed {currency} amount. "
                        "Enter it in the PDF review before including this transaction."
                    )
                if row.billed_amount is not None and row.billed_currency not in (None, currency):
                    raise MoneyError(f"Billed currency {row.billed_currency} does not match {currency}.")
                if row.billed_amount is not None and row.amount != 0 and (row.billed_amount > 0) != (row.amount > 0):
                    raise MoneyError("The billed amount must have the same debit or credit direction as the original transaction.")
                amount = quantize(row.billed_amount if row.billed_amount is not None else row.amount, currency)
                balance = quantize(row.balance, currency) if row.balance is not None else None
                if amount == 0:
                    raise MoneyError("The posted amount is zero.")
                original = row.original_amount
                original_currency = row.original_currency
                if foreign:
                    original = original if original is not None else row.amount
                    original_currency = original_currency or row.currency
                if original is not None and not original_currency:
                    raise MoneyError("Original currency is missing.")
                details = []
                if original is not None:
                    details.append(f"Original: {abs(original)} {original_currency}")
                if row.billed_amount is not None:
                    details.append(f"Bank billed: {abs(amount)} {currency}")
                if row.fx_rate is not None:
                    if row.fx_rate <= 0:
                        raise MoneyError("Invalid FX rate.")
                    details.append(f"Bank FX rate: {row.fx_rate}")
                if row.fee is not None:
                    details.append(f"FX/transaction fee reported: {abs(row.fee)} {currency} (informational)")
                if row.vat is not None:
                    details.append(f"VAT reported: {abs(row.vat)} {currency} (informational)")
            except MoneyError as exc:
                raise _invalid(f"Row {number}: {exc}") from None
            items.append((row.posted_on, amount, description, balance, row.reference, row.time, " | ".join(details) or None, original, original_currency, row.fee))
        try:
            opening = quantize(body.opening_balance, currency) if body.opening_balance is not None else None
            closing = quantize(body.closing_balance, currency) if body.closing_balance is not None else None
        except MoneyError as exc:
            raise _invalid(str(exc)) from None
        counted = [(i, item) for i, item in enumerate(items, start=1) if body.rows[i - 1].include]
        check = pdf.verify([(i, item[1], item[3]) for i, item in counted], opening, closing)
        failed = _summary_failures(body.summary, [item[1] for _, item in counted], opening, currency)
        if failed and not body.accept_mismatch:
            raise ApplicationError(
                ErrorCode.VALIDATION_ERROR,
                "The rows do not match the statement's own counts or totals. Fix the rows, or confirm that you want to import them anyway.",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                details=[ErrorDetail(type="pdf_summary_mismatch", message=",".join(failed))],
            )
        if check.status == "mismatch" and not body.accept_mismatch:
            raise ApplicationError(
                ErrorCode.VALIDATION_ERROR,
                "The rows do not add up to the closing balance. Fix the rows, or confirm that you want to import them anyway.",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                details=[ErrorDetail(type="pdf_balance_mismatch", message=str(check.difference) if check.difference is not None else "")],
            )
        checks["reconciliation"] = SI.stored(SI.ReconciliationView(
            status={"matched": "tied", "mismatch": "differs"}.get(check.status, "unknown"), opening=check.opening, closing=check.closing,  # type: ignore[arg-type]
            rows_total=check.rows_total, expected_closing=check.expected_closing, difference=check.difference, opening_source=check.opening_source,
            closing_source=check.closing_source, breaks=check.breaks[:50]))
        if body.period_start and body.period_end and body.period_start <= body.period_end:
            checks["period"] = (body.period_start, body.period_end)
        hashes = _occurrence_hashes(account.id, currency, [(posted, amount, description) for posted, amount, description, _, _, _, _, _, _, _ in items])
        tz = zone(settings_for(store, context).timezone)
        rows = []
        for number, ((posted, amount, description, _, reference, at, notes, original, original_currency, fee), digest) in enumerate(zip(items, hashes, strict=True), start=1):
            rows.append(csv_import.ParsedRow(number, posted, amount, description, digest, notes, abs(original) if original is not None else None,
                                             original_currency, abs(fee) if fee is not None else None))
            if at:
                parts = [int(x) for x in at.split(":")] + [0]
                times[digest] = datetime.combine(posted, dtime(parts[0] % 24, parts[1] % 60, parts[2] % 60), tzinfo=tz)
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

    layout = body.layout
    if layout is not None and len(layout.labels) != len(layout.roles):
        raise _invalid("The layout needs one role for every column.")
    if layout is not None and layout.remember:
        with open_template_store(context) as templates:
            known = templates.list(context)
        if len(known) >= MAX_TEMPLATES and all(t.fingerprint != layout.fingerprint for t in known):
            raise _invalid(f"You have {MAX_TEMPLATES} saved statement layouts. Delete one you no longer use, then import again.")
    decisions = {number: (row if row.include else _LeftOut()) for number, row in enumerate(body.rows, start=1)}
    dropped = [{"posted_on": d.posted_on.isoformat() if d.posted_on else None, "amount": str(d.amount) if d.amount is not None else None,
                "description": pdf.mask_numbers(csv_import.clean_text(d.text))[:200], "reason": d.reason, "message": f"page {d.page}"}
               for d in body.dropped]
    record, added, breaks, outcome = record_import(context, body.account_id, _filename(body.filename), build, references=references, times=times,
                                                   decisions=decisions, checks=checks, dropped=dropped)
    template_id = _remember(context, layout) if layout is not None else None
    _audit(repository, context, "finance.import.create", record.id, {
        "format": record.preset, **outcome.audit(added, record.duplicate_count, breaks), "accept_mismatch": body.accept_mismatch,
        "template": template_id,
    })
    return outcome.view(record, breaks)


class _LeftOut:
    """The decision for an unticked row: left out (``skip``), nothing else."""

    action = "skip"
    bill = transfer = correct = None


def _summary_failures(stated: list[PdfStatedFact], amounts: list[Decimal], opening: Decimal | None, currency: str) -> list[str]:
    """The names of the statement's printed counts and totals the rows do not give (the reader's ``_summary``)."""
    credits = [a for a in amounts if a > 0]
    debits = [-a for a in amounts if a < 0]
    found = {
        "deposits_count": Decimal(len(credits)), "withdrawals_count": Decimal(len(debits)),
        "deposits_total": sum(credits, Decimal(0)), "withdrawals_total": sum(debits, Decimal(0)),
        # A card: the previous balance (owed, read as a minus opening) plus the charges is the amount due.
        "total_due": -(opening or Decimal(0)) - sum(amounts, Decimal(0)),
    }
    failed = []
    for fact in stated:
        expected = abs(fact.stated) if fact.name != "total_due" else fact.stated
        if quantize(found[fact.name], currency) != quantize(expected, currency):
            failed.append(fact.name)
    return failed


def _remember(context: TenantContext, layout: PdfLayout) -> str | None:
    """Keep (or refresh) the owner's mapping for this layout, or count a use of the one already kept."""
    now = utc_now()
    with open_template_store(context) as templates:
        found = templates.by_fingerprint(context, layout.fingerprint)
        if found is None and not layout.remember:
            return None
        if found is None:
            found = StatementTemplate(
                id=new_id("fin_tpl"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                fingerprint=layout.fingerprint, labels=layout.labels, roles=layout.roles, date_format=layout.date_format, created_at=now,
                updated_at=now,
            )
        elif layout.remember:
            found.labels, found.roles, found.date_format, found.updated_at = layout.labels, layout.roles, layout.date_format, now
        found.uses += 1
        found.last_used_at = now
        templates.save(found)
        return found.id


class TemplateView(BaseModel):
    id: str
    labels: list[str]
    roles: list[str]
    date_format: str | None
    uses: int
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


@router.get("/imports/pdf/templates", response_model=list[TemplateView])
def list_pdf_templates(context: TenantContext = Depends(tenant_context)) -> list[TemplateView]:
    """The statement layouts you taught ANUM (yours only)."""
    _manage(context)
    with open_template_store(context) as templates:
        items = templates.list(context)
    return [TemplateView(**t.model_dump(include=set(TemplateView.model_fields))) for t in items]


@router.delete("/imports/pdf/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pdf_template(
    template_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Forget a statement layout: the next statement from that bank is read from scratch."""
    _manage(context)
    with open_template_store(context) as templates:
        removed = templates.delete(context, template_id)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement layout not found.")
    _audit(repository, context, "finance.import.template_delete", template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["PASSWORD_HEADER", "router"]
