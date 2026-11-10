"""Tax rules of a business: jurisdictions, tax codes, the default rate table and registration numbers (docs/business-tax.md).

Pure and in ``Decimal``: nothing here reads a store. The rates a business uses are rows of its own rate table
(``business_tax_rates``, seeded from :data:`DEFAULT_RATES` when an owner first saves the tax settings and editable
after that), never constants in a report. ANUM never claims a return or an invoice is compliant: the figures are a
working draft for the owner and their accountant, who file on the authority's own portal.

Tax codes (one per invoice line and per bill):

* ``standard``: the jurisdiction's standard rate (KSA 15 %, UAE 5 % in the default table).
* ``zero_rated``: a taxable supply at 0 % inside the country.
* ``export``: a zero-rated export (KSA return row "exports", UAE zero-rated supplies).
* ``exempt``: an exempt supply (no output tax; input tax on it is not recoverable).
* ``out_of_scope``: outside the VAT system (not a supply for VAT); shown, never in a return box.
* ``reverse_charge``: purchases only. A supply from abroad on which the business accounts for the VAT itself: the
  supplier charges none, the business posts the same amount as output and input tax.
* ``import_customs``: purchases only. VAT paid to customs on imported goods (the bill is the customs declaration).

A line written before tax codes existed has no code: its documents show "unknown" and the return lists them
(``tax_return``), never silently counted as one box or another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class Jurisdiction(StrEnum):
    SA = "SA"
    AE = "AE"
    OTHER = "OTHER"


class TaxCode(StrEnum):
    STANDARD = "standard"
    ZERO_RATED = "zero_rated"
    EXPORT = "export"
    EXEMPT = "exempt"
    OUT_OF_SCOPE = "out_of_scope"
    REVERSE_CHARGE = "reverse_charge"
    IMPORT_CUSTOMS = "import_customs"


class Frequency(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


SALES_CODES = frozenset({TaxCode.STANDARD, TaxCode.ZERO_RATED, TaxCode.EXPORT, TaxCode.EXEMPT, TaxCode.OUT_OF_SCOPE})
PURCHASE_CODES = frozenset(TaxCode)
# Codes that never carry tax on the document itself (the reverse charge is self-assessed, not charged by the supplier).
NO_TAX_CODES = frozenset({TaxCode.ZERO_RATED, TaxCode.EXPORT, TaxCode.EXEMPT, TaxCode.OUT_OF_SCOPE, TaxCode.REVERSE_CHARGE})
# The ZATCA tax category of each sales code (UN/ECE 5305 letters as the ZATCA XML implementation standard uses them).
ZATCA_CATEGORY = {TaxCode.STANDARD: "S", TaxCode.ZERO_RATED: "Z", TaxCode.EXPORT: "Z", TaxCode.EXEMPT: "E", TaxCode.OUT_OF_SCOPE: "O"}


@dataclass(frozen=True)
class DefaultRate:
    jurisdiction: Jurisdiction
    code: TaxCode
    rate_pct: Decimal
    effective_from: date
    effective_to: date | None
    note: str


# The seed of a business's rate table. Rates are data: an owner or accountant can add a row (a new rate from a date) and
# the reports read the table, never these constants.
DEFAULT_RATES: tuple[DefaultRate, ...] = (
    DefaultRate(Jurisdiction.SA, TaxCode.STANDARD, Decimal(5), date(2018, 1, 1), date(2020, 6, 30), "KSA standard rate before 1 July 2020"),
    DefaultRate(Jurisdiction.SA, TaxCode.STANDARD, Decimal(15), date(2020, 7, 1), None, "KSA standard rate from 1 July 2020"),
    DefaultRate(Jurisdiction.AE, TaxCode.STANDARD, Decimal(5), date(2018, 1, 1), None, "UAE standard rate"),
    *(DefaultRate(place, code, Decimal(0), date(2018, 1, 1), None, "")
      for place in (Jurisdiction.SA, Jurisdiction.AE, Jurisdiction.OTHER)
      for code in (TaxCode.ZERO_RATED, TaxCode.EXPORT, TaxCode.EXEMPT, TaxCode.OUT_OF_SCOPE)),
)

SA_VAT = re.compile(r"^3[0-9]{13}3$")
AE_TRN = re.compile(r"^[0-9]{15}$")
OTHER_NUMBER = re.compile(r"^[A-Za-z0-9 ./-]{3,30}$")


class TaxRuleError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def check_registration(jurisdiction: Jurisdiction, value: str) -> str:
    """The registration number, trimmed, or a plain refusal. KSA: 15 digits starting and ending with 3. UAE TRN: 15 digits."""
    text = re.sub(r"\s+", "", value or "")
    if not text:
        return ""
    if jurisdiction == Jurisdiction.SA and not SA_VAT.match(text):
        raise TaxRuleError("A Saudi VAT number is 15 digits that start and end with 3.", "invalid_vat_number")
    if jurisdiction == Jurisdiction.AE and not AE_TRN.match(text):
        raise TaxRuleError("A UAE tax registration number (TRN) is 15 digits.", "invalid_vat_number")
    if jurisdiction == Jurisdiction.OTHER and not OTHER_NUMBER.match(text):
        raise TaxRuleError("Use 3 to 30 letters, digits, spaces, dots, slashes or hyphens.", "invalid_vat_number")
    return text


def rate_on(rows: list[tuple[TaxCode | str, Decimal, date, date | None]], code: TaxCode | str, on: date) -> Decimal | None:
    """The rate of ``code`` in force on ``on`` from (code, rate, from, to) rows; the newest start wins an overlap. None when the
    table has no row for that day: the caller refuses, it never guesses."""
    best: tuple[date, Decimal] | None = None
    wanted = str(code)
    for row_code, rate, start, end in rows:
        if str(row_code) != wanted or start > on or (end is not None and end < on):
            continue
        if best is None or start > best[0]:
            best = (start, rate)
    return best[1] if best else None


def tax_of(net: Decimal, rate_pct: Decimal) -> Decimal:
    return (net * rate_pct / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def split(amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``amount`` shared in proportion to ``weights`` to the cent, the rounding left on the largest share: the parts add up exactly."""
    total = sum(weights, Decimal(0))
    if not weights:
        return []
    if total == 0:
        return [amount] + [Decimal("0.00")] * (len(weights) - 1)
    parts = [(amount * weight / total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for weight in weights]
    gap = amount - sum(parts, Decimal(0))
    if gap:
        largest = max(range(len(weights)), key=lambda index: abs(weights[index]))
        parts[largest] += gap
    return parts


def code_allowed(code: TaxCode | str, *, sales: bool) -> bool:
    try:
        value = TaxCode(str(code))
    except ValueError:
        return False
    return value in (SALES_CODES if sales else PURCHASE_CODES)


@dataclass(frozen=True)
class FilingPreset:
    """The usual VAT return deadline of a country: ``end_of_next_month`` (the last day of the month after the period) or a
    number of days after the period ends. The period itself (monthly or quarterly) stays the business's own setting."""

    country: str
    usual_frequency: str  # what the authority normally assigns, shown as a hint only
    end_of_next_month: bool
    days_after: int | None
    source: str


# Built-in VAT return deadlines (docs/business-tax.md#filing-deadlines). Each is a reminder only: the authority's portal shows
# the binding date, and an owner's own due day (``TaxSettings.filing_due_days``) always wins.
FILING_PRESETS: dict[str, FilingPreset] = {
    # ZATCA: the return and payment are due by the last day of the month after the tax period.
    "SA": FilingPreset("SA", "monthly or quarterly, as ZATCA assigns", True, None,
                       "https://zatca.gov.sa/en/E-Invoicing/Introduction/Guidelines/Documents/E-Invoicing_Detailed__Guideline.pdf"),
    # FTA: 28 days after the end of the tax period.
    "AE": FilingPreset("AE", "quarterly, or monthly when the FTA assigns it", False, 28, "https://tax.gov.ae/-/media/Files/EN/PDF/Guides/VAT-Returns-User-Guide.pdf"),
    # NBR (Bahrain): monthly above BHD 3 million of supplies a year, else quarterly; due by the last day of the month after
    # the VAT period. https://www.nbr.gov.bh/VAT/vat_return_filing/when_to_file (read 10 November 2026).
    "BH": FilingPreset("BH", "monthly above BHD 3 million a year, else quarterly", True, None, "https://www.nbr.gov.bh/VAT/vat_return_filing/when_to_file"),
    # Oman Tax Authority: quarterly tax periods; the return is filed within 30 days after the tax period ends (VAT Law,
    # Royal Decree 121/2020, and the VAT Taxpayer Guide on return filing, read 10 November 2026):
    # https://tms.taxoman.gov.om/portal/documents/20126/1414820/VAT+Taxpayer+Guide+-+VAT+Return+Filing.pdf
    "OM": FilingPreset("OM", "quarterly", False, 30,
                       "https://tms.taxoman.gov.om/portal/documents/20126/1414820/VAT+Taxpayer+Guide+-+VAT+Return+Filing.pdf"),
}
# GCC countries without VAT (checked 10 November 2026): ANUM says so and never invents a deadline for them.
NO_VAT_COUNTRIES: frozenset[str] = frozenset({"KW", "QA"})
MAX_DUE_DAYS = 120


def filing_country(jurisdiction: Jurisdiction | str, country: str | None) -> str | None:
    """The country whose VAT return this is: KSA and the UAE from the jurisdiction, any other from the saved country."""
    if str(jurisdiction) in (Jurisdiction.SA.value, Jurisdiction.AE.value):
        return str(jurisdiction)
    return (country or "").upper() or None


def deadline_source(jurisdiction: Jurisdiction | str, country: str | None = None, due_days: int | None = None) -> str:
    """Where the deadline comes from: ``owner`` (the business's own due day), ``preset``, ``no_vat`` (Kuwait, Qatar) or ``none``."""
    if due_days is not None:
        return "owner"
    place = filing_country(jurisdiction, country)
    if place in FILING_PRESETS:
        return "preset"
    if place in NO_VAT_COUNTRIES:
        return "no_vat"
    return "none"


def _end_of_next_month(period_end: date) -> date:
    following = date(period_end.year + (period_end.month // 12), period_end.month % 12 + 1, 1)
    after = date(following.year + (following.month // 12), following.month % 12 + 1, 1)
    return date.fromordinal(after.toordinal() - 1)


def return_deadline(jurisdiction: Jurisdiction | str, period_end: date, *, country: str | None = None, due_days: int | None = None) -> date | None:
    """The usual last day to file and pay the VAT return of a period ending on ``period_end``. The owner's own due day
    (days after the period ends) always wins; otherwise the country's preset (:data:`FILING_PRESETS`: KSA and Bahrain the
    last day of the next month, the UAE 28 days, Oman 30 days). ``None`` when neither exists: ANUM never guesses. A
    reminder only: the authority's portal shows the binding date, and ANUM never files."""
    if due_days is not None:
        return date.fromordinal(period_end.toordinal() + due_days)
    preset = FILING_PRESETS.get(filing_country(jurisdiction, country) or "")
    if preset is None:
        return None
    if preset.end_of_next_month:
        return _end_of_next_month(period_end)
    return date.fromordinal(period_end.toordinal() + (preset.days_after or 0))


__all__ = [
    "DEFAULT_RATES", "FILING_PRESETS", "MAX_DUE_DAYS", "NO_TAX_CODES", "NO_VAT_COUNTRIES", "FilingPreset", "PURCHASE_CODES", "SALES_CODES", "ZATCA_CATEGORY", "DefaultRate", "Frequency", "Jurisdiction", "TaxCode",
    "TaxRuleError", "check_registration", "code_allowed", "deadline_source", "filing_country", "rate_on", "return_deadline", "split", "tax_of",
]
