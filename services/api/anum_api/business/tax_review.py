"""VAT return warnings in the Accountant review (docs/business-tax.md#warnings-in-the-accountant-review).

The tax module's producer for the business's review list (``books_review.register_producer``, kind ``vat``). It only
reads, in short units of work, and only for a business marked VAT registered. Each item says what to look at before the
return is filed on the authority's portal; ANUM never files a return and never says a return or an invoice complies
with ZATCA or FTA rules.

* ``return_due_soon`` / ``return_overdue``: the last ended filing period is not closed in the books and its deadline
  (``tax_rules.return_deadline``: the owner's own due day first, else the built-in one: KSA and Bahrain the end of the
  next month, the UAE 28 days, Oman 30 days) is within 14 days or has passed. Closing the period in Books, Close (done
  once the return is filed) clears it.
* ``filing_deadline_missing``: neither the owner's due day nor a built-in deadline exists (another country), so ANUM
  asks for them in Tax settings instead of guessing. ``no_vat_country``: Kuwait or Qatar, which have no VAT, and no
  due day of the owner's own: ANUM says so and asks for the period and due day only if a return applies.
* ``missing_tax_number``: bills dated in the last ended or the current period that carry VAT while their vendor has no
  tax number saved (input VAT is normally claimed only on a tax invoice that shows the supplier's number).
* ``missing_tax_code``: invoices, credit notes or bills of those periods with no tax code (the rate cannot be read):
  the return counts them as standard rated when they carry VAT, or leaves them out.
* ``vat_unreconciled``: the period's movements on the VAT accounts (1200 and 2100) do not tie to the return's VAT.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from . import books_ledger as L
from . import books_review as BR
from . import tax_return as TR
from .books_store import MAX_SCAN, Between, open_books_store
from .tax_rules import deadline_source, filing_country, return_deadline
from .tax_service import settings_of

logger = logging.getLogger("anum_api.business.tax_review")

DUE_SOON_DAYS = 14
NOTE = "ANUM prepares a working draft only: it never files a return and does not check compliance with ZATCA or FTA rules."


def _item(code: str, detected: str, start: date, end: date, *, on: date | None = None, **params: str) -> BR.ReviewItem:
    return BR.ReviewItem(
        key=f"vat:{code}:{start.isoformat()}:{end.isoformat()}:{params.get('count', '')}"[:200], producer="tax", kind="vat",
        detected=f"{detected} {NOTE}", params={"code": code, "from": start.isoformat(), "to": end.isoformat(), **params},
        severity="warning", occurred_on=on or end, section="tax",
    )


def periods(ctx: BR.ReviewContext, settings: Any) -> list[tuple[date, date]]:
    """The last ended filing period and the current one, oldest first."""
    from .api_tax import _periods

    rows = _periods(settings, ctx.today)
    current = (date.fromisoformat(rows[0]["from"]), date.fromisoformat(rows[0]["to"]))
    previous = (date.fromisoformat(rows[1]["from"]), date.fromisoformat(rows[1]["to"]))
    return [previous, current]


def produce(ctx: BR.ReviewContext) -> list[BR.ReviewItem]:
    with open_books_store(ctx.context) as store:
        settings = settings_of(store)
        if settings is None or not settings.registered:
            return []
        locked = L.locked_through(store)
    (prev_start, prev_end), (cur_start, cur_end) = periods(ctx, settings)
    out: list[BR.ReviewItem] = []

    due = return_deadline(settings.jurisdiction, prev_end, country=settings.country, due_days=settings.filing_due_days)
    filed = locked is not None and locked >= prev_end
    source = deadline_source(settings.jurisdiction, settings.country, settings.filing_due_days)
    if source == "none":
        out.append(_item("filing_deadline_missing", "Set your VAT filing period and due day in Tax settings: ANUM knows no VAT return deadline "
                         "for this country and does not guess one.", prev_start, prev_end, on=ctx.today))
    elif source == "no_vat":
        place = filing_country(settings.jurisdiction, settings.country) or ""
        out.append(_item("no_vat_country", f"{place} has no VAT, so ANUM knows no VAT return deadline. If a VAT return applies to you, set "
                         "your VAT filing period and due day in Tax settings.", prev_start, prev_end, on=ctx.today, country=place))
    if due is not None and not filed:
        days = (due - ctx.today).days
        if days < 0:
            out.append(_item("return_overdue", f"The VAT return for {prev_start.isoformat()} to {prev_end.isoformat()} was usually due on "
                             f"{due.isoformat()} and the period is not closed in the books.", prev_start, prev_end, on=due,
                             due=due.isoformat(), days=str(-days)))
        elif days <= DUE_SOON_DAYS:
            out.append(_item("return_due_soon", f"The VAT return for {prev_start.isoformat()} to {prev_end.isoformat()} is usually due on "
                             f"{due.isoformat()}.", prev_start, prev_end, on=due, due=due.isoformat(), days=str(days)))

    with open_books_store(ctx.context) as store:
        bills, _ = store.find("bills", where={"bill_date": Between(prev_start, cur_end)}, limit=MAX_SCAN)
        vendor_ids = {b.vendor_id for b in bills}
        vendors = {v.id: v for v in store.find("vendors", where={"id": vendor_ids}, limit=MAX_SCAN)[0]} if vendor_ids else {}
    for start, end in ((prev_start, prev_end), (cur_start, cur_end)):
        unnumbered = [b for b in bills if start <= b.bill_date <= end and str(getattr(b.status, "value", b.status)) not in ("void", "draft")
                      and b.tax > Decimal(0) and not (vendors.get(b.vendor_id) and vendors[b.vendor_id].tax_number.strip())]
        if unnumbered:
            out.append(_item("missing_tax_number", f"{len(unnumbered)} bill(s) carry VAT but their vendor has no tax number saved.", start, end,
                             count=str(len(unnumbered))))
        if end < cur_start or start <= ctx.today:
            try:
                found = TR.compute(ctx.context, start, min(end, max(start, ctx.today)), today=ctx.today)
            except Exception as exc:  # noqa: BLE001 - one period that cannot be read hides only its own warnings
                logger.warning("tax_review_period_failed error_class=%s", type(exc).__name__)
                continue
            uncoded = sum(int(w.get("count", 0)) for w in found["warnings"] if w["code"] in ("inferred", "unknown"))
            if uncoded:
                out.append(_item("missing_tax_code", f"{uncoded} document(s) have no tax code, so their VAT rate cannot be read.", start, end,
                                 count=str(uncoded)))
            if not found["journal"]["ties"]:
                out.append(_item("vat_unreconciled", "The VAT accounts do not tie to the VAT in the return for this period.", start, end,
                                 output=found["journal"]["output_vat_movement"], output_in_return=found["journal"]["output_vat_in_return"],
                                 input=found["journal"]["input_vat_movement"], input_in_return=found["journal"]["input_vat_in_return"]))
    return out


BR.register_producer("tax", produce)

__all__ = ["DUE_SOON_DAYS", "NOTE", "periods", "produce"]
