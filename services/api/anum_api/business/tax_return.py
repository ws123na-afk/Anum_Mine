"""The VAT return of one business for a period, read from the journal (docs/business-tax.md#the-vat-return).

Every figure comes from posted journal lines dated in the period (``entry_date``), in the business's base currency:
the revenue and cost lines of invoices, credit notes and bills give the net amounts, their VAT lines the tax, each by
the line's tax code. Nothing is typed here and nothing is filed: ANUM prepares a working draft for the owner and their
accountant, who file on the authority's portal (ZATCA in KSA, EmaraTax in the UAE).

* A closed period never changes (its journal is frozen). An event of an earlier period posted late, or a correction of
  one (a reversal), lands in the adjustment column of the period it is posted in, never back in a closed one.
* A document without a tax code (written before tax codes existed) is shown, never hidden: with VAT on it, it counts as
  standard rated and is flagged "inferred"; without VAT it is "unknown" and stays out of the boxes, listed.
* Warnings say what the figures cannot include (changes not posted yet, documents without an exchange rate, draft bills,
  costs members paid with no VAT split, manual entries to the VAT accounts) instead of a silent total.

Box layouts (``SA_BOXES``, ``AE_BOXES``) follow the sources listed in docs/business-tax.md#sources; rows ANUM cannot fill
(KSA row 2, the UAE split by emirate, tourist refunds) are shown as zero with a note to complete them on the portal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from ..schemas import TenantContext
from . import books_ledger as L
from .books_models import AccountType, BillStatus
from .books_store import MAX_SCAN, Between, open_books_store
from .tax_rules import Jurisdiction
from .tax_service import table_in

ZERO = Decimal(0)
DOCUMENT_KINDS = frozenset({"invoice", "credit_note", "bill"})
VAT_SUBTYPES = frozenset({"tax_payable", "tax_receivable"})

# (box id, section, label) per jurisdiction. ``bucket`` names map tax codes to boxes below.
SA_BOXES: tuple[tuple[str, str, str], ...] = (
    ("1", "sales", "Standard rated sales"),
    ("2", "sales", "Private healthcare, private education or first house sales to citizens"),
    ("3", "sales", "Zero rated domestic sales"),
    ("4", "sales", "Exports"),
    ("5", "sales", "Exempt sales"),
    ("6", "sales_total", "Total sales"),
    ("7", "purchases", "Standard rated domestic purchases"),
    ("8", "purchases", "Imports subject to VAT paid at customs"),
    ("9", "purchases", "Imports subject to VAT accounted for through the reverse charge mechanism"),
    ("10", "purchases", "Zero rated purchases"),
    ("11", "purchases", "Exempt purchases"),
    ("12", "purchases_total", "Total purchases"),
    ("13", "net", "Total VAT due for the current period"),
    ("14", "net", "Corrections from previous period"),
    ("15", "net", "VAT credit carried forward from previous period"),
    ("16", "net", "Net VAT due (or claimed)"),
)
SA_SALES = {"standard": "1", "zero_rated": "3", "export": "4", "exempt": "5"}
SA_PURCHASES = {"standard": "7", "import_customs": "8", "reverse_charge": "9", "zero_rated": "10", "export": "10", "exempt": "11"}

AE_BOXES: tuple[tuple[str, str, str], ...] = (
    ("1", "sales", "Standard rated supplies (all emirates together)"),
    ("2", "sales", "Tax refunds provided to tourists"),
    ("3", "sales", "Supplies subject to the reverse charge"),
    ("4", "sales", "Zero rated supplies"),
    ("5", "sales", "Exempt supplies"),
    ("6", "sales", "Goods imported into the UAE"),
    ("7", "sales", "Adjustments to goods imported into the UAE"),
    ("8", "sales_total", "Totals (supplies and imports)"),
    ("9", "purchases", "Standard rated expenses"),
    ("10", "purchases", "Supplies subject to the reverse charge"),
    ("11", "purchases_total", "Totals (expenses)"),
    ("12", "net", "Total value of due tax for the period"),
    ("13", "net", "Total value of recoverable tax for the period"),
    ("14", "net", "Payable tax for the period"),
)
AE_SALES = {"standard": "1", "zero_rated": "4", "export": "4", "exempt": "5"}
AE_PURCHASES = {"standard": "9", "import_customs": "9", "reverse_charge": "10"}

OTHER_BOXES: tuple[tuple[str, str, str], ...] = (
    ("S-standard", "sales", "Standard rated sales"), ("S-zero", "sales", "Zero rated sales and exports"), ("S-exempt", "sales", "Exempt sales"),
    ("S-total", "sales_total", "Total sales"),
    ("P-standard", "purchases", "Standard rated purchases (including imports)"), ("P-reverse", "purchases", "Reverse charge purchases"),
    ("P-zero", "purchases", "Zero rated and exempt purchases"), ("P-total", "purchases_total", "Total purchases"),
    ("N-net", "net", "Net VAT due (or claimed)"),
)
OTHER_SALES = {"standard": "S-standard", "zero_rated": "S-zero", "export": "S-zero", "exempt": "S-exempt"}
OTHER_PURCHASES = {"standard": "P-standard", "import_customs": "P-standard", "reverse_charge": "P-reverse", "zero_rated": "P-zero", "export": "P-zero",
                   "exempt": "P-zero"}

# Rows that are not a box: shown below the return.
INFO_ROWS = {"out_of_scope_sales": "Out of scope sales (not in the return)", "out_of_scope_purchases": "Out of scope purchases (not in the return)",
             "unknown_sales": "Sales without a tax code and without VAT (not in the return)",
             "unknown_purchases": "Purchases without a tax code and without VAT (not in the return)",
             "zero_exempt_purchases": "Zero rated and exempt purchases (not reported on this return)"}

NOTES = {
    "SA": ["Row 2 (sales to citizens of private healthcare, private education or a first house) is not tracked by ANUM: complete it on the ZATCA portal.",
           "Rows 14 (corrections from a previous period) and 15 (credit carried forward) are entered on the ZATCA portal; late postings show in the adjustment column.",
           "Row 9 VAT is both output and input tax: it is added to the tax due and recovered, as the return form does."],
    "AE": ["Box 1 is shown for all emirates together: split it by emirate on EmaraTax.",
           "Boxes 2 (tourist refunds) and 7 (import adjustments) are not tracked by ANUM. The FTA pre-fills box 6 from customs data: check it.",
           "Reverse charge VAT is in box 3 (due) and box 10 (recoverable)."],
    "OTHER": ["No statutory layout for this jurisdiction: a summary by tax code."],
}


@dataclass
class Box:
    box: str
    section: str
    label: str
    amount: Decimal = ZERO
    vat: Decimal = ZERO
    adjustment_amount: Decimal = ZERO
    adjustment_vat: Decimal = ZERO
    documents: int = 0
    note: str = ""


@dataclass
class Document:
    entry_id: str
    source_kind: str
    source_id: str | None
    description: str
    entry_date: date
    event_date: date
    box: str
    code: str
    inferred: bool
    adjustment: bool
    net: Decimal
    vat: Decimal


@dataclass
class _Parts:
    net: dict[str | None, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    vat: dict[str | None, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    self_assessed: dict[str | None, Decimal] = field(default_factory=lambda: defaultdict(Decimal))


def layout(jurisdiction: str) -> tuple[tuple[tuple[str, str, str], ...], dict[str, str], dict[str, str]]:
    if jurisdiction == Jurisdiction.SA:
        return SA_BOXES, SA_SALES, SA_PURCHASES
    if jurisdiction == Jurisdiction.AE:
        return AE_BOXES, AE_SALES, AE_PURCHASES
    return OTHER_BOXES, OTHER_SALES, OTHER_PURCHASES


def _bucket(code: str, sales: bool, mapping: dict[str, str], jurisdiction: str) -> str:
    if code in mapping:
        return mapping[code]
    if code == "out_of_scope":
        return "out_of_scope_sales" if sales else "out_of_scope_purchases"
    if code == "unknown":
        return "unknown_sales" if sales else "unknown_purchases"
    return "zero_exempt_purchases"  # an AE purchase that is zero rated or exempt (no VAT201 box)


def classify(entries: dict[str, Any], parts: dict[str, _Parts], start: date, jurisdiction: str) -> list[Document]:
    """One document row per entry and tax code: the box it counts in, net and VAT, inferred when the line had no code."""
    _, sales_map, purchase_map = layout(jurisdiction)
    out: list[Document] = []
    for entry_id, found in parts.items():
        entry = entries.get(entry_id)
        if entry is None:
            continue
        sales = entry.source_kind in ("invoice", "credit_note")
        adjustment = entry.event_date < start
        codes = set(found.net) | set(found.vat) | set(found.self_assessed)
        for code in sorted(codes, key=lambda item: item or ""):
            net, vat = found.net.get(code, ZERO), found.vat.get(code, ZERO)
            if net == 0 and vat == 0:
                continue
            inferred = False
            name = code
            if code is None:
                # Written before tax codes: with VAT on it, it is a standard rated supply (the only rate above zero); else unknown.
                inferred = vat != 0
                name = "standard" if inferred else "unknown"
            out.append(Document(entry_id, entry.source_kind, entry.source_id, entry.description, entry.entry_date, entry.event_date,
                                _bucket(name, sales, sales_map if sales else purchase_map, jurisdiction), name, inferred, adjustment, net, vat))
    out.sort(key=lambda row: (row.entry_date, row.description, row.entry_id, row.code))
    return out


def boxes_from(jurisdiction: str, documents: list[Document], self_assessed: Decimal, self_assessed_adj: Decimal, base: str) -> list[Box]:
    template, _, _ = layout(jurisdiction)
    boxes = {box: Box(box, section, label) for box, section, label in template}
    for row in documents:
        target = boxes.get(row.box)
        if target is None:
            continue
        if row.adjustment:
            target.adjustment_amount += row.net
            target.adjustment_vat += row.vat
        else:
            target.amount += row.net
            target.vat += row.vat
        target.documents += 1

    def total(into: str, members: list[str]) -> None:
        box = boxes[into]
        for name in members:
            part = boxes[name]
            box.amount += part.amount
            box.vat += part.vat
            box.adjustment_amount += part.adjustment_amount
            box.adjustment_vat += part.adjustment_vat

    def due(box: Box) -> Decimal:
        return box.vat + box.adjustment_vat

    if jurisdiction == Jurisdiction.SA:
        total("6", ["1", "2", "3", "4", "5"])
        total("12", ["7", "8", "9", "10", "11"])
        # Reverse charge VAT (row 9) is output tax as well as input tax: added to the tax due, recovered in row 12.
        boxes["13"].vat = due(boxes["6"]) + self_assessed + self_assessed_adj - due(boxes["12"])
        boxes["16"].vat = boxes["13"].vat
    elif jurisdiction == Jurisdiction.AE:
        # Box 3: the supplies received under the reverse charge (their value and the VAT due on them).
        boxes["3"].amount, boxes["3"].adjustment_amount = boxes["10"].amount, boxes["10"].adjustment_amount
        boxes["3"].vat, boxes["3"].adjustment_vat = self_assessed, self_assessed_adj
        boxes["3"].documents = boxes["10"].documents
        total("8", ["1", "2", "3", "4", "5", "6", "7"])
        total("11", ["9", "10"])
        boxes["12"].vat = due(boxes["8"])
        boxes["13"].vat = due(boxes["11"])
        boxes["14"].vat = boxes["12"].vat - boxes["13"].vat
    else:
        total("S-total", ["S-standard", "S-zero", "S-exempt"])
        total("P-total", ["P-standard", "P-reverse", "P-zero"])
        boxes["N-net"].vat = due(boxes["S-total"]) + self_assessed + self_assessed_adj - due(boxes["P-total"])
    for box in boxes.values():
        for name in ("amount", "vat", "adjustment_amount", "adjustment_vat"):
            setattr(box, name, quantize(getattr(box, name), base))
    return list(boxes.values())


def money(value: Decimal, base: str) -> str:
    return str(quantize(value, base))


def compute(context: TenantContext, start: date, end: date, *, today: date) -> dict[str, Any]:
    """The return for ``start`` to ``end`` with every box, its documents, the warnings and the journal tie-out."""
    pending = L.status(context, today)
    with open_books_store(context) as store:
        settings = store.get_settings()
        table = table_in(store)
        chart = {row.id: row for row in L.chart_accounts(store)}
        locked = L.locked_through(store)
        where = {"entry_date": Between(start, end)}
        rows = store.ledger_totals({**where, "source_kind": set(DOCUMENT_KINDS)}, ("entry_id", "account_id", "tax_code"))
        vat_ids = {row.id for row in chart.values() if row.subtype in VAT_SUBTYPES}
        vat_rows = store.ledger_totals({**where, "account_id": vat_ids}, ("entry_id", "source_kind", "account_id")) if vat_ids else []
        member_paid = store.ledger_totals({**where, "source_kind": "allocation"}, ("entry_id",))
        ids = {row["entry_id"] for row in rows} | {row["entry_id"] for row in vat_rows if row["source_kind"] not in DOCUMENT_KINDS}
        limit = MAX_SCAN * 4
        entries_list, entries_total = store.find("journal_entries", where={"id": ids}, limit=limit) if ids else ([], 0)
        issues, _ = store.find("posting_issues", where={"event_date": Between(start, end)}, limit=200)
        _, draft_count = store.find("bills", where={"status": BillStatus.DRAFT, "bill_date": Between(start, end)}, limit=1)
    base = settings.base_currency if settings else "SAR"
    jurisdiction = table.settings.jurisdiction if table else Jurisdiction.OTHER.value
    entries = {row.id: row for row in entries_list}
    parts: dict[str, _Parts] = defaultdict(_Parts)
    self_assessed = self_assessed_adj = ZERO
    for row in rows:
        account = chart.get(row["account_id"])
        entry = entries.get(row["entry_id"])
        if account is None or entry is None:
            continue
        movement = row["debit"] - row["credit"]
        code = row["tax_code"]
        sales = entry.source_kind in ("invoice", "credit_note")
        found = parts[row["entry_id"]]
        if account.subtype == "tax_payable":
            if sales:
                found.vat[code] += -movement
            else:  # a reverse-charge bill: VAT the business owes on its own purchase
                found.self_assessed[code] += -movement
                if entry.event_date < start:
                    self_assessed_adj += -movement
                else:
                    self_assessed += -movement
        elif account.subtype == "tax_receivable":
            if not sales:
                found.vat[code] += movement
        elif account.type == AccountType.REVENUE and sales:
            found.net[code] += -movement
        elif not sales and (account.type == AccountType.EXPENSE or account.subtype == "fixed_asset"):
            found.net[code] += movement
    documents = classify(entries, parts, start, jurisdiction)
    boxes = boxes_from(jurisdiction, documents, self_assessed, self_assessed_adj, base)
    info: dict[str, dict[str, Any]] = {}
    for row in documents:
        if row.box in INFO_ROWS:
            item = info.setdefault(row.box, {"key": row.box, "label": INFO_ROWS[row.box], "amount": ZERO, "vat": ZERO, "documents": 0})
            item["amount"] += row.net
            item["vat"] += row.vat
            item["documents"] += 1
    # Manual or adjustment entries on the VAT accounts (a VAT payment to the authority, a correction): not in the return, shown.
    manual = [row for row in vat_rows if row["source_kind"] not in DOCUMENT_KINDS]
    manual_entries = sorted({row["entry_id"] for row in manual})
    payable_moves = sum((row["credit"] - row["debit"] for row in vat_rows if chart[row["account_id"]].subtype == "tax_payable"), ZERO)
    receivable_moves = sum((row["debit"] - row["credit"] for row in vat_rows if chart[row["account_id"]].subtype == "tax_receivable"), ZERO)
    output_in_return = sum((row.vat for row in documents if row.source_kind != "bill"), ZERO) + self_assessed + self_assessed_adj
    input_in_return = sum((row.vat for row in documents if row.source_kind == "bill"), ZERO)
    manual_payable = sum((row["credit"] - row["debit"] for row in manual if chart[row["account_id"]].subtype == "tax_payable"), ZERO)
    manual_receivable = sum((row["debit"] - row["credit"] for row in manual if chart[row["account_id"]].subtype == "tax_receivable"), ZERO)
    inferred = sum(1 for row in documents if row.inferred)
    unknown = sum(1 for row in documents if row.code == "unknown")
    warnings: list[dict[str, Any]] = []

    def warn(code: str, text: str, **extra: Any) -> None:
        warnings.append({"code": code, "text": text, **extra})

    if table is None:
        warn("not_set_up", "Tax is not set up for this business: the return uses a summary layout and every document reads as uncoded.")
    elif not table.settings.registered:
        warn("not_registered", "The business is not marked as VAT registered. A business that is not registered files no return.")
    if not pending["complete"]:
        warn("unposted", "Some changes in the books are not posted to the journal yet. Post now, then read the return again.",
             to_post=pending["to_post"], to_reverse=pending["to_reverse"], issues=pending["issues"])
    if issues:
        warn("missing_rate", f"{len(issues)} document(s) dated in this period have no exchange rate and are not in the journal or the return.",
             count=len(issues), currencies=sorted({row.currency for row in issues if row.currency}))
    if draft_count:
        warn("draft_bills", f"{draft_count} draft bill(s) dated in this period are not counted until approved.", count=draft_count)
    if inferred:
        warn("inferred", f"{inferred} document(s) have no tax code but carry VAT: counted as standard rated. Set their tax code to confirm.", count=inferred)
    if unknown:
        warn("unknown", f"{unknown} document(s) have no tax code and no VAT: left out of the boxes. Set their tax code.", count=unknown)
    if member_paid:
        warn("member_paid", f"{len(member_paid)} cost(s) paid by members carry no VAT split: their input VAT is not claimed. Record a bill with the tax for any you can recover.",
             count=len(member_paid))
    if manual_entries:
        warn("manual_vat", f"{len(manual_entries)} manual or adjustment entries touch the VAT accounts and are not in the return (a VAT payment, a correction).",
             count=len(manual_entries))
    if entries_total > limit or pending.get("truncated"):
        warn("truncated", "There are too many records to read at once: the return is incomplete.")
    expected = {"SA": "SAR", "AE": "AED"}.get(jurisdiction)
    if expected and base != expected:
        warn("base_currency", f"The books are kept in {base}; the return is filed in {expected}. Convert the figures before filing.")
    closed = locked is not None and end <= locked
    if not closed:
        warn("period_open", "This period is not closed: the figures can still change. Close it in Books, Close when the return is filed.")
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "jurisdiction": jurisdiction,
        "base_currency": base,
        "registration_number": table.settings.registration_number if table else "",
        "closed": closed,
        "closed_through": locked.isoformat() if locked else None,
        "complete": pending["complete"] and not issues and not draft_count and unknown == 0 and entries_total <= limit,
        "boxes": [{"box": box.box, "section": box.section, "label": box.label, "amount": money(box.amount, base), "vat": money(box.vat, base),
                   "adjustment_amount": money(box.adjustment_amount, base), "adjustment_vat": money(box.adjustment_vat, base), "documents": box.documents}
                  for box in boxes],
        "info": [{**item, "amount": money(item["amount"], base), "vat": money(item["vat"], base)} for item in info.values()],
        "notes": NOTES.get(jurisdiction, NOTES["OTHER"]),
        "warnings": warnings,
        "journal": {
            "output_vat_movement": money(payable_moves, base), "input_vat_movement": money(receivable_moves, base),
            "output_vat_in_return": money(output_in_return, base), "input_vat_in_return": money(input_in_return, base),
            "manual_output": money(manual_payable, base), "manual_input": money(manual_receivable, base),
            "ties": quantize(payable_moves - output_in_return - manual_payable, base) == 0 and quantize(receivable_moves - input_in_return - manual_receivable, base) == 0,
        },
        "_documents": documents,
    }


def document_view(row: Document, base: str) -> dict[str, Any]:
    return {"entry_id": row.entry_id, "source_kind": row.source_kind, "source_id": row.source_id, "description": row.description,
            "entry_date": row.entry_date.isoformat(), "event_date": row.event_date.isoformat(), "box": row.box, "tax_code": row.code,
            "inferred": row.inferred, "adjustment": row.adjustment, "net": money(row.net, base), "vat": money(row.vat, base)}


__all__ = ["AE_BOXES", "INFO_ROWS", "NOTES", "SA_BOXES", "Box", "Document", "boxes_from", "classify", "compute", "document_view", "layout"]
