"""Money in statements (docs/finance-statements.md#amounts): either decimal mark, thousands separators,
``CR``/``DR`` (and Arabic دائن/مدين) after the number, a minus before or after it, brackets for a negative,
a currency code or sign next to it, Arabic-Indic digits (already folded by ``words.normalize``).
A token is money only with the currency's decimal places, so reference numbers and dates are not read
as amounts.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .. import csv_import

_MARK = r"(?:\s?(?:CR|DR|Cr|Dr|cr|dr|دائن|مدين)(?!\w))?"


def money_pattern(decimals: int) -> re.Pattern[str]:
    places = r"\d{%d}" % (decimals or 2)
    return re.compile(
        r"(?<![\w.,/:])(\(?[-+]?\(?\d{1,3}(?:[,.']\d{3})*[.,]" + places + r"\)?-?|\(?[-+]?\d+[.,]" + places + r"\)?-?)" + _MARK + r"(?![\w.,/])"
    )


def parse(token: str) -> Decimal | None:
    text = token.strip()
    if text.endswith("دائن"):
        text = text[: -len("دائن")].strip() + " CR"
    elif text.endswith("مدين"):
        text = text[: -len("مدين")].strip() + " DR"
    try:
        return csv_import.parse_amount(text)
    except (InvalidOperation, ValueError):
        return None


def explicit_sign(token: str) -> bool:
    text = token.strip()
    return bool(re.search(r"(?i)(cr|dr|دائن|مدين)$", text) or text.startswith(("-", "(", "+")) or text.endswith("-"))


def is_money(text: str, pattern: re.Pattern[str]) -> bool:
    found = pattern.fullmatch(text.strip())
    return bool(found)


__all__ = ["explicit_sign", "is_money", "money_pattern", "parse"]
