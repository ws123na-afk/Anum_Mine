"""Exact money for the finance ledger (docs/finance.md#money-rules).

* Amounts are ``Decimal``, never ``float``. PostgreSQL stores them as ``numeric(20, 4)``
  next to a three-letter ISO 4217 ``currency``.
* **The one rounding rule:** an amount is rounded once, when it enters ANUM (a CSV cell,
  a form field), to its currency's minor unit (2 decimals for AED, 3 for BHD, JOD, KWD,
  OMR and TND, 0 for JPY and KRW) with banker's rounding (``ROUND_HALF_EVEN``). Sums of
  rounded amounts are exact, so they are never rounded again.
* Different currencies are never added together: every total is a ``{currency: amount}``
  map. There is no FX conversion in Stage 2 (docs/finance.md#not-built-yet).
* On the wire an amount is a decimal string ("1234.50"), so no client turns it into a
  binary float on the way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

ROUNDING = ROUND_HALF_EVEN
MAX_ABS = Decimal(1000000000000)  # 10^12: far below numeric(20, 4), far above any statement
THREE_DECIMALS = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})
ZERO_DECIMALS = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF"})
CURRENCY = re.compile(r"^[A-Z]{3}$")


class MoneyError(ValueError):
    """An amount or currency ANUM refuses (unreadable, too large, unknown code)."""


def normalize_currency(value: str) -> str:
    code = (value or "").strip().upper()
    if not CURRENCY.match(code):
        raise MoneyError("A currency is a three-letter ISO 4217 code, such as AED.")
    return code


def minor_digits(currency: str) -> int:
    if currency in THREE_DECIMALS:
        return 3
    if currency in ZERO_DECIMALS:
        return 0
    return 2


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round ``amount`` to ``currency``'s minor unit, half to even (the one rule)."""
    if not amount.is_finite():
        raise MoneyError("The amount is not a number.")
    if abs(amount) >= MAX_ABS:
        raise MoneyError("The amount is too large.")
    return amount.quantize(Decimal(1).scaleb(-minor_digits(currency)), rounding=ROUNDING)


def to_money(value: object, currency: str) -> Decimal:
    """A form or API value (str, int or Decimal; never float) as a rounded amount."""
    if isinstance(value, float):
        # A float has already lost precision; the API accepts amounts as strings.
        value = repr(value)
    if isinstance(value, bool) or value is None:
        raise MoneyError("The amount is missing.")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise MoneyError("The amount is not a number.") from exc
    return quantize(amount, currency)


def fmt(amount: Decimal, currency: str) -> str:
    """The amount as its currency shows it, e.g. ``1234.50``."""
    return str(quantize(amount, currency))


def totals(rows: Iterable[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Per-currency sums: never one number across currencies."""
    result: dict[str, Decimal] = {}
    for currency, amount in rows:
        result[currency] = result.get(currency, Decimal(0)) + amount
    return dict(sorted(result.items()))


def describe(amounts: dict[str, Decimal]) -> str:
    """``"AED 1,234.50 and USD 20.00"``: one figure per currency, never a converted total."""
    if not amounts:
        return "nothing"
    parts = [f"{currency} {quantize(value, currency):,}" for currency, value in amounts.items()]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
