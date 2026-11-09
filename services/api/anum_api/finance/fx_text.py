"""Money and foreign exchange details read from free text (docs/finance.md#fx-details-from-the-row-text).

One reader for every place a bank writes money into words: an SMS (``sms_parser``), a CSV statement's
description and a PDF statement row. Pure and deterministic: no clock, no store, no network.

* :func:`amounts` finds every "currency word and number" pair ("USD 20.00", "20.00 USD", "120.00 ر.س").
* :func:`split_converted` is the SMS rule for two amounts: the first is what was spent, a second one in
  another currency is the converted (billed) amount: "USD 20.00 (SAR 75.40)".
* :func:`read_fx` reads a statement row's text for the original amount and currency, the rate the bank
  used ("@ 3.84", "FX rate 3.8400", "سعر الصرف 3.84"), a markup ("markup 2.5%", "هامش 2.5%") and a fee
  ("intl txn fee SAR 1.20", "رسوم 1.20 ر.س"). It never invents a rate: a rate is either printed or worked
  out from two printed amounts (and then marked ``implied``).
* :func:`check` compares the bank-billed amount with original x rate (plus the fee or markup when they are
  printed) and gives the difference. The ledger always keeps the bank-billed amount; the check only tells
  the person whether the figures on the row agree with each other.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .money import MoneyError, minor_digits, quantize

# Currency words to ISO codes. Longer spellings first so "SAR" wins over "SR".
CURRENCY_WORDS: tuple[tuple[str, str], ...] = (
    ("ر.س", "SAR"), ("ريال سعودي", "SAR"), ("ريال", "SAR"), ("رس", "SAR"), ("SAR", "SAR"), ("SR", "SAR"),
    ("د.إ", "AED"), ("درهم", "AED"), ("AED", "AED"), ("DHS", "AED"), ("DH", "AED"),
    ("USD", "USD"), ("US$", "USD"), ("$", "USD"), ("دولار", "USD"),
    ("EUR", "EUR"), ("€", "EUR"), ("يورو", "EUR"), ("GBP", "GBP"), ("£", "GBP"),
    ("KWD", "KWD"), ("د.ك", "KWD"), ("QAR", "QAR"), ("BHD", "BHD"), ("OMR", "OMR"), ("EGP", "EGP"),
    ("INR", "INR"), ("PKR", "PKR"), ("JOD", "JOD"), ("TRY", "TRY"), ("CHF", "CHF"), ("JPY", "JPY"),
)
CURRENCY_ALT = "|".join(re.escape(word) for word, _ in sorted(CURRENCY_WORDS, key=lambda item: -len(item[0])))
CURRENCY_CODE = {word.upper(): code for word, code in CURRENCY_WORDS}
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# A currency word, then the number; or the number, then the currency word. Latin codes stand alone
# ("SAR 9", not the "sar" inside a word).
AMOUNT = re.compile(
    rf"(?:(?<![A-Za-z])(?P<c1>{CURRENCY_ALT})\s?(?P<n1>{NUMBER}))|(?:(?<![\d.,])(?P<n2>{NUMBER})\s?(?P<c2>{CURRENCY_ALT})(?![A-Za-z]))",
    re.IGNORECASE,
)
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_RATE_NUMBER = r"\d{1,6}(?:\.\d{1,10})?"
# "@ 3.84", "@3.8400", "FX rate 3.84", "rate: 3.84", "exchange rate 3.84", "conv rate 3.84", Arabic.
_RATE = re.compile(
    rf"(?:@\s?|(?:(?:fx|exchange|exch|conversion|conv|xchg)\.?\s*rate|\brate)\s*(?::|=|of)?\s*|"
    rf"(?:سعر\s*(?:الصرف|التحويل|صرف|تحويل)|معدل\s*(?:الصرف|التحويل))\s*:?\s*)(?P<rate>{_RATE_NUMBER})(?!\s?%)(?![\d])",
    re.IGNORECASE,
)
# "markup 2.5%", "mark-up 2.50 %", "FX margin 2.5%", "هامش 2.5%".
_MARKUP = re.compile(r"(?:mark[\s-]?up|fx\s*margin|margin|هامش(?:\s*الصرف)?|نسبة\s*التحويل)\s*:?\s*(?P<pct>\d{1,2}(?:\.\d{1,4})?)\s?%", re.IGNORECASE)
# A fee printed inside the row: "intl txn fee SAR 1.20", "FX fee 1.20", "fee: 1.20 SAR", "رسوم 1.20 ر.س".
_FEE = re.compile(
    rf"(?:fee|fees|charges?|commission|رسوم|عمولة)\s*(?:of|:|=)?\s*"
    rf"(?:(?P<c1>{CURRENCY_ALT})\s?(?P<n1>{NUMBER})|(?P<n2>{NUMBER})(?:\s?(?P<c2>{CURRENCY_ALT}))?)(?!\s?%)",
    re.IGNORECASE,
)


def fold(text: str) -> str:
    """Compatibility forms and Arabic-Indic digits folded, so one pattern reads both scripts."""
    return unicodedata.normalize("NFKC", text or "").translate(_ARABIC_DIGITS)


def number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def currency_code(raw: str) -> str:
    return CURRENCY_CODE.get(raw.upper(), CURRENCY_CODE.get(raw, raw.upper()))


def money_match(match: re.Match[str]) -> tuple[Decimal, str] | None:
    """A matched "currency word and number" as an amount rounded to that currency's minor unit."""
    found = match.group("n1") or match.group("n2")
    word = match.group("c1") or match.group("c2")
    value = number(found or "")
    if value is None or not word:
        return None
    currency = currency_code(word)
    try:
        return quantize(value, currency), currency
    except MoneyError:
        return None


def amounts(text: str, skip: list[tuple[int, int]] | None = None) -> list[tuple[Decimal, str, int]]:
    """Every amount with a currency, in order, except those inside ``skip`` spans."""
    found = []
    for match in AMOUNT.finditer(text):
        if any(start <= match.start() < end for start, end in skip or []):
            continue
        read = money_match(match)
        if read is not None:
            found.append((read[0], read[1], match.start()))
    return found


def split_converted(found: list[tuple[Decimal, str, int]]) -> tuple[tuple[Decimal, str] | None, tuple[Decimal, str] | None]:
    """(spent, converted): the first amount, and the first later amount in another currency (the billed one)."""
    if not found:
        return None, None
    first = (found[0][0], found[0][1])
    for amount, currency, _ in found[1:]:
        if currency != first[1]:
            return first, (amount, currency)
    return first, None


@dataclass(frozen=True)
class FxText:
    """What a row's text says about a foreign currency payment. Amounts are positive."""

    original_amount: Decimal | None = None
    original_currency: str | None = None
    billed_amount: Decimal | None = None  # an amount in the account's currency printed in the text
    rate: Decimal | None = None
    rate_source: str | None = None  # "printed" or "implied" (billed / original, both printed)
    fee: Decimal | None = None
    fee_currency: str | None = None
    markup_percent: Decimal | None = None

    def empty(self) -> bool:
        return self.original_amount is None and self.rate is None and self.fee is None and self.markup_percent is None


def read_fx(text: str, account_currency: str) -> FxText:
    """The FX details a statement row's text prints. Nothing is guessed: only figures that are there."""
    body = fold(text)
    spans: list[tuple[int, int]] = []
    fee = fee_currency = None
    fee_match = _FEE.search(body)
    if fee_match is not None:
        word = fee_match.group("c1") or fee_match.group("c2")
        value = number(fee_match.group("n1") or fee_match.group("n2") or "")
        currency = currency_code(word) if word else account_currency
        if value is not None and value > 0:
            try:
                fee, fee_currency = quantize(value, currency), currency
                spans.append((fee_match.start(), fee_match.end()))
            except MoneyError:
                fee = fee_currency = None
    markup = None
    markup_match = _MARKUP.search(body)
    if markup_match is not None:
        markup = Decimal(markup_match.group("pct"))
        spans.append((markup_match.start(), markup_match.end()))
    rate = None
    rate_match = _RATE.search(body)
    if rate_match is not None and not any(s <= rate_match.start() < e for s, e in spans):
        value = number(rate_match.group("rate"))
        if value is not None and Decimal("0.00001") <= value <= Decimal(100000):
            rate = value
            spans.append((rate_match.start(), rate_match.end()))
    found = amounts(body, spans)
    foreign = next(((a, c) for a, c, _ in found if c != account_currency), None)
    billed = next((a for a, c, _ in found if c == account_currency), None)
    rate_source = "printed" if rate is not None else None
    if rate is None and foreign is not None and billed is not None and foreign[0] > 0:
        rate, rate_source = (billed / foreign[0]).quantize(Decimal("0.000001")), "implied"
    if foreign is None and rate is None:
        # A fee or markup with no foreign amount or rate is not an FX detail: a row that is only a fee
        # ("SERVICE CHARGE 15.00") is its own expense, recorded as such.
        return FxText()
    return FxText(
        original_amount=foreign[0] if foreign else None, original_currency=foreign[1] if foreign else None,
        billed_amount=billed if foreign else None, rate=rate if foreign or rate_source == "printed" else None,
        rate_source=rate_source if foreign or rate_source == "printed" else None, fee=fee, fee_currency=fee_currency,
        markup_percent=markup,
    )


@dataclass(frozen=True)
class FxCheck:
    """billed against original x rate (+ fee or markup): ``ties`` within two minor units, else ``differs``."""

    expected: Decimal
    difference: Decimal  # billed minus expected
    status: str  # "ties" or "differs"
    includes: str  # "rate", "rate_fee" or "rate_markup": which printed figures the expectation used


def check(billed: Decimal, original: Decimal | None, rate: Decimal | None, currency: str, *, fee: Decimal | None = None,
          fee_currency: str | None = None, markup_percent: Decimal | None = None) -> FxCheck | None:
    """Does the bank-billed amount agree with the original amount at the printed rate? ``None`` without both.

    The expectation uses the printed fee (in the account's currency) or markup when it brings the figures closer;
    the difference is what is left either way. Nothing here changes the amount that is recorded."""
    if original is None or rate is None or original <= 0:
        return None
    billed = abs(billed)
    base = original * rate
    options = [("rate", base)]
    if fee is not None and (fee_currency in (None, currency)):
        options.append(("rate_fee", base + abs(fee)))
    if markup_percent is not None:
        options.append(("rate_markup", base * (1 + markup_percent / 100)))
    includes, expected = min(options, key=lambda item: (abs(billed - item[1]), item[0]))
    try:
        expected = quantize(expected, currency)
    except MoneyError:
        return None
    difference = billed - expected
    tolerance = Decimal(2).scaleb(-minor_digits(currency))
    return FxCheck(expected=expected, difference=difference, status="ties" if abs(difference) <= tolerance else "differs", includes=includes)


__all__ = [
    "AMOUNT",
    "CURRENCY_ALT",
    "CURRENCY_CODE",
    "CURRENCY_WORDS",
    "NUMBER",
    "FxCheck",
    "FxText",
    "amounts",
    "check",
    "currency_code",
    "fold",
    "money_match",
    "number",
    "read_fx",
    "split_converted",
]
