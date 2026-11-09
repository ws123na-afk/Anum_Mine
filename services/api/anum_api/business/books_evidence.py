"""Supporting documents and audit evidence for a business's books (docs/business-books.md#supporting-documents-and-evidence).

A receipt, contract or statement already in the business workspace's **Document vault** is attached to a bill, an
invoice, a journal adjustment or a manual journal entry as a vault document link (``vault_document_links``, target
types ``business_bill``, ``business_invoice``, ``business_adjustment`` and ``business_journal``, migration
``0157_finance_evidence``). There is no second store: the bytes stay the vault's Files object, its access rules and
its access log apply, and removing the link in the vault removes the evidence here.

* **Who attaches.** ``business:write`` for bills and invoices; an owner or an accountant for adjustments and manual
  entries (the same people who record them). The document must be a **workspace** document of this business that the
  caller can see (a private one would be invisible to the other owners and accountants: ``422 private_document``).
* **Evidence missing.** With a threshold in Settings, a live bill or an active adjustment whose amount in the base
  currency is at or above it and that has no attached document is flagged. A figure in another currency without a
  usable rate is flagged too (it cannot be shown to be below the threshold).
* **Evidence pack.** Owners and accountants export, for a period, a CSV index of every journal entry (posted or
  reversed) with its source document and the attached evidence, and a zip of the attached files they may read. An
  allocation row is listed by its six character source reference only: never the member's bank, account, card,
  merchant text or messages, which the books never hold.

Reads are short units of work; no transaction is held while files are read or zipped.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from ..schemas import TenantContext
from . import books_report as R
from . import books_service as S
from .books_store import MAX_SCAN, Between, open_books_store

SourceType = Literal["bill", "invoice", "adjustment", "journal"]
TARGETS: dict[str, str] = {"bill": "business_bill", "invoice": "business_invoice", "adjustment": "business_adjustment", "journal": "business_journal"}
MAX_PER_SOURCE = 20  # documents attached to one record
PACK_MAX_FILES = 500
PACK_MAX_BYTES = 200 * 1024 * 1024  # files past this are listed in the index as not included
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Source:
    """What a record is, for the evidence views: its date, amount and currency (amount ``None`` for a manual entry)."""

    type: str
    id: str
    on: date
    currency: str | None
    amount: Decimal | None
    reference: str


def _clean(value: str, limit: int = 120) -> str:
    return " ".join((value or "").split())[:limit]


def source(context: TenantContext, source_type: str, source_id: str) -> Source:
    """The record evidence is attached to, in this business; 404 when it is not here."""
    if source_type not in TARGETS:
        raise S.BooksError(422, "Attach evidence to a bill, an invoice, an adjustment or a manual journal entry.", "invalid_source")
    if source_type == "invoice":
        from .store import open_business_store

        with open_business_store(context) as business:
            invoice = business.get("invoices", source_id)
        if invoice is None:
            raise S.BooksError(404, "Invoice not found.", "invoice_not_found")
        return Source("invoice", invoice.id, invoice.issue_date or invoice.created_at.date(), str(getattr(invoice.currency, "value", invoice.currency)),
                      invoice.total, invoice.number or "")
    kind = {"bill": "bills", "adjustment": "adjustments", "journal": "journal_entries"}[source_type]
    with open_books_store(context) as store:
        found = store.get(kind, source_id)
    if found is None or (source_type == "journal" and found.source_kind != "manual"):
        label = {"bill": "Bill", "adjustment": "Adjustment", "journal": "Manual journal entry"}[source_type]
        raise S.BooksError(404, f"{label} not found.", f"{source_type}_not_found")
    if source_type == "bill":
        return Source("bill", found.id, found.bill_date, found.currency, found.total, found.reference)
    if source_type == "adjustment":
        return Source("adjustment", found.id, found.entry_date, found.currency, found.amount, found.kind.value)
    return Source("journal", found.id, found.entry_date, None, None, "")


def _view(link: Any, doc: Any) -> dict[str, Any]:
    return {"link_id": link.id, "document_id": doc.id, "title": doc.title, "category": doc.category, "added_by": link.created_by,
            "added_at": link.created_at.isoformat()}


def attached(context: TenantContext, source_type: str, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The documents attached to each record that the caller can see (others' private documents never show)."""
    from ..vault.store import open_vault_unit

    if not ids:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    with open_vault_unit(context) as unit:
        for link, doc in unit.store.links_to(context, TARGETS[source_type], list(dict.fromkeys(ids))):
            out.setdefault(link.target_id, []).append(_view(link, doc))
    return out


def attach(context: TenantContext, source_type: str, source_id: str, document_id: str) -> dict[str, Any]:
    from fastapi import HTTPException

    from ..vault import service as vault
    from ..vault.store import open_vault_unit

    found = source(context, source_type, source_id)
    with open_vault_unit(context) as unit:
        try:
            doc = vault.get_visible(unit, context, document_id)
        except HTTPException:
            raise S.BooksError(404, "Document not found in this business's Document vault.", "document_not_found") from None
        if doc.status != "active":
            raise S.BooksError(409, "This document is in the trash. Restore it first.", "document_deleted")
        if doc.access_class != "workspace":
            raise S.BooksError(422, "Attach a workspace document: a private one is hidden from the other owners and accountants. "
                               "Change who can see it in the Document vault.", "private_document")
        current = unit.store.links_to(context, TARGETS[source_type], [source_id])
    if any(d.id == document_id for _, d in current):
        link = next(l for l, d in current if d.id == document_id)
        return {**_view(link, doc), "replayed": True}
    if len(current) >= MAX_PER_SOURCE:
        raise S.BooksError(409, f"At most {MAX_PER_SOURCE} documents can be attached to one record.", "too_many_documents")
    label = f"{source_type} {found.reference}".strip()[:200]
    try:
        link = vault.add_link(context, document_id, TARGETS[source_type], source_id, label)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise S.BooksError(403, "You can attach only documents you added, unless you are a workspace owner.", "document_not_editable") from None
        raise
    return {**_view(link, doc), "replayed": False}


def detach(context: TenantContext, source_type: str, source_id: str, link_id: str) -> str:
    """Remove one attached document; answers the document id."""
    from fastapi import HTTPException

    from ..vault import service as vault
    from ..vault.store import open_vault_unit

    source(context, source_type, source_id)
    with open_vault_unit(context) as unit:
        current = unit.store.links_to(context, TARGETS[source_type], [source_id])
    found = next(((l, d) for l, d in current if l.id == link_id), None)
    if found is None:
        raise S.BooksError(404, "That document is not attached here.", "link_not_found")
    try:
        vault.remove_link(context, found[1].id, link_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise S.BooksError(403, "Only the person who added the document, or a workspace owner, can remove it.", "document_not_editable") from None
        raise
    return found[1].id


# -- evidence missing ---------------------------------------------------------------------------------------------------------


def missing(context: TenantContext, start: date, end: date) -> dict[str, Any]:
    """Bills and adjustments dated in the period at or above the threshold with no document attached."""
    with open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None or settings.evidence_threshold is None:
            return {"enabled": False, "threshold": None, "currency": settings.base_currency if settings else None, "count": 0, "items": []}
        bills, _ = store.find("bills", where={"bill_date": Between(start, end)}, limit=MAX_SCAN)
        adjustments, _ = store.find("adjustments", where={"entry_date": Between(start, end), "status": "active"}, limit=MAX_SCAN)
        rates, _ = store.find("rates", order_by="as_of", limit=S.RATE_SCAN)
    converter = R.Converter(rates, settings.base_currency, settings.rate_max_age_days)
    threshold = settings.evidence_threshold
    candidates: list[tuple[str, Any, date, str, Decimal]] = []
    for bill in bills:
        if str(getattr(bill.status, "value", bill.status)) == "void":
            continue
        candidates.append(("bill", bill, bill.bill_date, bill.currency, bill.total))
    for adj in adjustments:
        candidates.append(("adjustment", adj, adj.entry_date, adj.currency, adj.amount))
    due: list[tuple[str, Any, date, str, Decimal, Decimal | None]] = []
    for kind, item, on, currency, amount in candidates:
        base = converter.convert(abs(amount), currency, on)
        if base is None or base >= threshold:
            due.append((kind, item, on, currency, amount, base))
    have = {**{f"bill:{k}": v for k, v in attached(context, "bill", [d[1].id for d in due if d[0] == "bill"]).items()},
            **{f"adjustment:{k}": v for k, v in attached(context, "adjustment", [d[1].id for d in due if d[0] == "adjustment"]).items()}}
    items = []
    for kind, item, on, currency, amount, base in due:
        if have.get(f"{kind}:{item.id}"):
            continue
        items.append({"type": kind, "id": item.id, "date": on.isoformat(), "currency": currency, "amount": str(amount),
                      "base_amount": str(base) if base is not None else None, "not_converted": base is None,
                      "reference": getattr(item, "reference", "") if kind == "bill" else item.kind.value})
    items.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
    return {"enabled": True, "threshold": str(threshold), "currency": settings.base_currency, "count": len(items), "items": items[:200]}


def flags(context: TenantContext, source_type: str, records: list[Any]) -> dict[str, bool]:
    """For a page of bills or adjustments: which ones need evidence (at or above the threshold, nothing attached)."""
    if source_type not in ("bill", "adjustment") or not records:
        return {}
    with open_books_store(context) as store:
        settings = store.get_settings()
        if settings is None or settings.evidence_threshold is None:
            return {}
        rates, _ = store.find("rates", order_by="as_of", limit=S.RATE_SCAN)
    converter = R.Converter(rates, settings.base_currency, settings.rate_max_age_days)
    have = attached(context, source_type, [r.id for r in records])
    out = {}
    for record in records:
        if source_type == "bill":
            if str(getattr(record.status, "value", record.status)) == "void":
                continue
            on, currency, amount = record.bill_date, record.currency, record.total
        else:
            if record.status != "active":
                continue
            on, currency, amount = record.entry_date, record.currency, record.amount
        base = converter.convert(abs(amount), currency, on)
        out[record.id] = (base is None or base >= settings.evidence_threshold) and not have.get(record.id)
    return out


# -- the journal drill-down and the evidence pack -------------------------------------------------------------------------------


def _bill_of_payment(context: TenantContext, payment_ids: list[str]) -> dict[str, str]:
    if not payment_ids:
        return {}
    with open_books_store(context) as store:
        rows, _ = store.find("bill_payments", where={"id": set(payment_ids)}, limit=len(payment_ids) + 1)
    return {row.id: row.bill_id for row in rows}


def entry_targets(context: TenantContext, entries: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Each journal entry's evidence target: its bill (also for a bill payment), invoice, adjustment or itself (manual)."""
    payments = _bill_of_payment(context, [e["source_id"] for e in entries if e["source_kind"] == "bill_payment" and e.get("source_id")])
    out: dict[str, tuple[str, str]] = {}
    for entry in entries:
        kind, sid = entry["source_kind"], entry.get("source_id")
        if kind == "manual":
            out[entry["id"]] = ("journal", entry["id"])
        elif kind in ("bill", "invoice", "adjustment") and sid:
            out[entry["id"]] = (kind, sid)
        elif kind == "bill_payment" and sid and sid in payments:
            out[entry["id"]] = ("bill", payments[sid])
    return out


def with_evidence(context: TenantContext, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The journal page's entries with their attached documents (``evidence``) and where they come from (``evidence_source``)."""
    targets = entry_targets(context, entries)
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for source_type in TARGETS:
        ids = [sid for (kind, sid) in targets.values() if kind == source_type]
        for sid, docs in attached(context, source_type, ids).items():
            found[(source_type, sid)] = docs
    out = []
    for entry in entries:
        target = targets.get(entry["id"])
        out.append({**entry, "evidence_source": {"type": target[0], "id": target[1]} if target else None,
                    "evidence": found.get(target, []) if target else []})
    return out


def _source_reference(entry: Any, allocations: dict[str, Any]) -> str:
    if entry.source_kind == "allocation" and entry.source_id in allocations:
        row = allocations[entry.source_id]
        # Only what the business already holds about the row: never the member's account, card, merchant text or messages.
        return f"#{row.source_ref} {row.category}"
    return ""


def pack_rows(context: TenantContext, start: date, end: date) -> tuple[list[list[Any]], list[tuple[str, str, str]], int]:
    """The CSV index rows, the documents to put in the zip (document id, entry id, title) and how many entries."""
    from . import books_ledger as L

    with open_books_store(context) as store:
        settings = S.settings_or_409(store)
        entries, total = store.find("journal_entries", where={"entry_date": Between(start, end)}, order_by="entry_date", descending=False,
                                    limit=MAX_SCAN)
    allocations = S.allocations_by_id(context) if any(e.source_kind == "allocation" for e in entries) else {}
    views = with_evidence(context, [L.entry_view(e) for e in entries])
    totals = _entry_totals(context, [e.id for e in entries])
    rows: list[list[Any]] = [["entry_id", "entry_date", "event_date", "status", "source_kind", "source_id", "source_reference", "description",
                              "amount", "currency", "evidence_count", "evidence_document_ids", "evidence_names"]]
    docs: list[tuple[str, str, str]] = []
    by_id = {e.id: e for e in entries}
    for view in views:
        entry = by_id[view["id"]]
        evidence = view["evidence"]
        description = entry.description if entry.source_kind != "allocation" else "Paid by a member"
        rows.append([entry.id, entry.entry_date.isoformat(), entry.event_date.isoformat(), entry.status, entry.source_kind, entry.source_id or "",
                     _source_reference(entry, allocations), _clean(description, 200), totals.get(entry.id, ""), settings.base_currency,
                     len(evidence), " ".join(d["document_id"] for d in evidence), " | ".join(_clean(d["title"], 80) for d in evidence)])
        docs.extend((d["document_id"], entry.id, d["title"]) for d in evidence)
    if total > MAX_SCAN:
        rows.append(["truncated", f"{total - MAX_SCAN} more entries not listed: export a shorter period"] + [""] * 11)
    return rows, docs, total


def _entry_totals(context: TenantContext, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    out: dict[str, Decimal] = {}
    with open_books_store(context) as store:
        for start in range(0, len(ids), 500):
            chunk = set(ids[start:start + 500])
            lines, _ = store.find("journal_lines", where={"entry_id": chunk}, limit=len(chunk) * 50 + 1)
            for line in lines:
                out[line.entry_id] = out.get(line.entry_id, Decimal(0)) + line.debit
    return {key: str(value) for key, value in out.items()}


def to_csv(rows: list[list[Any]]) -> str:
    from ..finance.csv_import import safe_cell

    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([safe_cell(str(cell)) if cell is not None else "" for cell in row])
    return out.getvalue()


def pack_zip(context: TenantContext, rows: list[list[Any]], docs: list[tuple[str, str, str]]) -> tuple[bytes, int, int]:
    """The zip: ``index.csv`` plus each attached file the caller may read, once, under ``documents/``. Answers the bytes, the files
    included and the files left out (quarantined, unreadable or past the size limit; the index's ``files.csv`` says which)."""
    from fastapi import HTTPException

    from ..vault import service as vault

    buffer = io.BytesIO()
    included = left_out = 0
    size = 0
    listing: list[list[str]] = [["document_id", "file", "status"]]
    seen: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.csv", to_csv(rows))
        for document_id, _entry, _title in docs:
            if document_id in seen:
                continue
            seen.add(document_id)
            if included >= PACK_MAX_FILES:
                listing.append([document_id, "", "not_included_limit"])
                left_out += 1
                continue
            try:
                record, found, content, _version = vault.read_bytes(context, document_id, "download")
            except HTTPException:
                listing.append([document_id, "", "not_readable"])
                left_out += 1
                continue
            if size + len(content) > PACK_MAX_BYTES:
                listing.append([document_id, "", "not_included_size"])
                left_out += 1
                continue
            name = f"documents/{document_id}-{_SAFE_NAME.sub('_', found.name)[:80] or 'file'}"
            archive.writestr(name, content)
            size += len(content)
            included += 1
            listing.append([document_id, name, "included"])
            del record
        archive.writestr("files.csv", to_csv(listing))
    return buffer.getvalue(), included, left_out


__all__ = ["TARGETS", "attach", "attached", "detach", "entry_targets", "flags", "missing", "pack_rows", "pack_zip", "source", "to_csv", "with_evidence"]
