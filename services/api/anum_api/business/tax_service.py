"""Tax settings, the rate table and tax codes on documents (docs/business-tax.md).

The settings and the rate table live with the business books (``open_books_store``, forced RLS per business). A business
without tax settings works as before: its lines have no tax code and the return lists them as "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..schemas import TenantContext, new_id
from .books_models import TaxRate, TaxSettings
from .books_store import BooksStore, DuplicateError, open_books_store
from .tax_rules import (
    DEFAULT_RATES,
    NO_TAX_CODES,
    Jurisdiction,
    TaxCode,
    TaxRuleError,
    code_allowed,
    rate_on,
    tax_of,
)

RATE_SCAN = 500


@dataclass
class TaxTable:
    settings: TaxSettings
    rows: list[TaxRate] = field(default_factory=list)

    @property
    def jurisdiction(self) -> Jurisdiction:
        return Jurisdiction(self.settings.jurisdiction)

    def rate(self, code: TaxCode | str, on: date) -> Decimal | None:
        return rate_on([(row.code, row.rate_pct, row.effective_from, row.effective_to) for row in self.rows if row.jurisdiction == self.settings.jurisdiction],
                       code, on)


def settings_of(store: BooksStore) -> TaxSettings | None:
    return store.first("tax_settings")


def rates_of(store: BooksStore, jurisdiction: str | None = None) -> list[TaxRate]:
    rows, _ = store.find("tax_rates", where={"jurisdiction": jurisdiction} if jurisdiction else None, order_by="effective_from", descending=False,
                         limit=RATE_SCAN)
    return rows


def table_in(store: BooksStore) -> TaxTable | None:
    settings = settings_of(store)
    return TaxTable(settings, rates_of(store, settings.jurisdiction)) if settings is not None else None


def table_for(context: TenantContext) -> TaxTable | None:
    """The business's settings and rate table, read in one short unit of work (``None`` before an owner saves tax settings)."""
    with open_books_store(context) as store:
        return table_in(store)


def seed_rates(store: BooksStore, context: TenantContext, jurisdiction: Jurisdiction, actor: str, now: datetime) -> int:
    """Add the default rows of a jurisdiction the table does not have yet (idempotent). Returns how many were added."""
    have = {(row.code, row.effective_from) for row in rates_of(store, jurisdiction.value)}
    added = 0
    for item in DEFAULT_RATES:
        if item.jurisdiction != jurisdiction or (item.code.value, item.effective_from) in have:
            continue
        try:
            store.add("tax_rates", TaxRate(id=new_id("btaxr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now,
                                           jurisdiction=jurisdiction.value, code=item.code.value, rate_pct=item.rate_pct,
                                           effective_from=item.effective_from, effective_to=item.effective_to, note=item.note, created_by=actor))
            added += 1
        except DuplicateError:
            continue
    return added


def line_rate(table: TaxTable | None, code: str, on: date) -> Decimal:
    """The rate a sales line with ``code`` takes on ``on``, or a plain refusal."""
    if table is None:
        raise TaxRuleError("Set up tax first (Books, Tax) to use tax codes on invoice lines.", "tax_not_set_up")
    if not code_allowed(code, sales=True):
        raise TaxRuleError("That tax code is for purchases only.", "invalid_tax_code")
    rate = table.rate(code, on)
    if rate is None:
        raise TaxRuleError(f"The rate table has no rate for {code.replace('_', ' ')} on {on.isoformat()}. Add one in Books, Tax.", "rate_missing")
    return rate


def coded_lines(lines: list[Any], table: TaxTable | None, on: date) -> list[Any]:
    """Invoice lines with the rate of their tax code (the code wins over a typed rate); lines without a code are unchanged."""
    out = []
    for line in lines:
        if line.tax_code is None:
            out.append(line)
            continue
        out.append(line.model_copy(update={"tax_rate_pct": line_rate(table, line.tax_code, on)}))
    return out


def bill_tax(table: TaxTable | None, code: str | None, subtotal: Decimal, tax: Decimal, on: date) -> tuple[str | None, Decimal]:
    """(tax code, reverse-charge VAT) of a bill, or a plain refusal. Zero-rated, exempt, out-of-scope and reverse-charge bills
    carry no supplier tax; a reverse-charge bill's VAT is self-assessed at the standard rate of its day."""
    if code is None:
        return None, Decimal(0)
    if table is None:
        raise TaxRuleError("Set up tax first (Books, Tax) to use tax codes on bills.", "tax_not_set_up")
    try:
        value = TaxCode(code)
    except ValueError:
        raise TaxRuleError("Unknown tax code.", "invalid_tax_code") from None
    if value in NO_TAX_CODES and tax != 0:
        raise TaxRuleError("A bill with this tax code carries no tax: set the tax to 0.", "tax_not_allowed")
    if value == TaxCode.REVERSE_CHARGE:
        rate = table.rate(TaxCode.STANDARD, on)
        if rate is None:
            raise TaxRuleError(f"The rate table has no standard rate on {on.isoformat()}. Add one in Books, Tax.", "rate_missing")
        return value.value, tax_of(subtotal, rate)
    return value.value, Decimal(0)


def settings_view(settings: TaxSettings | None) -> dict[str, Any] | None:
    if settings is None:
        return None
    return settings.model_dump(mode="json", exclude={"tenant_id", "workspace_id"})


def rate_view(row: TaxRate) -> dict[str, Any]:
    return {"id": row.id, "jurisdiction": row.jurisdiction, "code": row.code, "rate_pct": format(row.rate_pct.normalize(), "f"),
            "effective_from": row.effective_from.isoformat(), "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "note": row.note}


__all__ = ["TaxTable", "bill_tax", "coded_lines", "line_rate", "rate_view", "rates_of", "seed_rates", "settings_of", "settings_view", "table_for",
           "table_in"]
