"""Supporting documents and the evidence pack under ``/api/v1/business/books`` (docs/business-books.md#supporting-documents-and-evidence).

* ``GET /evidence?source_type&ids`` (``business:read``): the documents attached to each record and whether it needs one.
* ``POST /evidence`` and ``DELETE /evidence/{source_type}/{source_id}/{link_id}``: ``business:write`` for bills and invoices,
  owners and accountants for adjustments and manual journal entries; the document rules of the vault apply too.
* ``GET /evidence/missing?from&to`` (``business:read``): what is above the threshold with nothing attached.
* ``GET /evidence/receipt-matches`` (``business:read`` and the Document vault): receipts proposed for bills without evidence
  (``books_receipts.py``); ``POST /evidence/receipt-matches/accept`` attaches one (the attach rules above, audited).
* ``GET /evidence-pack.csv`` and ``GET /evidence-pack.zip`` (owners and accountants, audited): the period's index and files.

Audit rows hold ids and codes only. Every route is a plain ``def`` (the database and file work runs in the thread pool).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext
from . import books_evidence as E
from . import books_service as S
from .api_books import _accounting, _audit, _read, _today, _write, refusals
from .books_store import open_books_store

router = APIRouter(prefix="/api/v1/business/books", tags=["business"])
SourceType = Literal["bill", "invoice", "adjustment", "journal"]
SAFE_ID = r"^[A-Za-z0-9_.:-]+$"
DOCUMENT_DENIED = "Reading business evidence needs access to the Document vault. Ask a workspace owner."


def _may_attach(context: TenantContext, source_type: str) -> None:
    if source_type in ("adjustment", "journal"):
        _accounting(context)
    else:
        _write(context)
    if not has_permission(context, Permission.DOCUMENT_WRITE):
        raise S.BooksError(403, "Attaching documents needs permission to add documents to the Document vault. Ask a workspace owner.", "forbidden")


def _documents(context: TenantContext) -> None:
    if not has_permission(context, Permission.DOCUMENT_READ):
        raise S.BooksError(403, DOCUMENT_DENIED, "forbidden")


def _period(context: TenantContext, start: date | None, end: date | None) -> tuple[date, date]:
    from . import books_report as R
    from .api_books import MAX_SPAN_DAYS

    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
    first, last = R.default_period(settings, _today())
    start, end = start or first, end or last
    if start > end:
        raise S.BooksError(422, "The period starts after it ends.", "invalid_period")
    if (end - start).days > MAX_SPAN_DAYS:
        raise S.BooksError(422, "An evidence pack covers at most three years.", "period_too_long")
    return start, end


def _records(context: TenantContext, source_type: str, ids: list[str]) -> list[Any]:
    kind = {"bill": "bills", "adjustment": "adjustments"}.get(source_type)
    if kind is None:
        return []
    with open_books_store(context) as store:
        rows, _ = store.find(kind, where={"id": set(ids)}, limit=len(ids) + 1)
    return rows


@router.get("/evidence")
@refusals
def list_evidence(source_type: SourceType, ids: str = Query(min_length=1, max_length=4000),
                  context: TenantContext = Depends(tenant_context)) -> Any:
    """For up to 100 records of one type: the attached documents the caller can see, and whether each needs one."""
    _read(context)
    _documents(context)
    wanted = [i for i in dict.fromkeys(part.strip() for part in ids.split(",")) if i]
    if not wanted or len(wanted) > 100 or any(len(i) > 80 or not re.match(SAFE_ID, i) for i in wanted):
        raise S.BooksError(422, "Ask for 1 to 100 record ids.", "invalid_ids")
    docs = E.attached(context, source_type, wanted)
    needed = E.flags(context, source_type, _records(context, source_type, wanted))
    return {"items": {i: {"documents": docs.get(i, []), "evidence_missing": bool(needed.get(i))} for i in wanted},
            "can_attach": has_permission(context, Permission.DOCUMENT_WRITE)
            and (has_permission(context, Permission.BUSINESS_WRITE) if source_type in ("bill", "invoice") else _can_account(context))}


def _can_account(context: TenantContext) -> bool:
    try:
        _accounting(context)
    except S.BooksError:
        return False
    return True


class AttachInput(BaseModel):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=80, pattern=SAFE_ID)
    document_id: str = Field(min_length=1, max_length=80, pattern=SAFE_ID)


@router.post("/evidence", status_code=status.HTTP_201_CREATED)
@refusals
def attach_evidence(payload: AttachInput, response: Response, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Any:
    _may_attach(context, payload.source_type)
    result = E.attach(context, payload.source_type, payload.source_id, payload.document_id)
    if result.pop("replayed"):
        response.status_code = status.HTTP_200_OK
    else:
        _audit(repository, context, "evidence_attached", payload.source_id, {"source_type": payload.source_type, "document": payload.document_id})
    return result


@router.delete("/evidence/{source_type}/{source_id}/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
@refusals
def detach_evidence(source_type: SourceType, source_id: str, link_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Response:
    _may_attach(context, source_type)
    document_id = E.detach(context, source_type, source_id, link_id)
    _audit(repository, context, "evidence_detached", source_id, {"source_type": source_type, "document": document_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReceiptMatchInput(BaseModel):
    bill_id: str = Field(min_length=1, max_length=80, pattern=SAFE_ID)
    document_id: str = Field(min_length=1, max_length=80, pattern=SAFE_ID)


@router.get("/evidence/receipt-matches")
@refusals
def receipt_matches(context: TenantContext = Depends(tenant_context)) -> Any:
    """Receipts in the Document vault that match a bill with no document attached, by amount, date and vendor. Suggestions only."""
    from . import books_receipts as RM

    _read(context)
    _documents(context)
    items = [m.view() for m in RM.candidates(context, _today())]
    return {"items": items, "total": len(items), "window_days": RM.WINDOW_DAYS}


@router.post("/evidence/receipt-matches/accept", status_code=status.HTTP_201_CREATED)
@refusals
def accept_receipt_match(payload: ReceiptMatchInput, context: TenantContext = Depends(tenant_context),
                         repository: AnumRepository = Depends(repository_context)) -> Any:
    """Attach a proposed receipt to its bill (only when the person accepts it)."""
    from . import books_receipts as RM

    _may_attach(context, "bill")
    result = RM.accept(context, _today(), payload.bill_id, payload.document_id)
    result.pop("replayed", None)
    _audit(repository, context, "receipt_match_accepted", payload.bill_id, {"document": payload.document_id})
    return result


@router.get("/evidence/missing")
@refusals
def missing_evidence(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                     context: TenantContext = Depends(tenant_context)) -> Any:
    _read(context)
    _documents(context)
    first, last = _period(context, start, end)
    return {**E.missing(context, first, last), "period": {"from": first.isoformat(), "to": last.isoformat()}}


@router.get("/evidence-pack.csv")
@refusals
def evidence_index(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                   context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Every journal entry of the period with its source document and the attached evidence (owners and accountants)."""
    _accounting(context)
    _documents(context)
    first, last = _period(context, start, end)
    rows, docs, total = E.pack_rows(context, first, last)
    _audit(repository, context, "evidence_pack", f"{first.isoformat()}..{last.isoformat()}", {"format": "csv", "entries": total, "documents": len(docs)})
    return Response(content=E.to_csv(rows), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="evidence-index-{first.isoformat()}-{last.isoformat()}.csv"', "Cache-Control": "no-store"})


@router.get("/evidence-pack.zip")
@refusals
def evidence_pack(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                  context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """The index plus every attached file the caller may read (owners and accountants)."""
    _accounting(context)
    _documents(context)
    first, last = _period(context, start, end)
    rows, docs, total = E.pack_rows(context, first, last)
    content, included, left_out = E.pack_zip(context, rows, docs)
    _audit(repository, context, "evidence_pack", f"{first.isoformat()}..{last.isoformat()}",
           {"format": "zip", "entries": total, "files": included, "left_out": left_out})
    return Response(content=content, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="evidence-pack-{first.isoformat()}-{last.isoformat()}.zip"', "Cache-Control": "no-store"})


__all__ = ["router"]


# The receipts producer of the Accountant review registers itself when imported.
from . import books_receipts as _books_receipts  # noqa: E402,F401
