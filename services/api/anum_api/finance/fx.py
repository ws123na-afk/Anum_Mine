"""Exchange rates for Finance (docs/finance.md#exchange-rates). Display only: nothing is paid.

ANUM never invents a rate. A rate is either

* published by a real source and fetched by a read-only connector (``connectors.py``):
  the European Central Bank's euro foreign exchange reference rates, stored with the
  date the ECB gives and the source's name and address; or
* typed by the person (source ``manual``), with the date they give.

The UAE dirham is not in the ECB list. It is pegged to the US dollar by the Central Bank
of the UAE at 3.6725 dirhams a dollar, so the connector also stores EUR to AED as the
ECB's EUR to USD rate times that peg, and says so in the rate's source.

Rates are untrusted input (internet content): the ECB file is parsed by a strict pattern
(never an XML parser that could expand entities), every code must be three letters, every
rate a positive number in a sane range, and the date must be recent and not in the future.

Converting is for a combined figure only (safe-to-spend's total in the base currency).
Each conversion shows the rate, its date and its source. A converted figure is rounded
once, half to even, to the target currency's minor unit; the per-currency figures stay
exact and remain the source of truth.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from .models import FxRate
from .money import quantize

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_SOURCE = "European Central Bank euro reference rates"
# The dirham's fixed rate to the US dollar, set by the Central Bank of the UAE.
AED_PER_USD = Decimal("3.6725")
PEG_SOURCE = "ECB EUR/USD x CBUAE peg 3.6725 AED per USD"
RATE_PLACES = Decimal("0.0000000001")  # numeric(24, 10)
MIN_RATE = Decimal("0.000001")
MAX_RATE = Decimal("10000000")
MAX_FEED_BYTES = 64 * 1024
MAX_FEED_AGE_DAYS = 10

_ECB_DAY = re.compile(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]\s*>")
_ECB_RATE = re.compile(r"<Cube\s+currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9]{1,9}(?:\.[0-9]{1,10})?)['\"]\s*/>")
CODE = re.compile(r"^[A-Z]{3}$")


class FxError(ValueError):
    """A rate or a feed ANUM refuses (message is safe to show)."""


@dataclass(frozen=True)
class PublishedRates:
    as_of: date
    base: str
    rates: dict[str, Decimal]


def check_rate(value: object) -> Decimal:
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise FxError("A rate is a number, for example 3.6725.") from None
    if not rate.is_finite() or rate < MIN_RATE or rate > MAX_RATE:
        raise FxError("A rate must be above zero and below 10,000,000.")
    return rate.quantize(RATE_PLACES)


def parse_ecb(text: str, today: date) -> PublishedRates:
    """The ECB daily file as rates from EUR. Strict: anything unexpected is refused."""
    if len(text.encode("utf-8")) > MAX_FEED_BYTES:
        raise FxError("The ECB answer is larger than a rates file can be.")
    days = _ECB_DAY.findall(text)
    if len(days) != 1:
        raise FxError("The ECB answer has no single rates date.")
    try:
        as_of = date.fromisoformat(days[0])
    except ValueError:
        raise FxError("The ECB rates date is not a date.") from None
    if as_of > today + timedelta(days=1):
        raise FxError("The ECB rates date is in the future.")
    if as_of < today - timedelta(days=MAX_FEED_AGE_DAYS):
        raise FxError(f"The ECB rates are older than {MAX_FEED_AGE_DAYS} days.")
    rates: dict[str, Decimal] = {}
    for code, value in _ECB_RATE.findall(text):
        if code in rates or code == "EUR":
            raise FxError("The ECB answer lists a currency twice.")
        rates[code] = check_rate(value)
    if "USD" not in rates or len(rates) < 5 or len(rates) > 80:
        raise FxError("The ECB answer does not look like the reference rates.")
    return PublishedRates(as_of=as_of, base="EUR", rates=rates)


def with_aed_peg(published: PublishedRates) -> dict[str, tuple[Decimal, str]]:
    """Each quote with its source; AED derived from EUR/USD and the CBUAE peg."""
    found = {code: (rate, ECB_SOURCE) for code, rate in published.rates.items()}
    if "AED" not in found and "USD" in published.rates:
        found["AED"] = ((published.rates["USD"] * AED_PER_USD).quantize(RATE_PLACES), PEG_SOURCE)
    return found


# -- conversion ----------------------------------------------------------------------------------


class Conversion(BaseModel):
    """How one currency's figure became the base currency: rate, date and source, shown."""

    from_currency: str
    to_currency: str
    amount: Decimal
    rate: Decimal  # 1 from_currency = rate to_currency
    converted: Decimal
    as_of: date
    source: str
    via: str | None = None  # the shared base of a cross rate, e.g. "EUR"


@dataclass(frozen=True)
class _Path:
    rate: Decimal
    as_of: date
    source: str
    via: str | None


def _latest(rates: Iterable[FxRate], oldest: date) -> dict[tuple[str, str], FxRate]:
    best: dict[tuple[str, str], FxRate] = {}
    for item in rates:
        if item.as_of < oldest:
            continue
        key = (item.base, item.quote)
        current = best.get(key)
        # Newest date wins; on the same date a published source wins over a typed one.
        if current is None or (item.as_of, item.source != "manual") > (current.as_of, current.source != "manual"):
            best[key] = item
    return best


def find_rate(rates: Iterable[FxRate], source: str, target: str, today: date, max_age_days: int) -> _Path | None:
    """1 ``source`` in ``target``: direct, inverse, or a cross through a shared base."""
    if source == target:
        return _Path(Decimal(1), today, "same currency", None)
    table = _latest(rates, today - timedelta(days=max_age_days))
    direct = table.get((source, target))
    if direct is not None:
        return _Path(direct.rate, direct.as_of, direct.source, None)
    inverse = table.get((target, source))
    if inverse is not None and inverse.rate > 0:
        return _Path((Decimal(1) / inverse.rate).quantize(RATE_PLACES), inverse.as_of, inverse.source, None)
    for (base, quote), to_target in sorted(table.items()):
        if quote != target:
            continue
        to_source = table.get((base, source))
        if to_source is not None and to_source.rate > 0:
            rate = (to_target.rate / to_source.rate).quantize(RATE_PLACES)
            sources = to_source.source if to_source.source == to_target.source else f"{to_source.source}; {to_target.source}"
            return _Path(rate, min(to_source.as_of, to_target.as_of), sources, base)
    return None


def convert(amount: Decimal, source: str, target: str, rates: Iterable[FxRate], today: date, max_age_days: int) -> Conversion | None:
    path = find_rate(list(rates), source, target, today, max_age_days)
    if path is None:
        return None
    return Conversion(
        from_currency=source, to_currency=target, amount=amount, rate=path.rate,
        converted=quantize(amount * path.rate, target), as_of=path.as_of, source=path.source, via=path.via,
    )


def is_code(value: str) -> bool:
    return bool(CODE.match(value or ""))
